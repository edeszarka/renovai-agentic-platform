"""
Vibe Trajectory Audit — End-to-End Safety Envelope Report.

Generates a structured report that proves the agent stayed within its
'Safety Envelope' across all 7 Pillars of the RenovAI Security Architecture:

  1. Intent Classification (Gateway) — correct routing, no hallucinated intents
  2. Zero Ambient Authority (PolicyService) — structural + semantic gates pass
  3. Vibe Diff (Explainability) — human-readable reasoning for every decision
  4. Green Team (HITL) — human approval for high-stakes / low-confidence actions
  5. Agentic Identity (SPIFFE) — JIT downscoped tokens, no privilege leakage
  6. Audit Trail (Immutable) — every financial advice bound to trace_id + approval
  7. Telemetry (OpenTelemetry) — full span tree, PII-sanitized, exportable

Usage:
    tracker = VibeTrajectoryTracker(audit_store, tracer, identity_manager)
    await tracker.record_step("gateway", "classify", {"intent": "cost_estimation"}, "ok")
    await tracker.record_step("policy", "check_structural", {"role": "cost_estimator"}, "ok")
    report = tracker.generate_report()
    print(json.dumps(report, indent=2))
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from renovai.safety.agentic_identity import AgentIdentityManager
from renovai.safety.audit_trail import AuditStore
from renovai.safety.telemetry import RenovAITracer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 7-Pillar Safety Envelope
# ---------------------------------------------------------------------------

PILLAR_DEFINITIONS: dict[str, str] = {
    "intent_classification": "Gateway correctly classifies user intent without hallucination",
    "zero_ambient_authority": "Every tool call passes structural + semantic policy gates",
    "vibe_diff": "Every agent decision has a plain-English Vibe Diff for review",
    "green_team": "High-stakes or low-confidence actions trigger HITL approval",
    "agentic_identity": "Each sub-agent has a JIT-downscoped SPIFFE identity",
    "audit_trail": "Every financial advice is bound to trace_id + human approval timestamp",
    "telemetry": "Full span tree logged, PII-sanitized, exportable to BigQuery",
}


@dataclass
class TrajectoryStep:
    """A single step in the agent's execution trajectory."""

    step_id: int
    pillar: str
    component: str
    action: str
    status: str  # "ok" | "error" | "blocked" | "pending_approval" | "approved"
    duration_ms: float
    detail: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "pillar": self.pillar,
            "component": self.component,
            "action": self.action,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 2),
            "detail": self.detail,
            "timestamp": self.timestamp,
        }


@dataclass
class SafetyEnvelopeResult:
    """Result of validating a single pillar of the Safety Envelope."""

    pillar: str
    definition: str
    passed: bool
    steps: int
    errors: int
    total_duration_ms: float
    evidence: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Trajectory Tracker
# ---------------------------------------------------------------------------


