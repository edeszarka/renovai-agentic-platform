# RenovAI 2.0 — Multi-Agent Security & Observability Architecture

> Branch: `renovai_2_reconstruction` — 13 commits beyond `capstone-mcp`, built on the legacy RenovAI ingestion/ML/RAG/Streamlit foundation.

---

## 1. System Architecture (v2.0 Transformation)

The legacy monolithic orchestrator has been refactored into a **Spec-Driven, Multi-Agent system with custom handler dispatch and an integrated Safety Harness**. The architecture follows 7 security pillars:

```
 User Query (Hungarian)
      │
      ▼
┌─────────────────────────────────────────────────────┐
│              Gateway Agent (gateway.py)              │
│  Two-tier intent classification:                     │
│    1. Keyword matching (fast, ≥0.7 confidence)       │
│    2. Gemini structured JSON (accurate, <0.7)        │
│  Intents: cost_estimation, market_query,             │
│    due_diligence, ingestion, EXPERT_INTERVIEW,       │
│    CONSTRUCTION_PLANNING, combined, unknown          │
│  Returns: RoutingDecision with trace_id              │
└──────────────┬──────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│              PolicyService (policy_service.py)        │
│  Two-layer safety harness:                            │
│    1. Structural checks (YAML RBAC, sync, no LLM)    │
│    2. Semantic checks (Gemini flash-lite + regex)    │
│    3. ABAC check_abac() (SPIFFE-inspired identity)            │
│  Every check logged with trace_id                    │
└──────────────┬──────────────────────────────────────┘
               │
       ┌───────┴────────┬──────────┬──────────┐
       ▼                ▼          ▼          ▼
┌──────────────┐ ┌────────────┐ ┌────────┐ ┌────────────────┐
│ Cost Estimator││Market      ││Due     ││ Expert         │
│ (handler)     ││Analyst     ││Diligence││ Interviewer    │
│ price_model   ││text_to_sql ││RAG+    ││ (Tab 1)        │
│ + inflation   ││+ aggregate ││Gemini  ││ red-flag       │
│               ││stats       ││report  ││ identification │
└──────────────┘ └────────────┘ └────────┘ └────────────────┘
                                        ┌────────────────────┐
                                        │Construction Planner│
                                        │ (Tab 2)           │
                                        │ 7-phase sequencing│
                                        └────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│           Vibe Diff Engine (vibe_diff.py)            │
│  Low-temperature LLM → plain-English "Vibe Diff"     │
│  Explains WHY the agent reached its conclusion       │
│  Stored separately from main report (context hygiene)│
└──────────────┬──────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│           Green Team Service (green_team.py)          │
│  HITL gate for high-stakes actions:                   │
│    - confidence < 0.7 → ApprovalRequest              │
│    - semantic risk (high/critical) → ApprovalRequest │
│  generate_approval_request() → human review           │
│  approve() / reject() with timestamp                  │
└──────────────┬──────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│           Immutable Audit Trail (audit_trail.py)      │
│  Every financial advice bound to trace_id +          │
│  human_approval_timestamp                            │
│  Append-only JSONL → BigQuery export                 │
└─────────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│           Custom Telemetry (OTEL-compatible)          │
│  agent.think spans for reasoning                     │
│  agent.tool spans for execution latencies            │
│  PII-sanitized attributes                            │
└─────────────────────────────────────────────────────┘
```

---

## 2. 7-Pillar Security Architecture

### Pillar 1: Intent Classification (Gateway)
- **File**: `orchestrator/gateway.py` (380 lines)
- **8 intents** (6 working + 2 meta): `cost_estimation`, `expert_interview`, `construction_planning`, `market_query`, `due_diligence`, `ingestion`, plus meta-intents `combined`, `unknown`
- **Two-tier classification**:
  - Tier 1 (fast path, no LLM): keyword matching in `KEYWORD_INTENT_MAP` — 14 keyword groups covering Hungarian renovation vocabulary. Confidence ≥ 0.7 routes immediately.
  - Tier 2 (accurate path): Gemini structured JSON output with `response_mime_type: application/json`, temperature 0.1. Falls back gracefully to keyword if API key missing or call fails.
