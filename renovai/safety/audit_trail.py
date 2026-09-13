import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEntry:
    """Immutable audit trail entry.

    Every entry is bound to:
    - A trace_id (spanning the full Gateway -> Sub-Agent -> Safety Harness path)
    - An optional human approval timestamp (if Green Team intervened)
    - The full before/after snapshots for replayability

    Entries are append-only. Delete is not permitted.
    """

    entry_id: str
    trace_id: str
    action_type: (
        str  # "cost_estimation" | "due_diligence" | "ingestion" | "market_query"
    )
    user_intent: str
    agent_response_summary: str
    confidence_score: float
    vibe_diff_id: str | None
    human_approval_timestamp: str | None
    human_reviewer: str | None
    structural_check_passed: bool
    semantic_check_passed: bool
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Per-policy-check context (added for per-check audit entries; all default
    # to None so pre-existing request-level entries remain fully loadable).
    role: str | None = None
    action: str | None = None
    resource: str | None = None
    decision: str | None = None  # "allow" | "deny"

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "trace_id": self.trace_id,
            "action_type": self.action_type,
            "user_intent": self.user_intent,
            "agent_response_summary": self.agent_response_summary,
            "confidence_score": self.confidence_score,
            "vibe_diff_id": self.vibe_diff_id,
            "human_approval_timestamp": self.human_approval_timestamp,
            "human_reviewer": self.human_reviewer,
            "structural_check_passed": self.structural_check_passed,
            "semantic_check_passed": self.semantic_check_passed,
            "timestamp": self.timestamp,
            "role": self.role,
            "action": self.action,
            "resource": self.resource,
            "decision": self.decision,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class AuditStore:
    """Append-only store for audit trail entries.

    Backed by a local JSONL file. Each line is one AuditEntry JSON blob.
    Designed to be mirrored to BigQuery for long-term immutable storage.

    Usage:
        store = AuditStore()
        entry = AuditEntry(
            trace_id="gw-abc123",
            action_type="cost_estimation",
            user_intent="mennyibe kerül a felújítás",
            agent_response_summary="estimate: 8-10M HUF",
            confidence_score=0.85,
            vibe_diff_id="vd-xyz789",
            human_approval_timestamp=None,
            human_reviewer=None,
            structural_check_passed=True,
            semantic_check_passed=True,
        )
        store.append(entry)
        all_entries = store.read_all()
    """

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = (
                Path(__file__).resolve().parent.parent.parent
                / "data"
                / "audit_trail.jsonl"
            )
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: AuditEntry) -> None:
        """Append an audit entry. This is the only write operation.

        Each line is one compact JSON blob so the file is a true JSONL
        (json.dumps with indent=2 would span multiple lines and break
        read_all()/count()).
        """
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        logger.info("[%s] Audit entry appended: %s", entry.trace_id, entry.entry_id)

    def read_all(self) -> list[AuditEntry]:
        """Read all audit entries from the JSONL file (in order)."""
        if not self._path.exists():
            return []
        entries: list[AuditEntry] = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entries.append(AuditEntry(**data))
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    logger.warning("Skipping malformed audit entry: %s", exc)
        return entries

    def count(self) -> int:
        """Return the number of audit entries."""
        if not self._path.exists():
            return 0
        count = 0
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    def record_policy_check(
        self,
        trace_id: str,
        role: str,
        action: str,
        resource: str,
        decision: str,
        check_type: str,
    ) -> AuditEntry:
        """Append an audit entry for a resolved policy check.

        Used by PolicyService after every check_structural / check_semantic
        call resolves (allow or deny), so every gate decision is retrievable
        per trace_id.
        """
        entry = AuditEntry(
            entry_id=f"ae-{uuid.uuid4().hex[:12]}",
            trace_id=trace_id,
            action_type=f"{check_type}_check",
            user_intent=action,
            agent_response_summary=(
                f"{decision}: role={role}, action={action}, resource={resource}"
            ),
            confidence_score=0.0,
            vibe_diff_id=None,
            human_approval_timestamp=None,
            human_reviewer=None,
            structural_check_passed=(
                check_type == "structural" and decision == "allow"
            ),
            semantic_check_passed=(check_type == "semantic" and decision == "allow"),
            role=role,
            action=action,
            resource=resource,
            decision=decision,
        )
        self.append(entry)
        return entry

    def export_to_bigquery(self) -> list[dict[str, Any]]:
        """Return all entries as dicts, ready for BigQuery streaming insert."""
        return [e.to_dict() for e in self.read_all()]

    def build_entry(
        self,
        trace_id: str,
        action_type: str,
        user_intent: str,
        agent_response_summary: str,
        confidence_score: float,
        vibe_diff_id: str | None = None,
        human_approval_timestamp: str | None = None,
        human_reviewer: str | None = None,
        structural_check_passed: bool = True,
        semantic_check_passed: bool = True,
    ) -> AuditEntry:
        """Convenience factory for creating an AuditEntry with an auto-generated ID."""
        return AuditEntry(
            entry_id=f"ae-{uuid.uuid4().hex[:12]}",
            trace_id=trace_id,
            action_type=action_type,
            user_intent=user_intent,
            agent_response_summary=agent_response_summary,
            confidence_score=confidence_score,
            vibe_diff_id=vibe_diff_id,
            human_approval_timestamp=human_approval_timestamp,
            human_reviewer=human_reviewer,
            structural_check_passed=structural_check_passed,
            semantic_check_passed=semantic_check_passed,
        )
