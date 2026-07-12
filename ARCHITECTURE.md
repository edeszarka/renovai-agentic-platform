# Architecture

## Entry Points

RenovAI has three distinct entry points, each with its own routing scheme:

### 1. Streamlit App (`app/streamlit_app.py`)

The primary user-facing interface. Two tabs, each calling handlers **directly**:

```
Tab 1: "Vevői Felkészítő" (Buyer Preparation)
  → handle_expert_interview(params, policy, registry, trace_id)

Tab 2: "Felújítási Tervező" (Renovation Planner)
  → handle_construction_planning(params, policy, registry, trace_id)
```

Both tabs import the handler, instantiate `PolicyService` and `SkillRegistry`,
and call the handler with a `trace_id` — **bypassing gateway.py entirely**.

### 2. Gateway Agent (`orchestrator/gateway.py`)

A two-tier intent classifier (keyword fast-path → Gemini LLM fallback) that
classifies Hungarian natural-language questions into intents and extracts
structured parameters. It is the intended sole entry point for a multi-agent
deployment:

```
User question → Gateway.classify()
  → Tier 1: keyword matching (free, instant)
  → Tier 2: Gemini structured output (accurate, costs a Gemini call)
  → RoutingDecision { primary_intent, secondary_intents, parameters, confidence }
  → resolve_handler(intent) → handler(params)
```

The Gateway supports a `COMBINED` intent for questions spanning multiple
topics (e.g. "how much does it cost AND what should I watch out for?").

### 3. ADK/MCP Agent (`app/agent.py`)

A Google ADK agent that connects to the MCP server over SSE. Uses a simpler
3-intent routing scheme delegated to Gemini — no keyword tier, no parameter
extraction, no `resolve_handler()`.

```
User question → Gemini agent (system prompt)
  → MCP tools: estimate_renovation_cost, get_due_diligence_advice, query_renovation_market
```

### Entry Point Comparison

| Aspect | Streamlit (live) | Gateway (intended) | ADK/MCP |
|---|---|---|---|
| Routing | None — hardcoded per tab | Two-tier keyword + LLM | LLM-only via system prompt |
| Parameter extraction | Manual (form fields) | Regex + LLM extraction | LLM extraction |
| Intent coverage | 2 intents (Tab 1, Tab 2) | 7 intents + COMBINED | 3 MCP tools |
| Confidence gating | No | Yes (< 0.7 → clarification) | No |
| Trace logging | Basic trace_id | Full RoutingDecision | ADK built-in |

---

## Known Technical Debt

### Gateway is not exercised by real Streamlit usage

The two-tier classifier in `gateway.py` was built for the multi-agent
expansion phase but is **not called by the Streamlit tabs**. The tabs
construct params from form fields and call handlers directly. This means:

- The keyword classifier and Gemini fallback in `gateway.py` are only
  exercised by `scripts/load_test.py` and `renovai/evals/red_team_tests.py`.
- The `COMBINED` intent, clarification flow, and confidence gating are
  unreachable from the Streamlit UI.

**Root cause:** The project evolved through different course phases — a
Vibe Coding capstone (Streamlit + direct handler calls) that became the
live deployment, and a multi-agent expansion (Gateway + PolicyService +
SkillRegistry) that built infrastructure for a richer routing layer but
was never plugged into the existing UI.

**Planned fix:** Consolidate to a single dispatch layer — have the
Streamlit tabs route through `Gateway.classify()` instead of calling
handlers directly. This would activate keyword classification, confidence
gating, and parameter extraction for all user queries.

### ADK agent uses a separate, simpler routing scheme

The `app/agent.py` ADK agent has its own 3-intent routing via Gemini system
prompt, completely independent of `gateway.py`. The MCP server exposes only
3 tools (cost estimation, due diligence, market query) — it does not expose
expert interview or construction planning.

**Planned fix:** Extend the MCP server to expose all 5 handler capabilities
as tools, and have the ADK agent use them instead of its own LLM-based
routing.

---

## Demonstrative Modules

Several modules in `renovai/safety/` are architectural demonstrations that
are not wired into the current single-user local deployment. They have been
relocated to `demonstrative/` with full documentation:

- **`cache.py`** — Multi-level tenant-isolated cache (L1 memory + L2 disk)
- **`profile_hash.py`** — Deterministic apartment profile hashing for cache keys
- **`agentic_identity.py`** — SPIFFE-compatible JIT agent identity with HMAC-SHA256
- **`vibe_trajectory.py`** — 7-pillar safety envelope audit report

See `demonstrative/README.md` for architectural rationale and re-integration
path.