- **Parameter extraction**: regex-based extraction of district (kerület), area (nm), rooms, building_type, building_era, scope_flags (plumbing, electrical, flooring, demolition, slag, built-in shower), wall_condition (wallpaper), floor_construction, sequencing flag.
- **Combined intent detection**: if multiple intents match, `primary_intent` = `"combined"`, all matched intents listed in `secondary_intents`.
- **Clarification flow**: if confidence < 0.7 after LLM call, `clarification_needed=True` with a Hungarian clarification question offering all 6 intent options.

### Pillar 2: Zero Ambient Authority (PolicyService)
- **File**: `orchestrator/policy_service.py` (388 lines)
- **Two-layer safety harness**:
  1. **Structural checks** (synchronous, YAML-driven): RBAC via `policies.yaml` — 5 original roles + 2 new (expert_interviewer, construction_planner). Each role has `can_access`, `can_execute`, `cannot_access` lists. 11 structural rules enforce action-specific constraints.
  2. **Semantic checks** (asynchronous, Gemini + regex): PII detection via 5 compiled regex patterns (address, phone, email, personal_name, tax_id). Content safety via Gemini flash-lite with configurable timeout (5s).
- **ABAC via Agentic Identity**: `check_abac(identity, action, resource)` verifies SPIFFE-inspired token expiry + action/resource authorization.
- **Every check logged** with trace_id for full auditability.

### Pillar 3: Agentic Identity (SPIFFE-inspired + JIT Tokens)
- **File**: `renovai/safety/agentic_identity.py` (200+ lines)
- **SPIFFE-format URI**: `spiffe://renovai/<agent_id>/<session_id>` (custom HMAC JWT, not SPIRE-compliant)
- **6-agent registry**: gateway, cost_estimator, market_analyst, due_diligence, ingestion, green_team — each with `allowed_actions`, `allowed_resources`, `max_token_ttl_seconds`.
- **JIT minting**: `mint_identity()` creates HMAC-SHA256 signed JWT with embedded claims, TTL-enforced (5-10 min depending on role).
- **Downscoping**: identity claims are the MINIMUM set for the specific task — no token has more permissions than needed.
- **Ephemeral**: tokens expire automatically. `revoke_identity()` marks for revocation list.
- **ABAC enforcement**: `AgentIdentity.is_authorised(action, resource)` checked by PolicyService before every tool call.

### Pillar 4: Vibe Diff (Explainability)
- **File**: `renovai/safety/vibe_diff.py` (145 lines)
- **Purpose**: intercept raw sub-agent output (Expert Interviewer, Construction Planner, Cost Estimator, Due Diligence) and generate a plain-English "Vibe Diff" explaining the causal chain.
- **Low-temperature LLM**: Gemini 2.0 Flash Lite at temperature 0.15 — no embellishment, factual only.
- **Schema**: `VibeDiff` dataclass with `vibe_id`, `trace_id`, `source`, `explanation_hu`, `explanation_en`, `key_drivers`, `before_snapshot`, `after_snapshot`.
- **Context hygiene**: Vibe Diff stored as SEPARATE object (not embedded in the main report). Report references it by `vibe_id`.
- **Fallback**: if LLM call fails, returns graceful Hungarian fallback message.
- **Requirement doc**: `docs/vibe_diff_requirement.md` — when Vibe Diff is mandatory/optional (confidence thresholds, structural changes, rare building types).

### Pillar 5: Green Team (Human-in-the-Loop)
- **File**: `renovai/safety/green_team.py` (190+ lines)
- **Trigger conditions**:
  - `confidence < 0.7` (threshold configurable)
  - `semantic_risk in ("high", "critical")`
- **ApprovalRequest**: structured payload bundling `user_intent`, `proposed_action`, `proposed_response`, `VibeDiff`, `confidence_score`, `risk_level`, `trigger_reason`.
- **Lifecycle**: `pending` → `approved` | `rejected` with `reviewed_by`, `reviewed_at`, `reviewer_notes`.
- **Risk resolution**: combines confidence and semantic risk into single level (critical > high > medium > low).

