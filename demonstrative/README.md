# Demonstrative Modules

This folder contains architectural demonstrations that are **not wired into
the current single-user local deployment**. They were built as part of the
multi-agent expansion phase and illustrate production-grade patterns that
would be needed in a multi-tenant or zero-trust deployment.

Each module is self-contained, well-documented, and includes its own unit
tests. They can be re-integrated into the live system when the deployment
model requires them.

## What's Here

### `cache.py` — Multi-Level Tenant-Isolated Cache (`RenovAICache`)

Demonstrates an L1 (in-memory dict) + L2 (DiskCache) caching hierarchy
with per-tenant key prefixing. Designed for multi-user deployments where
one user's cached embeddings or query results must never leak into another
user's context.

**Architectural pattern:** Tiered cache with tenant isolation.

**Why it's not live:** The current deployment is single-user local — there
is no tenant boundary to enforce, and the in-process estimate cache in
`price_model.py` already handles repeat queries.

---

### `profile_hash.py` — Deterministic Apartment Profile Hashing

Generates a SHA-256 cache key from an apartment's profile (district, era,
area bucket, scope flags) for fuzzy deduplication of advisory reports.
Depends on `RenovAICache` above.

**Architectural pattern:** Content-addressable caching for LLM-heavy
advisory generation.

**Why it's not live:** Advisory reports are generated fresh each time in
the current Streamlit UI. Profile hashing would reduce LLM costs in a
high-traffic multi-user scenario.

---

### `agentic_identity.py` — SPIFFE-Compatible JIT Agent Identity

Mints Just-In-Time, ephemeral, downscoped identities for each sub-agent
(Gateway, Cost Estimator, Market Analyst, etc.) following the SPIFFE URI
format (`spiffe://renovai/<agent_id>/<session_id>`). Tokens are signed
with HMAC-SHA256 and carry ABAC claims (allowed actions, allowed resources).

**Architectural pattern:** Zero-trust agent identity with principle of
least privilege.

**Why it's not live:** The current deployment runs all handlers in a
single process with no inter-agent trust boundary. This pattern would be
required for a distributed multi-agent deployment where agents communicate
over a network.

---

### `vibe_trajectory.py` — 7-Pillar Safety Envelope Audit

Generates a structured audit report proving the agent stayed within its
"Safety Envelope" across 7 pillars: Intent Classification, Zero Ambient
Authority, Vibe Diff, Green Team (HITL), Agentic Identity, Audit Trail,
and Telemetry.

**Architectural pattern:** End-to-end safety envelope with immutable audit
trail.

**Why it's not live:** The trajectory tracker depends on the Agentic
Identity module above and on a distributed audit store. In the current
single-process deployment, the simpler `VibeDiff` and `AuditStore` classes
in `renovai/safety/` provide adequate explainability.

---

## Re-integration Path

To bring these modules back into the live system:

1. **Cache + Profile Hash:** Import `RenovAICache` and `ApartmentProfileHasher`
   in the handler layer; wrap `scope_matched_estimate()` and advisory report
   generation with cache-check-then-store logic.

2. **Agentic Identity:** Integrate `AgentIdentityManager` into `gateway.py`
   so each routing decision mints a JIT identity, and pass it to
   `PolicyService` for ABAC evaluation on every tool call.

3. **Vibe Trajectory:** Wire `VibeTrajectoryTracker` into the handler chain
   so each step records its pillar/status/duration, and generate the full
   audit report at the end of each request.

All four modules are designed for drop-in reintegration — no structural
changes to the modules themselves should be needed.
