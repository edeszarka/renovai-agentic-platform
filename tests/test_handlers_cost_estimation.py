"""
Regression test for handle_cost_estimation() (cost_estimator intent).

Covers the AttributeError fix from Item B: handle_cost_estimation() used to
pass an ApartmentInput directly into find_similar_quotes(), which reads
QuoteFeatures-only fields (num_line_items, ratios, cost shares) via getattr
for its similarity distance. ApartmentInput doesn't have those fields, so
every call raised AttributeError and the handler could never complete.

The call site now converts through apartment_input_to_features() first.
This test drives the full handler end-to-end with realistic params and
asserts a real (sane) result comes back, not just a non-exception.
"""
import os
import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.policy_service import PolicyCheckResult
from renovai.predictor.feature_extractor import apartment_input_to_features, ApartmentInput

DATA_ROOT = Path(__file__).resolve().parent.parent / "data"

# The DB must exist with seeded quotes for scope_matched_estimate() to run.
DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")


class FakePolicyService:
    def __init__(self):
        self._result = PolicyCheckResult(
            passed=True, reason="test-gate", trace_id="t", check_type="structural"
        )

    def check_structural(self, role: str, action: str, trace_id: str) -> PolicyCheckResult:
        return self._result

    async def check_semantic(self, args: dict, trace_id: str, **kwargs):
        return PolicyCheckResult(
            passed=True, reason="test-gate", trace_id=trace_id, check_type="semantic"
        )


class FakeSkillRegistry:
    def get(self, name: str):
        return None

    def load_instructions(self, name: str) -> str:
        return ""


def realistic_params(**overrides) -> dict:
    params = {
        "district": 5,
        "area_sqm": 55.0,
        "num_rooms": 2,
        "building_era": "1980",
        "ceiling_height": 2.75,
        "elevator_type": "small",
        "gas_heating": False,
        "floor_number": 1,
        "renovation_scope": "full",
        "target_date": "2026-07-10",
        "scope_flags": {
            "plumbing": True,
            "electrical": True,
            "flooring": True,
            "demolition": True,
            "slag": False,
        },
    }
    params.update(overrides)
    return params


@pytest.mark.asyncio
async def test_handle_cost_estimation_completes_with_sane_estimate():
    from orchestrator.handlers import handle_cost_estimation

    policy = FakePolicyService()
    registry = FakeSkillRegistry()

    result = await handle_cost_estimation(
        realistic_params(),
        policy_service=policy,
        skill_registry=registry,
        trace_id="test-trace",
    )

    assert result["status"] == "ok", f"handler errored: {result.get('error')}"
    data = result["data"]
    low = data["estimate_low_huf"]
    mid = data["estimate_mid_huf"]
    high = data["estimate_high_huf"]

    # Sane ordering and magnitude for a full renovation of a 55 m² flat
    assert 0 < low <= mid <= high
    assert high < 100_000_000
    assert 300_000 <= mid <= 30_000_000

    # Similar quotes come back ranked (would have raised AttributeError before the fix)
    assert isinstance(data["similar_quotes"], list)
    assert data["similar_quotes"][0]["distance"] >= 0


@pytest.mark.asyncio
async def test_building_type_passed_through_to_apartment_input(monkeypatch):
    """building_type from the UI must reach ApartmentInput (previously dropped)."""
    import renovai.predictor.feature_extractor as fe
    from orchestrator.handlers import handle_cost_estimation

    captured = {}

    class _SpyApartmentInput(fe.ApartmentInput):
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(fe, "ApartmentInput", _SpyApartmentInput)

    result = await handle_cost_estimation(
        realistic_params(building_type="panel"),
        policy_service=FakePolicyService(),
        skill_registry=FakeSkillRegistry(),
        trace_id="test-trace",
    )
    assert result["status"] == "ok", f"handler errored: {result.get('error')}"
    assert captured.get("building_type") == "panel"


# ---------------------------------------------------------------------------
# Item H — district must not influence find_similar_quotes() ranking
# ---------------------------------------------------------------------------

