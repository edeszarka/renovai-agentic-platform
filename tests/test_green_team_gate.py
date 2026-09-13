"""
Task 3 — Green Team human-in-the-loop gate wiring tests.

Proves that the handler responses now *surface* a needs_intervention flag when
the existing confidence / risk signals are weak, instead of returning a plain
number.  These tests fail on the pre-task-3 handlers (which have no
needs_intervention key at all) and pass once the gate is wired.
"""

import pytest

from orchestrator.policy_service import PolicyCheckResult


class FakePolicyService:
    def __init__(self):
        self._result = PolicyCheckResult(
            passed=True, reason="test-gate", trace_id="t", check_type="structural"
        )

    def check_structural(
        self, role: str, action: str, trace_id: str
    ) -> PolicyCheckResult:
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
        "building_era": "1965",
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


# ---------------------------------------------------------------------------
# cost_estimation — low-confidence input must surface needs_intervention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requires_local_corpus
async def test_cost_estimation_low_similarity_surfaces_needs_intervention(monkeypatch):
    """When fewer than 3 similar corpus quotes are found (the exact condition
    the handler's existing warning logic already flags as data scarcity),
    the response must carry needs_intervention=True."""
    from orchestrator.handlers import handle_cost_estimation

    monkeypatch.setattr(
        "renovai.predictor.price_model.find_similar_quotes",
        lambda features, quotes_dir, top_k=3: [
            {
                "file": "only.json",
                "address": 5,
                "grand_total_adjusted": 1_000_000,
                "distance": 0.5,
            },
        ],
    )

    result = await handle_cost_estimation(
        realistic_params(),
        policy_service=FakePolicyService(),
        skill_registry=FakeSkillRegistry(),
        trace_id="test-gt-lowconf",
    )

    assert result["status"] == "ok", f"handler errored: {result.get('error')}"
    assert result["data"]["needs_intervention"] is True


# ---------------------------------------------------------------------------
# expert_interview — high semantic risk must surface needs_intervention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expert_interview_critical_risk_surfaces_needs_intervention():
    """A pre-1960 / slag building drives overall_risk=CRITICAL; the gate must
    reuse that existing risk level and surface needs_intervention=True."""
    from orchestrator.handlers import handle_expert_interview

    params = {
        "district": 5,
        "area_sqm": 55.0,
        "num_rooms": 2,
        "building_type": "tégla",
        "building_era": "1950",
        "scope_flags": {"slag": True},
        "has_seen_in_person": False,
    }

    result = await handle_expert_interview(
        params,
        policy_service=FakePolicyService(),
        skill_registry=FakeSkillRegistry(),
        trace_id="test-gt-highrisk",
    )

    assert result["status"] == "ok", f"handler errored: {result.get('error')}"
    assert result["data"]["overall_risk"] == "CRITICAL"
    assert result["data"]["needs_intervention"] is True
    assert "green_team" in result["data"]


@pytest.mark.asyncio
async def test_expert_interview_low_risk_no_intervention():
    """A modern, low-risk profile must NOT trigger the gate (needs_intervention
    is still surfaced, as False, so the UI can render either state)."""
    from orchestrator.handlers import handle_expert_interview

    params = {
        "district": 5,
        "area_sqm": 55.0,
        "num_rooms": 2,
        "building_type": "panel",
        "building_era": "2005",
        "scope_flags": {},
        "has_seen_in_person": False,
    }

    result = await handle_expert_interview(
        params,
        policy_service=FakePolicyService(),
        skill_registry=FakeSkillRegistry(),
        trace_id="test-gt-norisk",
    )

    assert result["status"] == "ok", f"handler errored: {result.get('error')}"
    assert result["data"]["overall_risk"] == "LOW"
    assert result["data"]["needs_intervention"] is False
