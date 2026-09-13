from datetime import date
from pathlib import Path

import pytest

from renovai.ingestion.inflation_calc import (
    compound_inflation_factor,
    load_price_index,
)
from renovai.predictor.structural_cost import (
    CHAIN_RULES,
    CHIMNEY_TECHNICIAN_BASE,
    CHIMNEY_TECHNICIAN_PER_FLOOR,
    ELECTRICAL_STANDARDIZATION_MINIMUM,
    apply_height_surcharge,
    apply_infrastructure_minimums,
    ceiling_height_multiplier,
    chain_total_cost,
    chimney_technician_cost,
    compute_logistics_surcharge,
    detect_active_chains,
    elevator_surcharge,
)


class TestCeilingHeightMultiplier:
    def test_default_height(self):
        assert ceiling_height_multiplier(2.75) == 2.75

    def test_low_range(self):
        assert ceiling_height_multiplier(2.5) == 2.5
        assert ceiling_height_multiplier(2.3) == 2.5

    def test_standard_range(self):
        assert ceiling_height_multiplier(2.8) == 2.75
        assert ceiling_height_multiplier(3.0) == 2.75

    def test_intermediate_range(self):
        assert ceiling_height_multiplier(3.2) == 3.0

    def test_high_range(self):
        assert ceiling_height_multiplier(3.5) == 3.5
        assert ceiling_height_multiplier(3.8) == 3.5
        assert ceiling_height_multiplier(4.0) == 3.5

    def test_extreme_height(self):
        assert ceiling_height_multiplier(4.5) == 4.0
        assert ceiling_height_multiplier(5.0) == 4.0

    def test_zero_or_negative(self):
        assert ceiling_height_multiplier(0) == 1.0
        assert ceiling_height_multiplier(-1) == 1.0


class TestApplyHeightSurcharge:
    def test_standard_height_no_surcharge(self):
        adj, surcharge = apply_height_surcharge(2.75, 55, 200000)
        assert adj == 200000
        assert surcharge == 0.0

    def test_high_height_surcharge_applied(self):
        adj, surcharge = apply_height_surcharge(3.5, 55, 200000)
        # multiplier = 3.5, ref_mult = 2.75, ratio = 3.5/2.75 ≈ 1.2727
        expected_adj = int(200000 * (3.5 / 2.75))
        assert abs(adj - expected_adj) < 1
        assert surcharge > 0

    def test_extreme_height_surcharge(self):
        adj, surcharge = apply_height_surcharge(4.5, 55, 200000)
        # multiplier = 4.0, ref_mult = 2.75, ratio = 4.0/2.75 ≈ 1.4545
        expected_adj = int(200000 * (4.0 / 2.75))
        assert abs(adj - expected_adj) < 1

    def test_none_height_defaults(self):
        adj, surcharge = apply_height_surcharge(None, 55, 200000)
        assert adj == 200000
        assert surcharge == 0.0


class TestChainDetection:
    def test_pre_1970_with_door_scope_triggers_chain(self):
        scope = {"windows_doors": True, "flooring": False}
        chains = detect_active_chains(scope, "1965")
        assert len(chains) == 1
        assert chains[0]["id"] == "ajto_padlo_lanc"

    def test_pre_1970_with_floor_scope_triggers_chain(self):
        scope = {"windows_doors": False, "flooring": True}
        chains = detect_active_chains(scope, "1950")
        assert len(chains) == 1
        assert chains[0]["id"] == "ajto_padlo_lanc"

    def test_post_1970_triggers_misung_chain(self):
        scope = {"windows_doors": True, "flooring": True}
        chains = detect_active_chains(scope, "1985")
        assert len(chains) == 1
        assert chains[0]["id"] == "misung_subfloor_leveling"

    def test_panel_triggers_misung_chain_even_pre_1970(self):
        scope = {"windows_doors": True}
        chains = detect_active_chains(scope, "1960", building_type="panel")
        assert len(chains) == 1
        assert chains[0]["id"] == "misung_subfloor_leveling"

    def test_branch_a_bc_mutually_exclusive(self):
        # Pre-1970 tégla → full slag chain only (branch A)
        pre70 = detect_active_chains({"flooring": True}, "1960", building_type="tégla")
        assert [c["id"] for c in pre70] == ["ajto_padlo_lanc"]
        # Post-1970 tégla → misung only (branch B)
        post70 = detect_active_chains({"flooring": True}, "1985", building_type="tégla")
        assert [c["id"] for c in post70] == ["misung_subfloor_leveling"]
        # Panel → misung only (branch C)
        panel = detect_active_chains({"flooring": True}, "1975", building_type="panel")
        assert [c["id"] for c in panel] == ["misung_subfloor_leveling"]

    def test_misung_chain_cost_at_reference_area(self):
        chains = detect_active_chains({"flooring": True}, "1985")
        costs = chain_total_cost(chains, area_sqm=55)
        # doc 03 item 3 type 2 at 55 m² reference: base + per_sqm * 55
        assert costs["low"] == 1_250_000
        assert costs["high"] == 1_600_000
        assert costs["point"] == 1_425_000

    def test_no_matching_scope_no_chain(self):
        scope = {"demolition": True, "plumbing": True}
        chains = detect_active_chains(scope, "1950")
        assert len(chains) == 0

    def test_none_building_era_no_chain(self):
        scope = {"windows_doors": True, "flooring": True}
        chains = detect_active_chains(scope, None)
        assert len(chains) == 0

    def test_chain_cost_range(self):
        chains = detect_active_chains({"windows_doors": True}, "1950")
        costs = chain_total_cost(chains, area_sqm=55)
        # At 55 m² the area-scaled formula matches the doc 03 item 3 type 1 values
        assert costs["low"] == 2_000_000
        assert costs["high"] == 2_850_000
        assert costs["point"] == 2_425_000

    def test_chain_cost_scales_with_area(self):
        chains = detect_active_chains({"windows_doors": True}, "1950")
        costs_55 = chain_total_cost(chains, area_sqm=55)
        costs_110 = chain_total_cost(chains, area_sqm=110)
        # Larger area → higher cost
        assert costs_110["point"] > costs_55["point"]
        # 110 m² should be roughly 1.5–2× the 55 m² cost (base dominates)
        ratio = costs_110["point"] / costs_55["point"]
        assert 1.2 < ratio < 2.0

    def test_chain_cost_zero_area_uses_base(self):
        chains = detect_active_chains({"windows_doors": True}, "1950")
        costs = chain_total_cost(chains, area_sqm=0)
        # At 0 m² only the fixed base_cost remains
        assert costs["point"] == 1_215_000

    def test_chain_rule_structure(self):
        assert len(CHAIN_RULES) == 2
        for rule in CHAIN_RULES:
            assert "trigger" in rule
            assert "steps" in rule
            # Area-scaled fields
            assert "base_cost_point" in rule
            assert "per_sqm_cost_point" in rule
            assert "base_cost_low" in rule
            assert "per_sqm_cost_low" in rule
            assert "base_cost_high" in rule
            assert "per_sqm_cost_high" in rule