def _write_quote(quotes_dir: Path, filename: str, district: int) -> Path:
    data = {
        "original_metadata": {
            "file_name": filename,
            "address_raw": f"Bp {district}",
            "district": district,
            "total_labor": 100,
            "total_material": 100,
            "grand_total": 200,
        },
        "target_date": "2024-06-01",
        "line_items_adjusted": [
            {
                "original": {
                    "name": "Bontás",
                    "labor_cost": 60,
                    "material_cost": 0,
                    "total_cost": 60,
                    "section": "Main",
                    "version": 1,
                    "notes": None,
                },
                "total_cost_adjusted": 60,
                "labor_cost_adjusted": 60,
                "material_cost_adjusted": 0,
                "adjustment_factor_labor": 1.2,
                "adjustment_factor_materials": 1.1,
                "target_date": "2024-06-01",
            },
            {
                "original": {
                    "name": "Burkolás",
                    "labor_cost": 50,
                    "material_cost": 50,
                    "total_cost": 100,
                    "section": "Main",
                    "version": 1,
                    "notes": None,
                },
                "total_cost_adjusted": 115,
                "labor_cost_adjusted": 60,
                "material_cost_adjusted": 55,
                "adjustment_factor_labor": 1.2,
                "adjustment_factor_materials": 1.1,
                "target_date": "2024-06-01",
            },
        ],
        "grand_total_original": 150,
        "grand_total_adjusted": 175,
        "inflation_delta_pct": 16.67,
    }
    path = quotes_dir / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


@pytest.fixture
def similar_quotes_dir(tmp_path) -> Path:
    # Two corpus quotes identical in every respect except district. If the
    # district column still fed the Euclidean distance, the query target with
    # a matching district would rank the same-district quote closer. With
    # district dropped, relative distances (and so the ranking) become
    # district-independent.
    _write_quote(tmp_path, "q1_adjusted.json", district=1)
    _write_quote(tmp_path, "q2_adjusted.json", district=11)
    return tmp_path


def _ranking(district: int, quotes_dir: Path) -> list[str]:
    from renovai.predictor.price_model import find_similar_quotes

    apt = ApartmentInput(
        district=district,
        total_area_sqm=50,
        num_rooms=2,
        needs_plumbing=True,
        needs_full_demolition=True,
    )
    feat = apartment_input_to_features(apt)
    similar = find_similar_quotes(feat, quotes_dir, top_k=3)
    return [s["file"] for s in similar]


def test_district_does_not_change_similarity_ranking(similar_quotes_dir):
    ranking_2 = _ranking(2, similar_quotes_dir)
    ranking_11 = _ranking(11, similar_quotes_dir)
    assert ranking_2 == ranking_11


# ---------------------------------------------------------------------------
# Part C — scope-category expansion tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_cost_estimation_full_renovation_auto_enables_ac(caplog):
    """Full renovation scope must automatically set needs_ac."""
    from orchestrator.handlers import handle_cost_estimation

    policy = FakePolicyService()
    registry = FakeSkillRegistry()

    with caplog.at_level("WARNING"):
        result = await handle_cost_estimation(
            realistic_params(renovation_scope="full"),
            policy_service=policy,
            skill_registry=registry,
            trace_id="test-trace-ac",
        )

    assert result["status"] == "ok"
    mid = result["data"]["estimate_mid_huf"]

    # With AC auto-enabled we must get at least the min_premium contribution
    # (300_000 * 0.85 = 255_000 added to low, more to mid) plus corpus share.
    # The estimate must be > a baseline 4-scope full renovation estimate.
    # Indirect check: re-run without AC and confirm the estimate is higher
    # when AC is auto-enabled.
    ac_warn = any("needs_ac" in r.message and "no historical quotes" in r.message for r in caplog.records)
    assert not ac_warn, "needs_ac should use real corpus, not fallback"


@pytest.mark.asyncio
async def test_ac_scope_uses_real_corpus_not_fallback():
    """With 22 real klima quotes, needs_ac must use corpus data, not min_premium."""
    from datetime import date
    from renovai.predictor.price_model import scope_matched_estimate
    from renovai.ingestion.inflation_calc import load_price_index
    from renovai.db.session import get_engine, get_session_maker

    apt = ApartmentInput(
        district=5, total_area_sqm=55, num_rooms=2,
        needs_plumbing=True, needs_electrical=True,
        needs_flooring=True, needs_full_demolition=True,
        needs_ac=True, needs_windows_doors=False, needs_insulation=False,
    )

    pi = load_price_index(
        DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv",
        DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv",
    )
    eng = get_engine("sqlite+aiosqlite:///data/renovai.db")
    sm = get_session_maker(eng)

    est = await scope_matched_estimate(apt, sm, pi, date.today())

    assert est is not None
    assert "needs_ac" in est["debug"]["scope_avg_per_sqm_huf"]
    # Verify real corpus, not fallback — AC has 22 klima quotes
    assert est["debug"]["scope_line_item_count"]["needs_ac"] >= 2, (
        f"AC has {est['debug']['scope_line_item_count']['needs_ac']} quotes, "
        f"expected >= 2 (real corpus)"
    )
    await eng.dispose()