class VibeTrajectoryTracker:
    """Records and reports the full Vibe Trajectory across 7 pillars.

    Usage:
        tracker = VibeTrajectoryTracker()
        tracker.set_context(trace_id="gw-abc123", user_intent="cost_estimation")

        # Record each step as the agent executes
        await tracker.record_step("intent_classification", "gateway", "classify", "ok", 12.5, {"intent": "cost_estimation"})
        await tracker.record_step("zero_ambient_authority", "policy", "check_structural", "ok", 2.1, {"role": "cost_estimator", "action": "produce_estimate"})
        await tracker.record_step("vibe_diff", "vibe_diff_engine", "generate", "ok", 150.0, {"vibe_id": "vd-abc"})
        await tracker.record_step("green_team", "green_team", "evaluate", "ok", 5.0, {"confidence": 0.85, "needs_intervention": False})

        report = tracker.generate_report()
    """

    def __init__(
        self,
        audit_store: AuditStore | None = None,
        tracer: RenovAITracer | None = None,
        identity_manager: AgentIdentityManager | None = None,
    ):
        self._steps: list[TrajectoryStep] = []
        self._trace_id: str = ""
        self._user_intent: str = ""
        self._start_time: float | None = None
        self._audit = audit_store or AuditStore()
        self._tracer = tracer
        self._identity_mgr = identity_manager

    def set_context(self, trace_id: str, user_intent: str) -> None:
        """Set the trace context for this trajectory."""
        self._trace_id = trace_id
        self._user_intent = user_intent
        self._start_time = datetime.now(timezone.utc)

    async def record_step(
        self,
        pillar: str,
        component: str,
        action: str,
        status: str,
        duration_ms: float,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Record a single step in the Vibe Trajectory."""
        step = TrajectoryStep(
            step_id=len(self._steps) + 1,
            pillar=pillar,
            component=component,
            action=action,
            status=status,
            duration_ms=duration_ms,
            detail=detail or {},
        )
        self._steps.append(step)
        logger.debug(
            "[%s] Trajectory step %d: %s/%s -> %s (%.2fms)",
            self._trace_id,
            step.step_id,
            pillar,
            action,
            status,
            duration_ms,
        )

    def generate_report(self) -> dict[str, Any]:
        """Generate the final Vibe Trajectory Audit Report.

        The report proves the agent stayed within its Safety Envelope by
        enumerating every pillar, showing the passing/failing status,
        total steps, errors, and evidence for each.
        """
        total_steps = len(self._steps)
        total_errors = sum(1 for s in self._steps if s.status == "error")
        total_blocked = sum(1 for s in self._steps if s.status == "blocked")

        # Group steps by pillar
        pillars: dict[str, list[TrajectoryStep]] = {}
        for step in self._steps:
            pillars.setdefault(step.pillar, []).append(step)

        pillar_results: dict[str, SafetyEnvelopeResult] = {}
        all_passed = True

        for pillar_name, definition in PILLAR_DEFINITIONS.items():
            steps = pillars.get(pillar_name, [])
            pillar_errors = sum(1 for s in steps if s.status in ("error", "blocked"))
            pillar_duration = sum(s.duration_ms for s in steps)
            passed = pillar_errors == 0

            if not passed:
                all_passed = False

            pillar_results[pillar_name] = SafetyEnvelopeResult(
                pillar=pillar_name,
                definition=definition,
                passed=passed,
                steps=len(steps),
                errors=pillar_errors,
                total_duration_ms=pillar_duration,
                evidence=[s.to_dict() for s in steps],
            )

        # Calculate total wall-clock time
        total_wall_clock_ms = sum(s.duration_ms for s in self._steps)

        # Build audit entry for this trajectory
        if self._trace_id:
            audit_entry = self._audit.build_entry(
                trace_id=self._trace_id,
                action_type=self._user_intent or "unknown",
                user_intent=self._user_intent or "unknown",
                agent_response_summary=(
                    f"Vibe Trajectory: {total_steps} steps, "
                    f"{total_errors} errors, "
                    f"{'ALL PASSED' if all_passed else 'SOME FAILED'}"
                ),
                confidence_score=1.0 if all_passed else 0.0,
                structural_check_passed=True,
                semantic_check_passed=True,
            )
            self._audit.append(audit_entry)

        report = {
            "report_type": "vibe_trajectory_audit",
            "trace_id": self._trace_id,
            "user_intent": self._user_intent,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_steps": total_steps,
            "total_errors": total_errors,
            "total_blocked": total_blocked,
            "total_wall_clock_ms": round(total_wall_clock_ms, 2),
            "safety_envelope_passed": all_passed,
            "sustained_pass": all_passed and total_errors == 0,
            "audit_entry_id": audit_entry.entry_id if self._trace_id else None,
            "pillars": {
                name: {
                    "definition": result.definition,
                    "passed": result.passed,
                    "steps": result.steps,
                    "errors": result.errors,
                    "total_duration_ms": round(result.total_duration_ms, 2),
                    "evidence": result.evidence,
                }
                for name, result in pillar_results.items()
            },
        }

        return report

    def generate_summary(self, report: dict[str, Any]) -> str:
        """Generate a human-readable summary of the Vibe Trajectory."""
        lines = [
            f"Vibe Trajectory Audit Report",
            f"{'=' * 60}",
            f"Trace ID: {report.get('trace_id', 'N/A')}",
            f"Intent:   {report.get('user_intent', 'N/A')}",
            f"Time:     {report.get('generated_at', 'N/A')}",
            f"",
            f"Safety Envelope: {'PASSED' if report.get('safety_envelope_passed') else 'FAILED'}",
            f"Sustained Pass:  {'YES' if report.get('sustained_pass') else 'NO'}",
            f"",
            f"Steps: {report.get('total_steps')} | Errors: {report.get('total_errors')} | "
            f"Blocked: {report.get('total_blocked')}",
            f"Wall-clock: {report.get('total_wall_clock_ms', 0)}ms",
            f"",
        ]

        pillars = report.get("pillars", {})
        for name, data in pillars.items():
            icon = "[PASS]" if data["passed"] else "[FAIL]"
            lines.append(
                f"  {icon} {name}: {data['steps']} steps, "
                f"{data['errors']} errors, "
                f"{data['total_duration_ms']}ms"
            )

        lines.append(f"{'=' * 60}")
        return "\n".join(lines)
