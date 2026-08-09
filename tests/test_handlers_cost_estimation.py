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
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.policy_service import PolicyCheckResult

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