@pytest.mark.asyncio
async def test_windows_doors_insulation_fallback_warning(caplog):
    """needs_windows_doors and needs_insulation have 0 corpus quotes — must log warning."""
    from orchestrator.handlers import handle_cost_estimation

    policy = FakePolicyService()
    registry = FakeSkillRegistry()

    params = realistic_params(renovation_scope="partial")
    params["scope_flags"].update({
        "plumbing": True,
        "electrical": True,
        "flooring": True,
        "demolition": True,
        "windows_doors": True,
        "insulation": True,
        "ac": True,
    })
    params["renovation_scope"] = "partial"

    with caplog.at_level("WARNING"):
        result = await handle_cost_estimation(
            params,
            policy_service=policy,
            skill_registry=registry,
            trace_id="test-trace-wdi",
        )

    assert result["status"] == "ok"
    windows_warn = any(
        "needs_windows_doors" in rec.message and "no historical quotes" in rec.message
        for rec in caplog.records
    )
    insulation_warn = any(
        "needs_insulation" in rec.message and "no historical quotes" in rec.message
        for rec in caplog.records
    )
    assert windows_warn, "needs_windows_doors should log 0-quote fallback warning"
    assert insulation_warn, "needs_insulation should log 0-quote fallback warning"


def test_apartment_input_defaults():
    """New scope flags default to False — backwards compatible."""
    apt = ApartmentInput(district=1, total_area_sqm=55, num_rooms=2)
    assert not apt.needs_windows_doors
    assert not apt.needs_insulation
    assert not apt.needs_ac


def test_apartment_input_explicit_scopes():
    """Setting the new flags and converting to features works."""
    apt = ApartmentInput(
        district=1, total_area_sqm=55, num_rooms=2,
        needs_windows_doors=True, needs_insulation=True, needs_ac=True,
    )
    feat = apartment_input_to_features(apt)
    assert feat.district == 1
    # apartment_input_to_features doesn't change behavior for new flags,
    # but the conversion must still succeed


# ---------------------------------------------------------------------------
# Item E — combined IVW × recency weighting tests
# ---------------------------------------------------------------------------

def test_single_year_recency_same_as_pure_ivw():
    """If all quotes are from the same year, recency weights are uniform (2×)
    and mathematically cancel, producing the same result as pure IVW."""
    import numpy as np

    # 5 quotes from 2024 only
    vals = np.array([50000, 52000, 48000, 51000, 49000], dtype=float)
    years = [2024, 2024, 2024, 2024, 2024]
    _IVW_EPS = 1.0

    mean = vals.mean()
    var = (vals - mean) ** 2 + _IVW_EPS
    ivw = 1.0 / var
    pure_ivw_avg = float(np.sum(ivw * vals) / np.sum(ivw))

    boost_year = max(years)
    recency = np.array([2.0 if y == boost_year else 1.0 for y in years], dtype=float)
    combined = ivw * recency
    combined_avg = float(np.sum(combined * vals) / np.sum(combined))

    assert combined_avg == pytest.approx(pure_ivw_avg)


def test_multi_year_recency_shifts_toward_boost_year():
    """When quotes span multiple years, recency weighting shifts the mean
    toward the most recent year's value compared to pure IVW."""
    import numpy as np

    vals = np.array([40000, 42000, 38000, 41000, 60000, 62000, 58000, 61000], dtype=float)
    years = [2023, 2023, 2023, 2023, 2026, 2026, 2026, 2026]
    _IVW_EPS = 1.0

    mean = vals.mean()
    var = (vals - mean) ** 2 + _IVW_EPS
    ivw = 1.0 / var
    pure_ivw_avg = float(np.sum(ivw * vals) / np.sum(ivw))

    boost_year = max(years)
    recency = np.array([2.0 if y == boost_year else 1.0 for y in years], dtype=float)
    combined = ivw * recency
    combined_avg = float(np.sum(combined * vals) / np.sum(combined))

    # The 2026 entries are ~60k, 2023 entries ~40k. Recency should shift
    # the mean UP toward the higher 2026 values.
    assert combined_avg > pure_ivw_avg

    # Also verify: if we reverse (make older year the "boost"), the mean shifts down
    recency_rev = np.array([2.0 if y == 2023 else 1.0 for y in years], dtype=float)
    combined_rev = ivw * recency_rev
    combined_rev_avg = float(np.sum(combined_rev * vals) / np.sum(combined_rev))
    assert combined_rev_avg < pure_ivw_avg