### Pillar 6: Immutable Audit Trail
- **File**: `renovai/safety/audit_trail.py` (130+ lines)
- **AuditEntry**: immutable dataclass bound to `trace_id`, `action_type`, `user_intent`, `confidence_score`, `vibe_diff_id`, `human_approval_timestamp`, `structural_check_passed`, `semantic_check_passed`.
- **AuditStore**: append-only JSONL file (`data/audit_trail.jsonl`). `append()` is the only write — no delete, no update.
- **BigQuery export**: `export_to_bigquery()` returns rows ready for streaming insert into a partitioned table.
- **Entry factory**: `build_entry()` auto-generates `entry_id` (ae-xxxx) for convenience.

### Pillar 7: Telemetry (OpenTelemetry-compatible Custom Tracer)
- **File**: `renovai/safety/telemetry.py` (170+ lines)
- **Span types**: `agent.think` (reasoning steps) and `agent.tool` (execution latencies), following OpenTelemetry semantic conventions.
- **Span lifecycle**: `think_span()` / `tool_span()` context managers — auto-record start/end time, duration_ms, status (ok/error), error_message.
- **PII sanitization**: 5 regex patterns (IP, email, ID, phone, Hungarian address) applied to all span attributes before storage.
- **Export**: `export()` returns all spans as dicts. `register_exporter()` for custom exporters. `flush()` pushes to exporters and clears buffer.
- **Singleton**: `get_tracer()` returns module-level `RenovAITracer` singleton.

---

## 3. Multi-Agent Partitioning (Custom Handler Routing)

### Sub-Agent: Expert Interviewer (Tab 1)
- **Handler**: `handle_expert_interview` in `orchestrator/handlers.py` (120+ lines)
- **Red-flag identification priority** (6 levels):
  1. **CRITICAL**: Kohósalak in pre-1960 steel-beam floors (3 000-5 000 Ft/nm removal)
  2. **HIGH**: Aluminium wiring in 1965-1985 panel buildings (3 000-5 000 Ft/nm rewiring)
  3. **HIGH**: Foundation settlement in pre-1920 brick buildings (structural engineer required)
  4. **MEDIUM**: Sawdust wallpaper requiring scraping surcharge
  5. **MEDIUM**: Stack ventilation blockage in panel buildings
  6. **LOW**: Original wooden window frames
- **Output contract**: `red_flags[]` (with risk/title/detail/estimated_cost/action), `overall_risk` (CRITICAL/HIGH/MEDIUM/LOW), `summary_hu`, `inspection_checklist[]`, `questions_for_seller[]`, `recommended_experts[]`, `confidence{}`.
- **Confidence scoring**: 0.9 (no flags), 0.8 (1-2 flags with high-certainty), 0.75 (3+ flags with era defaults).
- **Cascading red-flag logic**: pre-1920 era activates födém megerősítés (floor reinforcement) at ~402k mat + 320-420k labor; 1920-1965 betontálcás födém triggers additional structural checks.

### Sub-Agent: Construction Planner (Tab 2)
- **Handler**: `handle_construction_planning` in `orchestrator/handlers.py` (~350 lines)
- **Full/Partial renovation toggle**: Full mode enables all 7 phases + infrastructure minimums; Partial mode skips to user-selected phases only.
- **Cascading dependency logic**:
  - Pre-1960 building + floor/demolition work → auto-activates aggregated **Slag Chain** block (3 sub-items: removal labor 1.3-2M HUF, EPS insulation 700-850k HUF, concrete leveling 6-9k HUF/sqm)
  - Pre-1920 + steel beam floor → additional floor reinforcement phase
  - Sawdust wallpaper → scraping + Q3 replastering surcharge
  - Built-in shower → mandatory cement-based waterproofing
- **Infrastructure minimums** (Full renovation only): electrical baseline 300k HUF, gas/heating baseline 800k HUF, logistics surcharge 15% of total labor
- **Phase sequencing**: Phase 0 (Slag Chain) → Demolition → Masonry → Rough-in → Plastering → Flooring → Painting; deviations produce warnings.
- **Mandatory Hungarian Vibe Diff**: generated in every plan; if total > 10M HUF → lists Hidden Technological Chains and auto-expands in UI.
- **Total estimate**: low/mid/high HUF ranges aggregated across all phases including infrastructure minimums.

