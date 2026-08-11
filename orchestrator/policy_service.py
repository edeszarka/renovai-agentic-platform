"""
Policy Service — Safety Harness for RenovAI 2.0.

Two-layer gate:
  1. Structural checks: Role-based access control defined in policies.yaml.
     Checks which roles may access which resources and actions.
  2. Semantic checks: Gemini-based detection of PII, policy violations, or
     unsafe content in tool arguments before execution.

Every check is logged with a trace_id for full auditability.
"""

import os
import re
import json
import uuid
import logging
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field

import yaml

from renovai.safety.audit_trail import AuditStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PolicyCheckResult:
    """Result of a single policy check."""
    passed: bool
    reason: str
    trace_id: str
    check_type: str  # "structural" or "semantic"


@dataclass
class PolicyEngine:
    """Loaded policy rules from policies.yaml."""
    roles: dict[str, Any] = field(default_factory=dict)
    structural_checks: list[dict[str, Any]] = field(default_factory=list)
    semantic_config: dict[str, Any] = field(default_factory=dict)
    audit_config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PolicyEngine":
        if path is None:
            path = Path(__file__).resolve().parent.parent / "policies.yaml"
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return cls(
            roles=raw.get("roles", {}),
            structural_checks=raw.get("structural_checks", []),
            semantic_config=raw.get("semantic_checks", {}),
            audit_config=raw.get("audit", {}),
        )


# ---------------------------------------------------------------------------
# Policy Service
# ---------------------------------------------------------------------------