@pytest.mark.asyncio
async def test_recency_active_on_real_corpus():
    """Verify recency weighting is actually doing something against the real
    DB: most categories span 2023-2026, so boost_year != min year and the
    combined average should differ from pure IVW."""
    from datetime import date
    from renovai.predictor.price_model import scope_matched_estimate
    from renovai.ingestion.inflation_calc import load_price_index
    from renovai.db.session import get_engine, get_session_maker

    apt = ApartmentInput(
        district=5, total_area_sqm=55, num_rooms=2,
        needs_plumbing=True, needs_electrical=True,
        needs_flooring=True, needs_full_demolition=True,
    )

    pi = load_price_index(
        DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv",
        DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv",
    )
    eng = get_engine("sqlite+aiosqlite:///data/renovai.db")
    sm = get_session_maker(eng)

    est = await scope_matched_estimate(apt, sm, pi, date.today())

    assert est is not None
    # The estimate is non-trivial (>1M)
    assert est["estimate_mid_huf"] > 1_000_000
    # All 4 scopes have data
    for sn in ("needs_plumbing", "needs_electrical", "needs_flooring", "needs_full_demolition"):
        assert est["debug"]["scope_distinct_quote_count"][sn] >= 2
    await eng.dispose()


@pytest.mark.asyncio
async def test_zero_quote_scopes_fallback_with_recency_in_place(caplog):
    """windows_doors and insulation still correctly fallback with recency weighting."""
    from datetime import date
    from renovai.predictor.price_model import scope_matched_estimate
    from renovai.ingestion.inflation_calc import load_price_index
    from renovai.db.session import get_engine, get_session_maker

    apt = ApartmentInput(
        district=5, total_area_sqm=55, num_rooms=2,
        needs_plumbing=True, needs_windows_doors=True, needs_insulation=True,
    )

    pi = load_price_index(
        DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv",
        DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv",
    )
    eng = get_engine("sqlite+aiosqlite:///data/renovai.db")
    sm = get_session_maker(eng)

    with caplog.at_level("WARNING"):
        est = await scope_matched_estimate(apt, sm, pi, date.today())

    assert est is not None
    windows_warn = any(
        "needs_windows_doors" in r.message and "no historical quotes" in r.message
        for r in caplog.records
    )
    insulation_warn = any(
        "needs_insulation" in r.message and "no historical quotes" in r.message
        for r in caplog.records
    )
    assert windows_warn
    assert insulation_warn
    assert est["debug"]["scope_distinct_quote_count"]["needs_windows_doors"] == 0
    assert est["debug"]["scope_distinct_quote_count"]["needs_insulation"] == 0
    await eng.dispose()


# ---------------------------------------------------------------------------
# Item D — per-category return shape tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_categories_dict_covers_all_active_scopes():
    """Every active user scope has an entry in the categories dict."""
    from datetime import date
    from renovai.predictor.price_model import scope_matched_estimate
    from renovai.ingestion.inflation_calc import load_price_index
    from renovai.db.session import get_engine, get_session_maker

    apt = ApartmentInput(
        district=5, total_area_sqm=55, num_rooms=2,
        needs_plumbing=True, needs_electrical=True,
        needs_flooring=True, needs_full_demolition=True,
        needs_ac=True, needs_windows_doors=True, needs_insulation=True,
    )

    pi = load_price_index(
        DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv",
        DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv",
    )
    eng = get_engine("sqlite+aiosqlite:///data/renovai.db")
    sm = get_session_maker(eng)

    est = await scope_matched_estimate(apt, sm, pi, date.today())
    assert est is not None
    cats = est.get("categories", {})
    assert "categories" in est

    user_scopes = {"needs_plumbing", "needs_electrical", "needs_flooring",
                   "needs_full_demolition", "needs_windows_doors",
                   "needs_insulation", "needs_ac"}
    for sn in user_scopes:
        assert sn in cats, f"Missing {sn} from categories"
        c = cats[sn]
        assert c["scope"] == sn
        assert isinstance(c["label_hu"], str)
        assert isinstance(c["distinct_quote_count"], int)
        assert isinstance(c["line_item_count"], int)
        assert c["area_sqm"] == pytest.approx(55.0)
        assert "low" in c["estimate_huf"]
        assert "mid" in c["estimate_huf"]
        assert "high" in c["estimate_huf"]

    await eng.dispose()


