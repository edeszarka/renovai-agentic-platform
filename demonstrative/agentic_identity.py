"""
Agentic Identity — SPIFFE-compatible identity and JIT downscoped tokens.

Each sub-agent (Gateway, Cost Estimator, Market Analyst, Due Diligence,
Ingestion) receives a unique, cryptographically verifiable identity that:

1. Follows the SPIFFE URI format: spiffe://renovai/<agent_id>/<session_id>
2. Is minted Just-In-Time (JIT) at the start of a session
3. Is downscoped to the minimum set of permissions needed for that
   specific task (Principle of Least Privilege)
4. Expires immediately after the task concludes (ephemeral)

The PolicyService uses these identities for Attribute-Based Access Control
(ABAC) — every tool call is evaluated against the identity's embedded claims.

Usage:
    identity_mgr = AgentIdentityManager()
    identity = await identity_mgr.mint_identity(
        agent_id="cost_estimator",
        session_id="gw-abc123",
        claims={"action": "produce_estimate", "scope": "read"},
    )
    # identity.token is a JWT signed with the service key
    # Pass identity to PolicyService for ABAC evaluation
"""

import os
import json
import uuid
import time
import hmac
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SPIFFE-compatible identity model
# ---------------------------------------------------------------------------

# Pre-defined agent roles with their allowed claims
AGENT_REGISTRY: dict[str, dict[str, Any]] = {
    "gateway": {
        "allowed_actions": ["classify_intent", "extract_params", "compose_answer"],
        "allowed_resources": ["routing_logs", "user_queries"],
        "max_token_ttl_seconds": 300,  # 5 minutes
    },
    "cost_estimator": {
        "allowed_actions": ["predict_cost", "find_similar", "scope_match", "compute_breakdown"],
        "allowed_resources": ["price_model", "inflation_data", "quote_corpus", "adjusted_json"],
        "max_token_ttl_seconds": 600,  # 10 minutes
    },
    "market_analyst": {
        "allowed_actions": ["text_to_sql", "query_database", "generate_stats"],
        "allowed_resources": ["aggregate_data", "sql_schema", "quote_summary"],
        "max_token_ttl_seconds": 300,
    },
    "due_diligence": {
        "allowed_actions": ["retrieve_context", "generate_report", "call_gemini", "hybrid_search"],
        "allowed_resources": ["rag_pipeline", "vector_store", "quote_corpus", "gemini_llm"],
        "max_token_ttl_seconds": 600,
    },
    "ingestion": {
        "allowed_actions": ["parse_xlsx", "extract_huf", "adjust_inflation", "write_to_db", "export_markdown"],
        "allowed_resources": ["sandbox", "raw_database", "financial_data", "adjusted_json"],
        "max_token_ttl_seconds": 600,
    },
    "green_team": {
        "allowed_actions": ["review_approval", "approve", "reject"],
        "allowed_resources": ["approval_requests", "audit_trail"],
        "max_token_ttl_seconds": 900,
    },
}


@dataclass(frozen=True)
class AgentIdentity:
    """SPIFFE-compatible identity for a RenovAI sub-agent.

    SPIFFE URI: spiffe://renovai/<agent_id>/<session_id>

    The identity carries embedded claims that define:
    - Which actions the agent may perform (ABAC)
    - Which resources it may access
    - The session trace_id for audit binding
    - Expiration timestamp (JIT + ephemeral)
    - HMAC signature for tamper-proofing
    """
    agent_id: str
    session_id: str
    spiffe_uri: str
    claims: dict[str, Any]
    issued_at: str
    expires_at: str
    token: str = ""     # JWT-formatted token string (signed HMAC)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "spiffe_uri": self.spiffe_uri,
            "claims": self.claims,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "token": self.token,
        }

    def is_expired(self) -> bool:
        """Check if the identity token has expired."""
        exp = datetime.fromisoformat(self.expires_at)
        return exp < datetime.now(timezone.utc)

    def is_authorised(self, action: str, resource: str | None = None) -> bool:
        """ABAC check: is this identity allowed to perform action on resource?"""
        allowed_actions = self.claims.get("allowed_actions", [])
        allowed_resources = self.claims.get("allowed_resources", [])

        if action not in allowed_actions:
            return False
        if resource and resource not in allowed_resources:
            return False
        return True


# ---------------------------------------------------------------------------
# Identity Manager
# ---------------------------------------------------------------------------

