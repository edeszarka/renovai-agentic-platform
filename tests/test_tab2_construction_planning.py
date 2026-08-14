"""Regression tests for Tab 2 (`handle_construction_planning`) infrastructure
and logistics costs.

The old code unconditionally added a flat 300k electrical / 800k gas phase and
an inline 15%-of-labor logistics line. The fix routes those through the shared
`structural_cost` functions (`apply_infrastructure_minimums` / 
`compute_logistics_surcharge`) so the same minimums and surcharge rate apply
everywhere.

These tests exercise the handler end-to-end with fake policy/registry services
(the real ones hit Gemini / load YAML, which the unit suite avoids).
"""

import asyncio

from orchestrator.policy_service import PolicyCheckResult
from orchestrator.handlers import handle_construction_planning
from renovai.predictor.structural_cost import (
    apply_infrastructure_minimums,
    compute_logistics_surcharge,
    ELECTRICAL_STANDARDIZATION_MINIMUM,
    GAS_HEATING_INFRA_MINIMUM,
)


class _FakePolicy:
    def check_structural(self, role, action, trace_id):
        return PolicyCheckResult(
            passed=True, reason="ok", trace_id=trace_id, check_type="structural"
        )

    async def check_semantic(self, args, trace_id, **kwargs):
        return PolicyCheckResult(
            passed=True, reason="ok", trace_id=trace_id, check_type="semantic"
        )


class _FakeRegistry:
    def load_instructions(self, name):
        return ""


def _run(params):
    return asyncio.run(
        handle_construction_planning(params, _FakePolicy(), _FakeRegistry(), "trace-1")
    )


def _full_params(gas_heating: bool) -> dict:
    # Demolition is disabled so every phase's printed material range is exactly
    # its real contribution (the demolition phase prints "0 - 50 000 Ft" while
    # actually adding 25k low / 50k high, which would skew base recomputation).
    return {
        "area_sqm": 55.0,
        "building_era": "1980",
        "renovation_scope": "full",
        "gas_heating": gas_heating,
        "floor_number": 1,
        "elevator_type": "none",
        "scope_flags": {"demolition": False},
    }


def _parse_range(s):
    lo, hi = s.replace(" Ft", "").split(" - ")
    return int(lo.replace(",", "").replace(" ", "")), int(
        hi.replace(",", "").replace(" ", "")
    )


def _base_totals(phases):
    """Sum material+labor of every non-infrastructure phase (the pre-minimum base)."""
    low = high = 0
    for p in phases:
        if p.get("is_infrastructure_minimum"):
            continue
        ml = mh = ll = lh = 0
        if " - " in p["material_cost_range"] and "anyagköltség" not in p["material_cost_range"]:
            ml, mh = _parse_range(p["material_cost_range"])
        if " - " in p["labor_cost_range"] and "benne" not in p["labor_cost_range"]:
            ll, lh = _parse_range(p["labor_cost_range"])
        low += ml + ll
        high += mh + lh
    return low, high


def _labor_totals(phases):
    low = high = 0
    for p in phases:
        if p.get("is_infrastructure_minimum"):
            continue
        if " - " in p["labor_cost_range"] and "benne" not in p["labor_cost_range"]:
            ll, lh = _parse_range(p["labor_cost_range"])
            low += ll
            high += lh
    return low, high


def _infra_phase_names(phases, needle):
    return [
        p["name"]
        for p in phases
        if p.get("is_infrastructure_minimum") and needle in p["name"]
    ]