### Load Testing (Gateway → Sub-Agent → Safety Harness)
- **File**: `scripts/load_test.py` (170+ lines)
- Simulates parallel multi-agent sessions with configurable concurrency (`--concurrency`) and request count (`--requests`).
- Records per-stage latencies: gateway classification, policy structural check, policy semantic check, total.
- Reports p50/p95/p99/avg/min/max for each stage.
- Uses mocked Gateway and PolicyService with realistic simulated latencies (10ms classify, 2ms structural, 5ms semantic).

---

## 4. Spec-Driven Development (Gherkin)

### Spec Files (5 feature files, 19 scenarios)

| File | Scenarios | Coverage |
|------|-----------|----------|
| `specs/buyer_interview.feature` | 5 | Expert Interviewer: slag, aluminium wiring, foundation, combined intent, low-confidence Vibe Diff |
| `specs/renovation_planner.feature` | 6 | Construction Planner: full sequence, slag removal, wallpaper scraping, waterproofing, Vibe Diff gate, Full vs Partial toggle |
| `specs/features/renovation_logic.feature` | 3 | Floor reinforcement, sawdust wallpaper surcharge, cement vs dispersion waterproofing |
| `specs/cost_estimation.feature` | 5 | HUF parsing, inflation factors, Gateway routing, combined intent, sandbox failure |

### Key Gherkin Patterns
- **Vibe Diff gate**: "The system MUST generate a Vibe Diff explaining the reasoning" + "presented for human approval before the final plan is emitted"
- **Confidence signaling**: "Sub-agents must return a confidence score; if < 0.7, the Gateway must flag it for manual Vibe Diff review"
- **Async communication**: "Gateway MUST detect both expert_interview and cost_estimation markers" + "MUST route to both sub-agents" + "MUST merge both responses into a single Hungarian answer"

---

## 5. Multi-Level Caching & Performance

### Caching Layer
- **File**: `renovai/safety/cache.py` (190+ lines)
- **Two tiers**:
  - L1: In-memory dict (sub-millisecond, process-local, max 512 entries with LRU eviction)
  - L2: DiskCache (persistent, TTL-configurable, shared across workers)
- **Tenant isolation**: every key prefixed with `tenant_id:` — no data leakage between sessions.
- **Specialized methods**: `get_embedding`/`set_embedding`, `get_market_query`/`set_market_query`, generic `get`/`set`.

### Apartment Profile Hashing
- **File**: `renovai/safety/profile_hash.py` (100+ lines)
- **Hash algorithm**: SHA-256 of `{district, building_era, area_bucket (rounded to 5), scope_flags (sorted)}`.
- **TTL**: 7 days default (configurable).
- **Fuzzy matching**: area rounded to nearest 5 sqm so 54 and 55 resolve to same cache key.
- **Cache busting**: `invalidate(profile)` removes specific entry.

### Cache Performance
| Tier | Access Time | Capacity | Persistence |
|------|-------------|----------|-------------|
| L1 (memory) | < 1 µs | 512 entries | Process lifetime |
| L2 (DiskCache) | < 5 ms | Disk-bound | Configurable TTL |
| Cache miss | LLM call cost | N/A | N/A |

---

## 6. Feedback & Retraining Loop

- **File**: `renovai/api/feedback.py` (160+ lines)
- **FastAPI router** at `/api/feedback` with 4 endpoints:
  - `POST /buyer` — BuyerFeedback (estimated vs actual costs, accuracy_rating, comments)
  - `POST /advisor` — AdvisorFeedback (red flag accuracy, missed flags, overall_rating)
  - `POST /batch` — bulk ingestion of mixed feedback types
  - `GET /export/bigquery` — returns all feedback as BigQuery-compatible row dicts
- **Storage**: local JSONL buffer (`data/feedback/{buyer_feedback,advisor_feedback}.jsonl`), designed for periodic BigQuery streaming insert.
- **Retraining signal**: feedback data updates priors in ConfidenceModel (if estimate was off, confidence decreases for that profile type).

---

## 7. Adversarial Red-Teaming

