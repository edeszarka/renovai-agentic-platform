"""
Task 4 — Policy-level audit trail wiring tests.

Every resolved check_structural / check_semantic call must append an audit
entry (trace_id, role, action, resource, decision, timestamp) to the
AuditStore, and a full Tab 1 (expert_interview) request through a real
PolicyService must produce a retrievable per-trace trail.
"""
import uuid

import pytest

from orchestrator.policy_service import PolicyCheckResult, PolicyService
from renovai.safety.audit_trail import AuditStore


class _FakeRegistry:
    def load_instructions(self, name: str) -> str:
        return ""


async def _ok_semantic(tool_args: dict, trace_id: str):
    """Hermetic stand-in for _llm_semantic_check: always allows, keeping the
    request's trace_id so audit entries are attributable."""
    return PolicyCheckResult(True, "ok", trace_id, "semantic")


@pytest.mark.asyncio
async def test_check_structural_and_semantic_write_audit_entries(tmp_path):
    store = AuditStore(path=tmp_path / "audit.jsonl")
    svc = PolicyService(audit_store=store)
    svc._llm_semantic_check = _ok_semantic

    structural = svc.check_structural("expert_interviewer", "assess_risk", "trace-a")
    assert structural.passed

    semantic = await svc.check_semantic(
        {"district": 5, "area_sqm": 55.0},
        "trace-a",
        role="expert_interviewer",
        action="assess_risk",
    )
    assert semantic.passed

    entries = store.read_all()
    assert len(entries) == 2, f"expected 2 entries, got {len(entries)}"

    se = [e for e in entries if e.action_type == "structural_check"][0]
    assert se.trace_id == "trace-a"
    assert se.role == "expert_interviewer"
    assert se.action == "assess_risk"
    assert se.resource == "-"
    assert se.decision == "allow"
    assert se.structural_check_passed is True
    assert se.timestamp

    se_sem = [e for e in entries if e.action_type == "semantic_check"][0]
    assert se_sem.trace_id == "trace-a"
    assert se_sem.role == "expert_interviewer"
    assert se_sem.action == "assess_risk"
    assert se_sem.decision == "allow"
    assert se_sem.semantic_check_passed is True


def test_denied_structural_check_records_deny(tmp_path):
    store = AuditStore(path=tmp_path / "audit.jsonl")
    svc = PolicyService(audit_store=store)

    denied = svc.check_structural("nonexistent_role", "x", "trace-deny")
    assert not denied.passed

    entries = store.read_all()
    assert len(entries) == 1
    e = entries[0]
    assert e.decision == "deny"
    assert e.role == "nonexistent_role"
    assert e.action == "x"


@pytest.mark.asyncio
async def test_expert_interview_request_produces_full_trail(tmp_path):
    """Tab 1 (expert_interview) through a real PolicyService + AuditStore must
    leave a retrievable structural+semantic trail for the request's trace_id."""
    from orchestrator.handlers import handle_expert_interview

    store = AuditStore(path=tmp_path / "audit.jsonl")
    svc = PolicyService(audit_store=store)
    svc._llm_semantic_check = _ok_semantic

    trace_id = f"st-{uuid.uuid4().hex[:12]}"
    params = {
        "district": 5,
        "area_sqm": 55.0,
        "num_rooms": 2,
        "building_type": "tégla",
        "building_era": "2005",
        "scope_flags": {},
        "has_seen_in_person": False,
    }

    result = await handle_expert_interview(params, svc, _FakeRegistry(), trace_id)

    assert result["status"] == "ok", f"handler errored: {result.get('error')}"
    by_trace = [e for e in store.read_all() if e.trace_id == trace_id]
    assert len(by_trace) == 2, f"expected 2 audit entries for trace, got {len(by_trace)}"

    structural = [e for e in by_trace if e.action_type == "structural_check"][0]
    assert structural.role == "expert_interviewer"
    assert structural.action == "assess_risk"
    assert structural.decision == "allow"

    semantic = [e for e in by_trace if e.action_type == "semantic_check"][0]
    assert semantic.role == "expert_interviewer"
    assert semantic.action == "assess_risk"
    assert semantic.decision == "allow"