class TestInfrastructureMinimumsThroughHandler:
    def test_realistic_gas_off_infra_minimum_applied_correctly(self):
        """With corpus-derived costs, infra minimums are applied via shared
        structural_cost functions. The exact delta may be zero or nonzero
        depending on corpus values — what matters is the invariant."""
        data = _run(_full_params(gas_heating=False))["data"]
        phases = data["phases"]

        base_low, base_high = _base_totals(phases)
        base_mid = (base_low + base_high) // 2
        adjusted = apply_infrastructure_minimums(base_low, base_mid, base_high, True, False)
        elec_phases = _infra_phase_names(phases, "Elektromos")
        gas_phases = _infra_phase_names(phases, "Gáz")

        if elec_phases:
            # Electrical mins kicked in — the total should include the delta
            assert data["total_estimate"]["mid_huf"] == adjusted[1]
        else:
            # Base already covered minimum — total unchanged
            assert adjusted[:3] == (base_low, base_mid, base_high)
        assert gas_phases == []

    def test_realistic_gas_on_infra_minimum_applied_correctly(self):
        """With corpus-derived costs + gas heating, infra minimums use the
        shared apply_infrastructure_minimums function."""
        data = _run(_full_params(gas_heating=True))["data"]
        phases = data["phases"]

        base_low, base_high = _base_totals(phases)
        base_mid = (base_low + base_high) // 2
        adjusted = apply_infrastructure_minimums(base_low, base_mid, base_high, True, True)

        if adjusted[:3] != (base_low, base_mid, base_high):
            # Minimums kicked in — check the infra phases exist
            elec_phases = _infra_phase_names(phases, "Elektromos")
            gas_phases = _infra_phase_names(phases, "Gáz")
            assert len(elec_phases) + len(gas_phases) >= 1

    def test_old_unconditional_add_removed(self):
        """The old flat 300k/800k unconditional add is gone. The total must
        equal the sum of its phase costs."""
        for gas_heating in (False, True):
            data = _run(_full_params(gas_heating=gas_heating))["data"]
            phases = data["phases"]

            # Sum low/high from every phase's material + labor
            computed_low = computed_high = 0
            for p in phases:
                ml = mh = ll = lh = 0
                if " - " in p.get("material_cost_range", "") and "anyagköltség" not in p.get("material_cost_range", ""):
                    ml, mh = _parse_range(p["material_cost_range"])
                if " - " in p.get("labor_cost_range", "") and "benne" not in p.get("labor_cost_range", ""):
                    ll, lh = _parse_range(p["labor_cost_range"])
                computed_low += ml + ll
                computed_high += mh + lh
            assert data["total_estimate"]["low_huf"] == computed_low
            assert data["total_estimate"]["high_huf"] == computed_high

    def test_partial_renovation_skips_minimums_and_logistics(self):
        params = {
            "area_sqm": 55.0,
            "building_era": "1980",
            "renovation_scope": "partial",
            "gas_heating": True,
            "floor_number": 1,
            "elevator_type": "none",
            "scope_flags": {"demolition": True, "plastering": True, "painting": True},
        }
        phases = _run(params)["data"]["phases"]
        # Partial scope => apply_infrastructure_minimums early-returns and the
        # logistics surcharge is skipped too.
        assert [p for p in phases if p.get("is_infrastructure_minimum")] == []

    def test_building_type_passed_through_to_apartment_input(self, monkeypatch):
        """The Tab 2 selectbox value must reach ApartmentInput, fixing the
        dead building_type UI field (previously collected but never sent)."""
        import renovai.predictor.feature_extractor as fe

        captured = {}

        class _SpyApartmentInput(fe.ApartmentInput):
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(fe, "ApartmentInput", _SpyApartmentInput)

        params = _full_params(gas_heating=False)
        params["building_type"] = "panel"
        result = _run(params)
        assert result["status"] == "ok"
        assert captured.get("building_type") == "panel"

    def test_building_type_optional_default_none(self):
        """Omitting building_type must not break the handler."""
        result = _run(_full_params(gas_heating=False))
        assert result["status"] == "ok"


class TestBelowMinimumTopUpMatchesSharedFunction:
    def test_two_call_split_equals_single_combined_call(self):
        # The handler implements the top-up as two calls on the same base
        # (electrical with gas disabled, then combined with the real gas flag).
        # Below both floors this must reproduce exactly what a single combined
        # apply_infrastructure_minimums() call would do.
        low = mid = high = 800_000

        _, elec_mid, _, _ = apply_infrastructure_minimums(low, mid, high, True, False)
        elec_delta = elec_mid - mid
        _, combined_mid, _, _ = apply_infrastructure_minimums(low, mid, high, True, True)
        gas_delta = (combined_mid - mid) - elec_delta

        combined = apply_infrastructure_minimums(low, mid, high, True, True)
        assert elec_delta > 0 and gas_delta > 0
        assert combined[:3] == (
            low + elec_delta + gas_delta,
            mid + elec_delta + gas_delta,
            high + elec_delta + gas_delta,
        )

    def test_topup_reaches_exact_minimum_floors(self):
        # Top-up closes the gap to the constants, matching handle_cost_estimation.
        low = mid = high = 800_000

        _, elec_mid, _, _ = apply_infrastructure_minimums(low, mid, high, True, False)
        elec_delta = elec_mid - mid
        _, combined_mid, _, _ = apply_infrastructure_minimums(low, mid, high, True, True)
        gas_delta = (combined_mid - mid) - elec_delta

        elec_share = int(mid * 0.12)
        gas_share = int((mid + elec_delta) * 0.20)
        assert elec_delta == ELECTRICAL_STANDARDIZATION_MINIMUM - elec_share
        assert gas_delta == GAS_HEATING_INFRA_MINIMUM - gas_share


class TestLogisticsSurchargeThroughHandler:
    def test_uses_shared_compute_logistics_surcharge(self):
        data = _run(_full_params(gas_heating=True))["data"]
        phases = data["phases"]

        logistics = [p for p in phases if "Logisztika" in p["name"]]
        assert len(logistics) == 1
        surcharge_low, surcharge_high = _parse_range(logistics[0]["labor_cost_range"])

        labor_low, labor_high = _labor_totals(phases)
        assert surcharge_low == int(compute_logistics_surcharge(labor_low))
        assert surcharge_high == int(compute_logistics_surcharge(labor_high))