- **File**: `renovai/evals/red_team_tests.py` (220+ lines)
- **11 attack vectors** across 4 categories:
  - **Jailbreak (3)**: DAN roleplay, system prompt override in Hungarian, simulated persona with authority claim
  - **PII Extraction (3)**: direct PII request, camouflaged "anonymization study" pretext, address gathering through innocent question
  - **Financial Bypass (3)**: below-minimum-viable-cost request, scope flag manipulation, inflation date manipulation
  - **Poisoned RAG (2)**: fake source contradicting golden dataset, request to inject fabricated quote
- **Each case**: has `expected_block` (True/False) and `expected_risk` (low/medium/high/critical) for automated pass/fail assessment.
- **Test runner**: `RedTeamRunner.run_all()` executes Gateway classification + PolicyService structural + semantic checks against each case.
- **Report**: per-category pass rates, individual test results with reasoning.

---

## 8. SBOM & Binary Authorization

- **File**: `scripts/generate_sbom.py` (200+ lines)
- **SBOM format**: SPDX 2.3 with package-level SHA-256 and purl references.
- **Known-good manifest**: 35 blessed packages with exact versions from `pyproject.toml`.
- **Blocked packages**: 13 hallucinated/incorrect packages (requests, beautifulsoup4, flask, django, tensorflow, torch, psycopg2, etc.).
- **Binary Authorization gate**: `verify_manifest()` checks installed against known-good — rejects on version mismatch, blocked package, or unknown package (with `--fail-on-unknown`).

---

## 9. Production Deployment Configuration

### Cloud Run
- **Dockerfile** (`deployment/cloud_run/Dockerfile`): multi-stage, Python 3.11-slim, nonroot user, no setuid binaries (gVisor-compatible).
- **Dockerfile.gvisor**: distroless nonroot base, no shell, no package manager.
- **Service YAML** (`deployment/cloud_run/service.yaml`): gen2 execution environment, Binary Authorization policy, CPU 2, memory 4Gi, concurrency 8, secrets from Secret Manager, health/liveness probes.
- **Health check** (`deployment/cloud_run/health.py`): verifies Python runtime, env vars, data directory writability.

### Vertex AI Agent Runtime
- **Config** (`deployment/vertex_ai/agent_runtime_config.yaml`): source-based deployment (no Dockerfile), CPU 2 / memory 4Gi / 2 workers, secrets, observability (Cloud Trace + BigQuery analytics), gVisor sandbox with syscall allow/deny lists, SPIFFE-inspired agent identity with ABAC.

### Container Security
- gVisor gen2 execution environment (kernel-level isolation)
- No setuid/setgid binaries
- Distroless option available
- Secret Manager for all credentials (no env-var secrets)
- Binary Authorization gate in CI/CD pipeline

---

## 10. Skill Registry (SkillRegistry)

- **File**: `orchestrator/skill_registry.py` (~120 lines)
- **Purpose**: loads skill metadata from disk-based skill directories under a configurable root.
- **Construction**: `SkillRegistry()` takes no args; defaults to `.agent/skills`. Must call `.load_all()` after instantiation.
- **Methods**: `scan_all()`, `find_by_intent()`, `load_instructions()`, `get_script_path()`, `get_reference_path()`.
- **Note**: Skill directories were previously defined under `.agent/skills/` with SKILL.md files and reference data, but have been removed in the current branch. Core handler logic now lives directly in `orchestrator/handlers.py` with skill metadata loaded dynamically when directories are present.

---

## 11. File Inventory (renovai_2_reconstruction)

### New Modules Created