class TestInfrastructureMinimums:
    def test_no_override_when_estimate_above_minimum(self):
        low, mid, high = 5_000_000, 5_500_000, 6_000_000
        result_low, result_mid, result_high, logs = apply_infrastructure_minimums(
            low,
            mid,
            high,
            is_full_renovation=True,
            has_gas_heating=True,
        )
        # Both shares above minimums: elec = 660k > 300k, heating = 1.1M > 800k
        assert result_mid == mid
        assert len(logs) == 0

    def test_partial_renovation_no_mins(self):
        low, mid, high = 1_000_000, 1_200_000, 1_500_000
        result_low, result_mid, result_high, logs = apply_infrastructure_minimums(
            low,
            mid,
            high,
            is_full_renovation=False,
            has_gas_heating=True,
        )
        assert result_mid == mid
        assert len(logs) == 0

    def test_estimate_way_below_electrical_minimum(self):
        low, mid, high = 1_000_000, 1_200_000, 1_500_000
        result_low, result_mid, result_high, logs = apply_infrastructure_minimums(
            low,
            mid,
            high,
            is_full_renovation=True,
            has_gas_heating=False,
        )
        # Electrical: 12% of 1.2M = 144k < 300k → override
        expected_elec_override = ELECTRICAL_STANDARDIZATION_MINIMUM - int(
            1_200_000 * 0.12
        )
        assert result_mid == mid + expected_elec_override
        assert any("Electrical min override" in log for log in logs)

    def test_gas_minimum_added_when_gas_heating(self):
        low, mid, high = 2_000_000, 2_500_000, 3_000_000
        result_low, result_mid, result_high, logs = apply_infrastructure_minimums(
            low,
            mid,
            high,
            is_full_renovation=True,
            has_gas_heating=True,
        )
        has_gas_log = any("Gas/heating min override" in log for log in logs)
        # Heating share: 20% of 2.5M = 500k < 800k → override
        has_elec_log = any("Electrical min override" in log for log in logs)
        # Electrical share: 12% of 2.5M = 300k → equals minimum → no override
        assert has_gas_log
        assert not has_elec_log


class TestLogisticsSurcharge:
    def test_standard_15_percent(self):
        surcharge = compute_logistics_surcharge(1_000_000)
        assert surcharge == 150_000

    def test_zero_labor(self):
        surcharge = compute_logistics_surcharge(0)
        assert surcharge == 0.0


class TestElevatorSurcharge:
    def test_no_elevator_on_high_floor(self):
        result = elevator_surcharge("none", 5)
        assert result > 0
        assert result == 4 * 50_000  # 4 floors above ground × 50k

    def test_no_elevator_ground_floor(self):
        result = elevator_surcharge("none", 1)
        assert result == 0  # ground floor, no surcharge

    def test_small_elevator_no_surcharge(self):
        result = elevator_surcharge("small", 5)
        assert result == 0

    def test_large_elevator_no_surcharge(self):
        result = elevator_surcharge("large", 5)
        assert result == 0

    def test_unknown_elevator_no_surcharge(self):
        result = elevator_surcharge(None, 3)
        assert result == 0

    def test_none_floor_default(self):
        result = elevator_surcharge("none", None)
        # floor_number = None → treated as 1 → only 0 floors above ground
        assert result == 0