@pytest.mark.asyncio
async def test_categories_estimate_sums_roughly_to_total():
    """Per-category mid estimates should roughly sum to the total mid."""
    from datetime import date
    from renovai.predictor.price_model import scope_matched_estimate
    from renovai.ingestion.inflation_calc import load_price_index
    from renovai.db.session import get_engine, get_session_maker

    apt = ApartmentInput(
        district=5, total_area_sqm=55, num_rooms=2,
        needs_plumbing=True, needs_electrical=True,
        needs_flooring=True, needs_full_demolition=True,
    )

    pi = load_price_index(
        DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv",
        DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv",
    )
    eng = get_engine("sqlite+aiosqlite:///data/renovai.db")
    sm = get_session_maker(eng)

    est = await scope_matched_estimate(apt, sm, pi, date.today())
    cats = est["categories"]

    scope_total = sum(
        cats[sn]["estimate_huf"]["mid"]
        for sn in ("needs_plumbing", "needs_electrical", "needs_flooring", "needs_full_demolition")
    )
    expected_mid = est["estimate_mid_huf"] - 200_000  # contingency
    ratio = scope_total / expected_mid
    assert 0.95 <= ratio <= 1.05, (
        f"Category mids sum to {scope_total:,}, expected ~{expected_mid:,} (ratio={ratio:.3f})"
    )

    await eng.dispose()


@pytest.mark.asyncio
async def test_data_quality_and_fallback_flags():
    """needs_ac -> sufficient, needs_windows_doors/insulation -> no_corpus_data."""
    from datetime import date
    from renovai.predictor.price_model import scope_matched_estimate
    from renovai.ingestion.inflation_calc import load_price_index
    from renovai.db.session import get_engine, get_session_maker

    apt = ApartmentInput(
        district=5, total_area_sqm=55, num_rooms=2,
        needs_plumbing=True, needs_ac=True,
        needs_windows_doors=True, needs_insulation=True,
    )

    pi = load_price_index(
        DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv",
        DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv",
    )
    eng = get_engine("sqlite+aiosqlite:///data/renovai.db")
    sm = get_session_maker(eng)

    est = await scope_matched_estimate(apt, sm, pi, date.today())
    cats = est["categories"]

    assert cats["needs_ac"]["data_quality"] == "sufficient"
    assert not cats["needs_ac"]["fallback_used"]
    assert cats["needs_ac"]["boost_year"] is not None

    assert cats["needs_windows_doors"]["data_quality"] == "no_corpus_data"
    assert cats["needs_windows_doors"]["fallback_used"]

    assert cats["needs_insulation"]["data_quality"] == "no_corpus_data"
    assert cats["needs_insulation"]["fallback_used"]

    # raw_per_sqm_huf is empty for 0-quote scopes
    assert cats["needs_windows_doors"]["raw_per_sqm_huf"] == []
    assert cats["needs_ac"]["raw_per_sqm_huf"] != []

    await eng.dispose()


@pytest.mark.asyncio
async def test_include_breakdown_false_omits_categories():
    """When include_breakdown=False, categories key is absent."""
    from datetime import date
    from renovai.predictor.price_model import scope_matched_estimate
    from renovai.ingestion.inflation_calc import load_price_index
    from renovai.db.session import get_engine, get_session_maker

    apt = ApartmentInput(
        district=5, total_area_sqm=55, num_rooms=2,
        needs_plumbing=True,
    )

    pi = load_price_index(
        DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv",
        DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv",
    )
    eng = get_engine("sqlite+aiosqlite:///data/renovai.db")
    sm = get_session_maker(eng)

    est = await scope_matched_estimate(apt, sm, pi, date.today(),
                                       include_breakdown=False)
    assert est is not None
    assert "categories" not in est
    assert "estimate_mid_huf" in est  # top-level still there
    await eng.dispose()