| Path | Lines | Purpose |
|------|-------|---------|
| `orchestrator/gateway.py` | 380 | Gateway — two-tier intent classification |
| `orchestrator/handlers.py` | ~1000 | 6 async handlers (cost, ingest, market, DD, interview, planning) + cascading cost + red-flag logic + resolver |
| `orchestrator/policy_service.py` | 388 | Two-layer safety harness + ABAC |
| `orchestrator/skill_registry.py` | ~120 | Type-safe skill loader with progressive disclosure |
| `renovai/safety/__init__.py` | ~15 | Safety package exports |
| `renovai/safety/vibe_diff.py` | 145 | Vibe Diff Engine |
| `renovai/safety/green_team.py` | ~190 | HITL gate service |
| `renovai/safety/telemetry.py` | ~170 | Custom spans (OpenTelemetry-compatible) |
| `renovai/safety/audit_trail.py` | ~130 | Immutable audit trail |
| `renovai/safety/cache.py` | ~190 | Two-tier caching (L1 mem + L2 DiskCache) |
| `renovai/safety/profile_hash.py` | ~100 | Profile-based report caching |
| `renovai/safety/agentic_identity.py` | ~200 | SPIFFE-inspired JIT token minting + ABAC |
| `renovai/safety/vibe_trajectory.py` | ~200 | 7-pillar safety envelope audit |
| `renovai/models/confidence.py` | 82 | ConfidenceModel Pydantic |
| `renovai/api/feedback.py` | ~160 | Feedback controller (buyer + advisor) |
| `renovai/evals/red_team_tests.py` | ~220 | 11 adversarial test cases |
| `policies.yaml` | 120+ | RBAC rules, 7 roles, 11 structural checks |
| `specs/buyer_interview.feature` | ~50 | 5 Gherkin scenarios |
| `specs/renovation_planner.feature` | ~70 | 6 Gherkin scenarios |
| `scripts/load_test.py` | ~170 | Load testing script |
| `scripts/generate_sbom.py` | ~200 | SBOM + Binary Authorization |
| `deployment/cloud_run/Dockerfile` | ~35 | Production container |
| `deployment/cloud_run/Dockerfile.gvisor` | ~15 | Distroless gVisor container |
| `deployment/cloud_run/service.yaml` | ~60 | Cloud Run service definition |
| `deployment/cloud_run/health.py` | ~50 | Health check server |
| `deployment/vertex_ai/agent_runtime_config.yaml` | ~60 | Agent Runtime config |
| `requirements.txt` | 35 | Pinned dependencies |
| `.dockerignore` | 15 | Docker build context |
| `docs/vibe_diff_requirement.md` | ~80 | Vibe Diff spec |
| `docs/TECHNICAL_SUMMARY_2.md` | (this file) | Complete architecture summary |

### Total: ~4,700+ new lines across 30+ files.

---

## 12. Confidence Model

- **File**: `renovai/models/confidence.py` (82 lines)
- **Pydantic model**: `score` (0.0-1.0), `reasoning` (human-readable), `sources_count` (int).
- **Thresholds**: `is_reliable()` = score ≥ 0.7, `is_speculative()` = score < 0.4.
- **Hungarian describe()**: returns localized label with source count.
- **Integration**: every handler returns `confidence` dict in output; Gateway uses it for Vibe Diff triggering; Green Team uses it for HITL intervention.

---

## 13. Data Limitations

- **Corpus**: ~30 historical quotes only. Any estimate with < 3 similar cases triggers a data limitation warning: "Figyelem: az adatbázis mindössze 30 idézetet tartalmaz".
- **Source 5**: Hungarian renovation Q&A guide with expert answers and HUF price lists — single source of truth for all pricing data in skill references.
- **Building era defaults**: if era unknown, defaults to 1960-1990 with flag `"Feltételezett építési korszak: 1960-1990"`.
- **Inflation**: KSH CPI quarterly series with separate labor/materials indices. If no data for target_date, uses nearest available quarter.

---

## 14. Testing Overview

| Test Layer | Tool | Coverage |
|------------|------|----------|
| Unit (handlers, gateway, policy) | pytest + asyncio | Gateway keyword routing, handler output contracts, structural pass/deny |
| Integration (ABAC, Vibe Diff) | pytest + mocks | Identity token validation, Vibe Diff LLM fallback |
| Adversarial (red-team) | RedTeamRunner | 11 jailbreak/PII/financial/poisoned-RAG cases |
| Load test | `scripts/load_test.py` | Parallel sessions, per-stage latency percentiles |
| Gherkin specs | Manual review | 19 scenarios across 5 feature files |
| SBOM gate | `generate_sbom.py --gate` | 35 known-good packages, 13 blocked |
| Vibe Trajectory audit | `VibeTrajectoryTracker` | 7-pillar Safety Envelope pass/fail |
| Streamlit UI | Manual | 2-tab interface (Buyer Preparation, Renovation Planner) |
