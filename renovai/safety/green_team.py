import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from renovai.safety.vibe_diff import VibeDiff

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovalRequest:
    """Structured request sent to the human-in-the-loop reviewer.

    Generated whenever the system detects a high-stakes action that
    requires human approval before proceeding.
    """

    request_id: str
    trace_id: str
    user_intent: str
    proposed_action: str
    proposed_response: dict[str, Any]
    vibe_diff: VibeDiff | None
    confidence_score: float
    risk_level: str               # "low" | "medium" | "high" | "critical"
    trigger_reason: str           # e.g. "confidence < 0.7" | "semantic_risk"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "pending"       # "pending" | "approved" | "rejected"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    reviewer_notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "user_intent": self.user_intent,
            "proposed_action": self.proposed_action,
            "proposed_response": self.proposed_response,
            "vibe_diff": self.vibe_diff.to_dict() if self.vibe_diff else None,
            "confidence_score": self.confidence_score,
            "risk_level": self.risk_level,
            "trigger_reason": self.trigger_reason,
            "created_at": self.created_at,
            "status": self.status,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "reviewer_notes": self.reviewer_notes,
        }


@dataclass
class InterventionDecision:
    """Structured output from the Green Team review gate."""
    needs_intervention: bool
    reason: str
    request: ApprovalRequest | None = None


class GreenTeamService:
    """Human-in-the-loop gate for high-stakes agent actions.

    The Green Team acts as a deterministic safety valve:

    1. If the Gateway Agent reports confidence < 0.7, the system pauses
       and triggers an approval request.
    2. If the Policy Server flags a semantic risk (medium+), the system
       pauses and triggers an approval request.
    3. The ApprovalRequest bundles the user intent, proposed action, and
       Vibe Diff into a single structured payload for a human reviewer.

    Usage:
        green_team = GreenTeamService()
        decision = await green_team.evaluate(
            trace_id="gw-abc",
            user_intent="cost_estimation",
            confidence=0.55,
            proposed_action="produce_estimate",
            proposed_response={...},
            vibe_diff=diff,
            semantic_risk="medium",
        )
        if decision.needs_intervention:
            # Send decision.request to human dashboard / Slack / email
            # Wait for human to call approve() or reject()
            pass
        else:
            # Proceed with execution
            pass
    """

    def __init__(self, confidence_threshold: float = 0.7):
        self._threshold = confidence_threshold

    async def evaluate(
        self,
        trace_id: str,
        user_intent: str,
        confidence: float,
        proposed_action: str,
        proposed_response: dict[str, Any],
        vibe_diff: VibeDiff | None = None,
        semantic_risk: str = "low",
    ) -> InterventionDecision:
        """Determine whether this action needs human review.

        Returns an InterventionDecision with needs_intervention=True if:
        - confidence < confidence_threshold (default 0.7), OR
        - semantic_risk is "high" or "critical"
        """
        trigger_reasons: list[str] = []

        if confidence < self._threshold:
            trigger_reasons.append(
                f"confidence ({confidence:.2f}) < threshold ({self._threshold})"
            )

        if semantic_risk in ("high", "critical"):
            trigger_reasons.append(f"semantic risk level: {semantic_risk}")

        if not trigger_reasons:
            return InterventionDecision(
                needs_intervention=False,
                reason="All checks passed — no intervention needed.",
            )

        risk_level = self._resolve_risk_level(confidence, semantic_risk)
        trigger_reason = "; ".join(trigger_reasons)

        request = ApprovalRequest(
            request_id=f"gt-{uuid.uuid4().hex[:12]}",
            trace_id=trace_id,
            user_intent=user_intent,
            proposed_action=proposed_action,
            proposed_response=proposed_response,
            vibe_diff=vibe_diff,
            confidence_score=confidence,
            risk_level=risk_level,
            trigger_reason=trigger_reason,
        )

        logger.info(
            "[%s] GREEN TEAM intervention triggered: %s (risk=%s)",
            trace_id, trigger_reason, risk_level,
        )

        return InterventionDecision(
            needs_intervention=True,
            reason=f"Intervention required: {trigger_reason}",
            request=request,
        )

    def approve(self, request: ApprovalRequest, reviewer: str, notes: str | None = None) -> ApprovalRequest:
        """Record a human approval decision."""
        updated = ApprovalRequest(
            request_id=request.request_id,
            trace_id=request.trace_id,
            user_intent=request.user_intent,
            proposed_action=request.proposed_action,
            proposed_response=request.proposed_response,
            vibe_diff=request.vibe_diff,
            confidence_score=request.confidence_score,
            risk_level=request.risk_level,
            trigger_reason=request.trigger_reason,
            created_at=request.created_at,
            status="approved",
            reviewed_by=reviewer,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            reviewer_notes=notes,
        )
        logger.info(
            "[%s] GREEN TEAM approved by %s", request.trace_id, reviewer,
        )
        return updated

    def reject(self, request: ApprovalRequest, reviewer: str, notes: str | None = None) -> ApprovalRequest:
        """Record a human rejection decision."""
        updated = ApprovalRequest(
            request_id=request.request_id,
            trace_id=request.trace_id,
            user_intent=request.user_intent,
            proposed_action=request.proposed_action,
            proposed_response=request.proposed_response,
            vibe_diff=request.vibe_diff,
            confidence_score=request.confidence_score,
            risk_level=request.risk_level,
            trigger_reason=request.trigger_reason,
            created_at=request.created_at,
            status="rejected",
            reviewed_by=reviewer,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            reviewer_notes=notes,
        )
        logger.info(
            "[%s] GREEN TEAM rejected by %s", request.trace_id, reviewer,
        )
        return updated

    @staticmethod
    def _resolve_risk_level(confidence: float, semantic_risk: str) -> str:
        """Combine confidence and semantic risk into a single risk level."""
        if semantic_risk == "critical":
            return "critical"
        if semantic_risk == "high":
            return "high"
        if confidence < 0.4:
            return "high"
        if confidence < 0.7:
            return "medium"
        return "low"