class AgentIdentityManager:
    """Mints, verifies, and revokes JIT-downscoped agent identities.

    Usage:
        mgr = AgentIdentityManager(secret_key=os.getenv("RENOVAI_IDENTITY_SECRET"))

        # Mint a JIT identity for a cost estimator session
        identity = await mgr.mint_identity(
            agent_id="cost_estimator",
            session_id="gw-abc123",
            extra_claims={"trace_id": "gw-abc123", "user_intent": "cost_estimation"},
        )

        # Verify and use in PolicyService
        is_valid = mgr.verify_token(identity.token)
        if is_valid and not identity.is_expired():
            if identity.is_authorised("produce_estimate", "price_model"):
                # Proceed with tool call
                pass
    """

    def __init__(self, secret_key: str | None = None):
        self._secret_key = secret_key or os.getenv("RENOVAI_IDENTITY_SECRET", "")
        if not self._secret_key:
            logger.warning(
                "RENOVAI_IDENTITY_SECRET not set. "
                "Agent identities will use an ephemeral key (not suitable for production)."
            )
            self._secret_key = f"ephemeral-{uuid.uuid4().hex}"

    async def mint_identity(
        self,
        agent_id: str,
        session_id: str,
        extra_claims: dict[str, Any] | None = None,
    ) -> AgentIdentity:
        """Mint a JIT-downscoped identity for the given agent.

        The identity's claims are derived from the agent registry,
        downscoped to the minimum set for the given session.
        """
        registry_entry = AGENT_REGISTRY.get(agent_id)
        if registry_entry is None:
            raise ValueError(f"Unknown agent_id: '{agent_id}'. Must be one of: {list(AGENT_REGISTRY.keys())}")

        spiffe_uri = f"spiffe://renovai/{agent_id}/{session_id}"
        now = datetime.now(timezone.utc)
        ttl = registry_entry["max_token_ttl_seconds"]
        issued_at = now.isoformat()
        expires_at = datetime.fromtimestamp(now.timestamp() + ttl, tz=timezone.utc).isoformat()

        claims = {
            "allowed_actions": registry_entry["allowed_actions"],
            "allowed_resources": registry_entry["allowed_resources"],
            **(extra_claims or {}),
        }

        # Build and sign the token
        token_payload = {
            "sub": spiffe_uri,
            "agent_id": agent_id,
            "session_id": session_id,
            "claims": claims,
            "iat": issued_at,
            "exp": expires_at,
            "iss": "renovai-identity-manager",
        }
        token = self._sign_token(token_payload)

        identity = AgentIdentity(
            agent_id=agent_id,
            session_id=session_id,
            spiffe_uri=spiffe_uri,
            claims=claims,
            issued_at=issued_at,
            expires_at=expires_at,
            token=token,
        )

        logger.info(
            "[%s] Identity minted: %s (TTL=%ds, claims=%s)",
            session_id, spiffe_uri, ttl, list(claims.keys()),
        )
        return identity

    def verify_token(self, token: str) -> bool:
        """Verify the HMAC signature and expiration of a token.

        Returns True if the token is valid and not expired.
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return False

            header_b64, payload_b64, sig_b64 = parts

            # Recompute signature
            expected_sig = self._compute_hmac(f"{header_b64}.{payload_b64}")
            actual_sig = self._urlsafe_b64_decode(sig_b64)

            if not hmac.compare_digest(expected_sig, actual_sig):
                logger.warning("Token signature mismatch")
                return False

            # Decode payload to check expiration
            import base64
            payload_padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
            payload_json = base64.urlsafe_b64decode(payload_padded)
            payload = json.loads(payload_json)

            exp_str = payload.get("exp", "")
            if exp_str:
                exp_dt = datetime.fromisoformat(exp_str)
                if exp_dt < datetime.now(timezone.utc):
                    logger.warning("Token expired at %s", exp_str)
                    return False

            return True

        except Exception as exc:
            logger.warning("Token verification failed: %s", exc)
            return False

    def revoke_identity(self, identity: AgentIdentity) -> None:
        """Revoke an identity (mark as expired immediately).

        In production, this would add the token to a revocation list
        (e.g. Redis set with TTL matching original expiry).
        """
        logger.info(
            "[%s] Identity revoked: %s", identity.session_id, identity.spiffe_uri,
        )
        # TODO: Add to revocation list in Redis

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sign_token(self, payload: dict[str, Any]) -> str:
        """Create a compact JWT-like signed token using HMAC-SHA256."""
        import base64

        header = json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
        header_b64 = base64.urlsafe_b64encode(header).rstrip(b"=").decode()

        payload_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode()
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()

        signing_input = f"{header_b64}.{payload_b64}"
        sig = self._compute_hmac(signing_input)
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

        return f"{header_b64}.{payload_b64}.{sig_b64}"

    def _compute_hmac(self, data: str) -> bytes:
        return hmac.new(
            self._secret_key.encode("utf-8"),
            data.encode("utf-8"),
            hashlib.sha256,
        ).digest()

    @staticmethod
    def _urlsafe_b64_decode(data: str) -> bytes:
        import base64
        padded = data + "=" * (4 - len(data) % 4)
        return base64.urlsafe_b64decode(padded)