class PolicyService:
    """
    Two-layer safety harness.

    Usage:
        svc = PolicyService()
        result = await svc.check_structural("market_analyst", "execute_sql", trace_id)
        if result.passed:
            result = await svc.check_semantic({"question": "..."}, trace_id)
    """

    def __init__(
        self,
        policies_path: str | Path | None = None,
        audit_store: AuditStore | None = None,
    ):
        self._engine = PolicyEngine.load(policies_path)
        self._pii_patterns: list[tuple[str, re.Pattern]] = self._compile_pii_patterns()
        # Lazy-loaded Gemini client for semantic checks
        self._semantic_client = None
        # Agent Identity Manager for ABAC
        self._identity_manager: Any = None
        # Append-only audit trail (defaults to data/audit_trail.jsonl)
        self._audit_store: AuditStore = audit_store or AuditStore()

    def set_identity_manager(self, mgr: Any) -> None:
        """Inject the AgentIdentityManager for ABAC token verification."""
        self._identity_manager = mgr

    def _record_check(
        self,
        result: PolicyCheckResult,
        role: str,
        action: str,
        resource: str = "-",
    ) -> PolicyCheckResult:
        """Persist an audit entry for a resolved policy check.

        Appends an entry with the check's trace_id, role, action, resource and
        allow/deny decision, then returns the result unchanged so the existing
        call sites keep working without any behavior change.
        """
        decision = "allow" if result.passed else "deny"
        self._audit_store.record_policy_check(
            trace_id=result.trace_id,
            role=role,
            action=action,
            resource=resource,
            decision=decision,
            check_type=result.check_type,
        )
        return result

    # -----------------------------------------------------------------------
    # ABAC check (Agent Identity-based)
    # -----------------------------------------------------------------------

    def check_abac(
        self,
        identity: Any,
        action: str,
        resource: str | None = None,
        trace_id: str | None = None,
    ) -> PolicyCheckResult:
        """Attribute-Based Access Control check using the agent's SPIFFE identity.

        Verifies:
        1. The token is not expired
        2. The requested action is in the identity's allowed_actions
        3. The requested resource is in the identity's allowed_resources (if specified)
        """
        tid = trace_id or str(uuid.uuid4())

        if identity is None:
            return PolicyCheckResult(
                passed=False,
                reason="No agent identity provided. ABAC check requires a valid AgentIdentity token.",
                trace_id=tid,
                check_type="structural",
            )

        if identity.is_expired():
            return PolicyCheckResult(
                passed=False,
                reason=f"Agent identity expired at {identity.expires_at}. "
                       f"Request a new JIT-downscoped token.",
                trace_id=tid,
                check_type="structural",
            )

        if not identity.is_authorised(action, resource):
            return PolicyCheckResult(
                passed=False,
                reason=(
                    f"ABAC denied: identity '{identity.agent_id}' is not authorised "
                    f"for action '{action}'" +
                    (f" on resource '{resource}'" if resource else "") +
                    f". Allowed actions: {identity.claims.get('allowed_actions', [])}"
                ),
                trace_id=tid,
                check_type="structural",
            )

        logger.info(
            "[%s] ABAC PASS: agent=%s action=%s resource=%s",
            tid, identity.agent_id, action, resource,
        )
        return PolicyCheckResult(
            passed=True,
            reason=f"ABAC authorised: agent '{identity.agent_id}' may perform '{action}'.",
            trace_id=tid,
            check_type="structural",
        )

    # -----------------------------------------------------------------------
    # Structural checks (YAML-driven)
    # -----------------------------------------------------------------------

    def check_structural(
        self,
        role: str,
        action: str,
        trace_id: str | None = None,
    ) -> PolicyCheckResult:
        """
        Check whether `role` is allowed to perform `action`.

        This is a synchronous, fast YAML lookup — no LLM call.
        """
        tid = trace_id or str(uuid.uuid4())

        # 1. Resolve the role definition
        role_def = self._engine.roles.get(role)
        if role_def is None:
            return self._record_check(
                PolicyCheckResult(
                    passed=False,
                    reason=f"Unknown role: '{role}'",
                    trace_id=tid,
                    check_type="structural",
                ),
                role, action,
            )

        # 2. Check if this action is explicitly forbidden
        cannot_access = role_def.get("cannot_access", [])
        if action in cannot_access:
            return self._record_check(
                PolicyCheckResult(
                    passed=False,
                    reason=f"Role '{role}' is explicitly forbidden from accessing '{action}'.",
                    trace_id=tid,
                    check_type="structural",
                ),
                role, action,
            )

        # 3. Check if this action is in the allowlist
        can_execute = role_def.get("can_execute", [])
        can_access = role_def.get("can_access", [])
        allowed = can_execute + can_access
        if action not in allowed:
            return self._record_check(
                PolicyCheckResult(
                    passed=False,
                    reason=f"Role '{role}' does not have permission for action '{action}'. "
                           f"Allowed actions: {allowed}",
                    trace_id=tid,
                    check_type="structural",
                ),
                role, action,
            )

        # 4. Run action-specific structural checks from the rules list
        for rule in self._engine.structural_checks:
            if rule["action"] == action:
                if role not in rule.get("allowed_roles", []):
                    return self._record_check(
                        PolicyCheckResult(
                            passed=False,
                            reason=rule.get("deny_message", f"Role '{role}' not allowed for action '{action}'."),
                            trace_id=tid,
                            check_type="structural",
                        ),
                        role, action,
                    )
                # Check required fields (if the caller supplied them)
                required = rule.get("required_fields", [])
                if required:
                    # Caller must pass kwargs; we skip field validation here
                    # since the handler layer is responsible.
                    pass

        logger.info(
            "[%s] STRUCTURAL PASS: role=%s action=%s",
            tid, role, action,
        )
        return self._record_check(
            PolicyCheckResult(
                passed=True,
                reason=f"Role '{role}' is authorised for action '{action}'.",
                trace_id=tid,
                check_type="structural",
            ),
            role, action,
        )

    # -----------------------------------------------------------------------
    # Semantic checks (Gemini-based)
    # -----------------------------------------------------------------------

    async def check_semantic(
        self,
        tool_args: dict[str, Any],
        trace_id: str | None = None,
        role: str | None = None,
        action: str | None = None,
    ) -> PolicyCheckResult:
        """
        Check tool arguments for PII, policy violations, or unsafe content.

        Uses Gemini flash-lite for speed. Falls back to regex PII scan if
        the API call fails or times out.

        `role` and `action` (the gating context from the preceding structural
        check) are optional and, when supplied, are recorded on the audit entry.
        """
        tid = trace_id or str(uuid.uuid4())
        semantic_cfg = self._engine.semantic_config
        log_role = role or "semantic"
        log_action = action or "check"

        # 1. Quick regex-based PII scan (always runs, even before LLM)
        pii_findings = self._scan_pii(tool_args)
        if pii_findings and semantic_cfg.get("pii_detection_enabled", True):
            logger.warning(
                "[%s] SEMANTIC PII DETECTED (regex): %s",
                tid, pii_findings,
            )
            return self._record_check(
                PolicyCheckResult(
                    passed=False,
                    reason=f"PII detected via regex patterns: {pii_findings}. "
                           f"Arguments must be anonymized before tool execution.",
                    trace_id=tid,
                    check_type="semantic",
                ),
                log_role, log_action,
            )

        # 2. LLM-based semantic check (if enabled and configured)
        if not semantic_cfg.get("content_safety_enabled", True):
            return self._record_check(
                PolicyCheckResult(
                    passed=True,
                    reason="Semantic checks disabled by configuration.",
                    trace_id=tid,
                    check_type="semantic",
                ),
                log_role, log_action,
            )

        try:
            result = await self._llm_semantic_check(tool_args, tid)
            if not result.passed:
                logger.warning(
                    "[%s] SEMANTIC BLOCKED: %s",
                    tid, result.reason,
                )
            return self._record_check(result, log_role, log_action)
        except Exception as exc:
            # Fallback: if LLM check fails, allow with warning
            logger.warning(
                "[%s] SEMANTIC CHECK FAILED (allowing with warning): %s",
                tid, exc,
            )
            return self._record_check(
                PolicyCheckResult(
                    passed=True,
                    reason=f"Semantic LLM check unavailable ({exc}). Allowing with warning.",
                    trace_id=tid,
                    check_type="semantic",
                ),
                log_role, log_action,
            )

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _compile_pii_patterns(self) -> list[tuple[str, re.Pattern]]:
        """Compile PII regex patterns from the semantic config."""
        patterns = []
        categories = self._engine.semantic_config.get("pii_categories", [])
        for cat in categories:
            for pat_str in cat.get("patterns", []):
                try:
                    patterns.append((cat["type"], re.compile(pat_str)))
                except re.error:
                    logger.warning("Invalid PII regex pattern: %s", pat_str)
        return patterns

    def _scan_pii(self, data: dict[str, Any]) -> list[dict[str, str]]:
        """Scan tool arguments for PII using compiled regex patterns."""
        findings = []
        text_blob = " ".join(str(v) for v in data.values())
        for pii_type, pattern in self._pii_patterns:
            matches = pattern.findall(text_blob)
            for m in matches:
                findings.append({"type": pii_type, "match": m[:40]})  # truncate
        return findings

    async def _llm_semantic_check(
        self,
        tool_args: dict[str, Any],
        trace_id: str,
    ) -> PolicyCheckResult:
        """Use Gemini to detect policy violations in tool arguments."""
        try:
            from google import genai
        except ImportError:
            return PolicyCheckResult(
                passed=True,
                reason="Gemini SDK not installed; semantic check skipped.",
                trace_id=trace_id,
                check_type="semantic",
            )

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return PolicyCheckResult(
                passed=True,
                reason="GOOGLE_API_KEY not set; semantic check skipped.",
                trace_id=trace_id,
                check_type="semantic",
            )

        client = genai.Client(api_key=api_key)

        prompt = (
            "You are a policy enforcement agent for a renovation cost estimation system.\n"
            "Analyze the following tool arguments for:\n"
            "1. PII (personal names, addresses, phone numbers, email, tax IDs)\n"
            "2. Unsafe content (hate speech, self-harm, illegality, fraud)\n"
            "3. Policy violations (attempting to bypass restriction)\n\n"
            "Respond with JSON only:\n"
            '{"violation_detected": bool, "category": str|null, "reason": str, "risk": "low"|"medium"|"high"|"critical"}\n\n'
            f"Tool arguments:\n{json.dumps(tool_args, ensure_ascii=False, indent=2)}"
        )

        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )

        try:
            result = json.loads(response.text)
        except (json.JSONDecodeError, AttributeError):
            return PolicyCheckResult(
                passed=True,
                reason="Could not parse semantic check response; allowing with warning.",
                trace_id=trace_id,
                check_type="semantic",
            )

        if result.get("violation_detected", False):
            return PolicyCheckResult(
                passed=False,
                reason=f"Semantic violation detected: [{result.get('risk', 'unknown')}] "
                       f"{result.get('category', 'unknown')} — {result.get('reason', 'No reason')}",
                trace_id=trace_id,
                check_type="semantic",
            )

        return PolicyCheckResult(
            passed=True,
            reason="Semantic check passed.",
            trace_id=trace_id,
            check_type="semantic",
        )