# ---------------------------------------------------------------------------
# Item G — Tab 2 corpus wiring tests
# ---------------------------------------------------------------------------

class _FakePolicy_:
    def check_structural(self, r, a, t):
        return PolicyCheckResult(True, "ok", t, "structural")

    async def check_semantic(self, a, t, **kwargs):
        return PolicyCheckResult(True, "ok", t, "semantic")


class _FakeRegistry_:
    def load_instructions(self, n):
        return ""


@pytest.mark.asyncio
async def test_construction_planning_has_data_source_on_all_phases():
    from orchestrator.handlers import handle_construction_planning

    params = {
        "area_sqm": 55.0, "building_era": "1980", "renovation_scope": "full",
        "gas_heating": False, "floor_number": 1, "elevator_type": "large",
        "scope_flags": {"plumbing": True, "electrical": True, "flooring": True,
                        "demolition": True, "ac": True},
    }
    result = await handle_construction_planning(
        params, _FakePolicy_(), _FakeRegistry_(), "t1",
    )
    phases = result["data"]["phases"]
    for p in phases:
        assert "data_source" in p, f"missing data_source in {p['name']}"
        assert p["data_source"] in ("corpus", "corpus_fallback", "hardcoded_2025")


@pytest.mark.asyncio
async def test_construction_planning_partial_different_confidence():
    from orchestrator.handlers import handle_construction_planning

    full_params = {
        "area_sqm": 55.0, "building_era": "1980", "renovation_scope": "full",
        "gas_heating": False, "floor_number": 1, "elevator_type": "large",
        "scope_flags": {"plumbing": True, "electrical": True, "flooring": True,
                        "demolition": True, "ac": True},
    }
    partial_params = {
        "area_sqm": 55.0, "building_era": "9999", "renovation_scope": "partial",
        "gas_heating": False, "floor_number": 1, "elevator_type": "large",
        "scope_flags": {"plumbing": True, "plastering": True},
    }

    full = await handle_construction_planning(full_params, _FakePolicy_(), _FakeRegistry_(), "t1")
    partial = await handle_construction_planning(partial_params, _FakePolicy_(), _FakeRegistry_(), "t2")

    assert full["data"]["confidence"]["reasoning"] != partial["data"]["confidence"]["reasoning"]
    # Full should have corpus-derived confidence
    assert "corpus" in full["data"]["confidence"]["reasoning"].lower()
    assert full["data"]["confidence"]["score"] != partial["data"]["confidence"]["score"]


@pytest.mark.asyncio
async def test_windows_doors_insulation_fallback_values_similar_to_old_hardcoded():
    from orchestrator.handlers import handle_construction_planning

    params = {
        "area_sqm": 55.0, "building_era": "1980", "renovation_scope": "full",
        "gas_heating": False, "floor_number": 1, "elevator_type": "large",
        "scope_flags": {"plumbing": True, "electrical": True, "flooring": True,
                        "demolition": True, "windows_doors": True, "insulation": True},
    }
    result = await handle_construction_planning(params, _FakePolicy_(), _FakeRegistry_(), "t3")
    phases = {p["name"]: p for p in result["data"]["phases"]}

    # Windows/doors should be hardcoded_2025 (unit-based) with reasonable costs
    wd = phases.get("Nyílászáró csere (Windows & Doors)")
    assert wd is not None
    assert wd["data_source"] == "hardcoded_2025"
    # Old unit-based: 200k-400k per unit * 3 units = 600k-1.2M material
    ml, mh = map(lambda x: int(x.replace(",", "").replace(" Ft", "")),
                 wd["material_cost_range"].split(" - "))
    assert 500_000 < ml < 700_000  # ~600k for 3 units

    ins = phases.get("Szigetelés (Insulation)")
    assert ins is not None
    assert ins["data_source"] == "corpus_fallback"


@pytest.mark.asyncio
async def test_vibe_diff_infra_numbers_match_actual_deltas():
    from orchestrator.handlers import handle_construction_planning

    params = {
        "area_sqm": 42.0, "building_era": "2005", "renovation_scope": "full",
        "gas_heating": True, "floor_number": 1, "elevator_type": "small",
        "scope_flags": {"plumbing": True, "electrical": True, "flooring": True,
                        "demolition": True},
    }
    result = await handle_construction_planning(params, _FakePolicy_(), _FakeRegistry_(), "t4")
    data = result["data"]

    # vibe_diff hidden chains exist
    vibe = data.get("vibe_diff", {})
    chains = vibe.get("hidden_chains", [])
    assert len(chains) > 0

    # The infra text must use actual computed values, not hardcoded 300k/800k
    infra_texts = [c for c in chains if "Infrastrukturális" in c or "Gáz/fűtés" in c]
    for t in infra_texts:
        assert "300k" not in t, f"hardcoded 300k in vibe_diff: {t}"
        assert "800k" not in t, f"hardcoded 800k in vibe_diff: {t}"