class TestChimneyTechnician:
    def test_ground_floor(self):
        cost = chimney_technician_cost(1)
        assert cost == 80_000

    def test_third_floor(self):
        cost = chimney_technician_cost(3)
        assert cost == 80_000 + 2 * 20_000  # 120k

    def test_no_floor_number_defaults_to_one(self):
        cost = chimney_technician_cost(None)
        assert cost == 80_000  # defaults to floor 1


DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
SOURCE_DATE = date(2025, 1, 1)  # 2025 expert-reference date


def _blended_factor(price_index, target_date: date) -> float:
    """Expected manual 55/45 labor/materials blend for structural add-ons."""
    f_lab = compound_inflation_factor(2025, target_date, price_index, "labor")
    f_mat = compound_inflation_factor(2025, target_date, price_index, "materials")
    return 0.55 * f_lab + 0.45 * f_mat


@pytest.fixture(scope="module")
def price_index():
    mat_path = DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv"
    lab_path = DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv"
    assert mat_path.exists() and lab_path.exists(), (
        "Real CPI files required for dated structural-add-on inflation tests"
    )
    return load_price_index(mat_path, lab_path)


@pytest.mark.requires_local_corpus
class TestDatedStructuralInflation:
    """Item F: structural add-ons get their own 2025 -> today inflation."""

    def test_infra_minimum_as_of_source_date_matches_undated(self, price_index):
        low, mid, high = 100_000, 120_000, 150_000
        res_low, res_mid, res_high, logs = apply_infrastructure_minimums(
            low,
            mid,
            high,
            is_full_renovation=True,
            has_gas_heating=False,
            target_date=SOURCE_DATE,
            price_index=price_index,
        )
        expected_elec_delta = ELECTRICAL_STANDARDIZATION_MINIMUM - int(mid * 0.12)
        assert res_mid == mid + expected_elec_delta
        assert any("Electrical min override" in log for log in logs)

    def test_infra_minimum_inflated_today_vs_source(self, price_index):
        low, mid, high = 100_000, 120_000, 150_000
        _, today_mid, _, _ = apply_infrastructure_minimums(
            low,
            mid,
            high,
            is_full_renovation=True,
            has_gas_heating=False,
            target_date=date.today(),
            price_index=price_index,
        )
        _, source_mid, _, _ = apply_infrastructure_minimums(
            low,
            mid,
            high,
            is_full_renovation=True,
            has_gas_heating=False,
            target_date=SOURCE_DATE,
            price_index=price_index,
        )
        expected_factor = _blended_factor(price_index, date.today())
        assert expected_factor > 1.0
        # delta = inflated minimum - existing electrical share (12% of mid)
        today_delta = today_mid - mid
        source_delta = source_mid - mid
        electrical_share = int(mid * 0.12)
        assert (
            today_delta
            == int(round(ELECTRICAL_STANDARDIZATION_MINIMUM * expected_factor))
            - electrical_share
        )
        assert source_delta == ELECTRICAL_STANDARDIZATION_MINIMUM - electrical_share
        assert today_mid > source_mid

    def test_chain_cost_inflated_with_hand_computed_factor(self, price_index):
        chains = detect_active_chains({"windows_doors": True}, "1950")
        source_costs = chain_total_cost(
            chains,
            area_sqm=55,
            target_date=SOURCE_DATE,
            price_index=price_index,
        )
        today_costs = chain_total_cost(
            chains,
            area_sqm=55,
            target_date=date.today(),
            price_index=price_index,
        )
        expected_factor = _blended_factor(price_index, date.today())
        assert expected_factor > 1.0
        assert source_costs["point"] == 2_425_000  # undated at source date
        assert today_costs["point"] == int(round(2_425_000 * expected_factor))
        assert today_costs["point"] > source_costs["point"]

    def test_chimney_inflated_with_hand_computed_factor(self, price_index):
        expected_factor = _blended_factor(price_index, date.today())
        assert expected_factor > 1.0
        today_cost = chimney_technician_cost(
            3, target_date=date.today(), price_index=price_index
        )
        source_cost = chimney_technician_cost(
            3, target_date=SOURCE_DATE, price_index=price_index
        )
        expected = int(
            round(
                (CHIMNEY_TECHNICIAN_BASE + 2 * CHIMNEY_TECHNICIAN_PER_FLOOR)
                * expected_factor
            )
        )
        assert source_cost == CHIMNEY_TECHNICIAN_BASE + 2 * CHIMNEY_TECHNICIAN_PER_FLOOR
        assert today_cost == expected
        assert today_cost > source_cost

    def test_elevator_surcharge_not_inflated_undated_placeholder(self, price_index):
        # ELEVATOR_SURCHARGE_PER_FLOOR is an undated placeholder constant;
        # it must remain uninflated (no target_date/price_index consumption).
        assert elevator_surcharge("none", 5) == 4 * 50_000
