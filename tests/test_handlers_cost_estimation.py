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

    async def check_semantic(self, args: dict, trace_id: str):
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