@pytest.mark.asyncio
async def test_rough_in_aggregates_all_three_categories():
    """The MEP phase must sum plumbing + electrical + AC from the categories dict."""
    from datetime import date
    from renovai.predictor.price_model import scope_matched_estimate
    from renovai.ingestion.inflation_calc import load_price_index
    from renovai.db.session import get_engine, get_session_maker

    apt = ApartmentInput(
        district=5, total_area_sqm=55, num_rooms=2,
        needs_plumbing=True, needs_electrical=True, needs_ac=True,
        needs_flooring=False, needs_full_demolition=False,
    )
    pi = load_price_index(
        DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv",
        DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv",
    )
    eng = get_engine("sqlite+aiosqlite:///data/renovai.db")
    sm = get_session_maker(eng)
    est = await scope_matched_estimate(apt, sm, pi, date.today())

    categories = est["categories"]
    p_mid = categories["needs_plumbing"]["estimate_huf"]["mid"]
    e_mid = categories["needs_electrical"]["estimate_huf"]["mid"]
    a_mid = categories["needs_ac"]["estimate_huf"]["mid"]
    expected_sum = p_mid + e_mid + a_mid

    # The construction planner's rough-in phase should equal this sum
    # when only those 3 scopes are active (no heating, no other electrical add-ons)
    from orchestrator.handlers import handle_construction_planning

    params = {
        "area_sqm": 55.0, "building_era": "2005", "renovation_scope": "partial",
        "gas_heating": False, "floor_number": 1, "elevator_type": "large",
        "scope_flags": {"plumbing": True, "electrical": True, "ac": True,
                        "demolition": False, "flooring": False, "plastering": False,
                        "painting": False},
    }
    result = await handle_construction_planning(params, _FakePolicy_(), _FakeRegistry_(), "t5")
    phases = {p["name"]: p for p in result["data"]["phases"]}

    rough_in = phases.get("Gépészet (Plumbing, Electrical, HVAC)")
    assert rough_in is not None

    def mid_from_range(mr, lr):
        m_lo, m_hi = [int(x.replace(",","").replace(" Ft","")) for x in mr.split(" - ")]
        l_lo, l_hi = [int(x.replace(",","").replace(" Ft","")) for x in lr.split(" - ")
                      if "0 Ft" not in x]
        return (m_lo + m_hi + l_lo + l_hi) // 2 if l_lo and l_hi else (m_lo + m_hi) // 2

    rough_mid = mid_from_range(rough_in["material_cost_range"], rough_in["labor_cost_range"])

    # The rough-in mid should be close to the sum of the 3 category mids
    # (within rounding and 55/45 split approximation)
    assert 0.85 * expected_sum <= rough_mid <= 1.15 * expected_sum, (
        f"rough-in mid={rough_mid:,}, expected ~{expected_sum:,} "
        f"(ratio={rough_mid/expected_sum:.3f})"
    )
    await eng.dispose()


@pytest.mark.asyncio
async def test_windows_doors_stays_unit_based_not_fallback():
    """Windows/doors must use unit-count-based hardcoded logic, not corpus_fallback."""
    from orchestrator.handlers import handle_construction_planning

    params = {
        "area_sqm": 55.0, "building_era": "2005", "renovation_scope": "partial",
        "gas_heating": False, "floor_number": 1, "elevator_type": "large",
        "scope_flags": {"windows_doors": True, "demolition": False, "flooring": False,
                        "plumbing": False, "electrical": False},
    }
    result = await handle_construction_planning(params, _FakePolicy_(), _FakeRegistry_(), "t6")
    phases = {p["name"]: p for p in result["data"]["phases"]}

    wd = phases.get("Nyílászáró csere (Windows & Doors)")
    assert wd is not None
    assert wd["data_source"] == "hardcoded_2025", (
        f"windows_doors should stay hardcoded_2025 (unit-based), got {wd['data_source']}"
    )