# PHASE_0_SUMMARY.md — Streamlit-canonical refactor

Branch: `refactor/phase-0-streamlit-canonical` (base: `main` = `3d84872`)

## Commit list (7)

| Commit | Scope | What changed |
|---|---|---|
| `8e858e5` | chore(baseline) | `NOTES_BASELINE.md` recording the pre-refactor test baseline. |
| `1b79397` | docs(architecture) | `docs/ARCHITECTURE.md` declares `app/streamlit_app.py` + `orchestrator/handlers.py` as the canonical live path and lists every other entry point as legacy with a one-sentence purpose each. |
| `2b2370c` | refactor(legacy) | Non-canonical entry points moved under `legacy/` (renovai_api/, ADK agent, ADK stretch-goal agent, mcp_server/, cloud_run/, root Dockerfile), with `legacy/README.md` and `legacy/requirements-legacy.txt` (google-adk, google-cloud-aiplatform, google-cloud-logging). `requirements.txt` trimmed to the Streamlit+handlers deploy target; live-path imports of the three moved packages verified absent. |
| `50c2b20` | feat(handlers) | **Task 3 — Green Team gate.** `handle_cost_estimation`, `handle_due_diligence`, `handle_expert_interview` now call `GreenTeamService.evaluate()` with their existing confidence/risk signals and surface `needs_intervention` (+ `green_team` review metadata) in the response payload. Tab 1 UI shows a "needs review" warning state. Tests added (`tests/test_green_team_gate.py`): **3 failed before (KeyError: 'needs_intervention'), 3 passed after.** |
| `85edd24` | feat(policy) | **Task 4 — Audit trail.** `PolicyService` appends an `AuditStore` entry (trace_id, role, action, resource, decision, timestamp) after every `check_structural` / `check_semantic` resolution (allow and deny). Handlers pass role/action context into `check_semantic`. `AuditEntry` gains optional `role/action/resource/decision` fields (backward-compatible). Also fixed a pre-existing serialization bug in `AuditStore.append()` (see notes). Tests added (`tests/test_audit_trail_policy.py`, 3 tests). |
| `ef39d6f` | feat(structural) | **Task 5 — Pydantic guard.** `CostBreakdownOutput` in `renovai/predictor/structural_cost.py` with `chain_ids_applied: list[str]`, `chain_cost_huf: int`, and a `model_validator` that raises a clear `ValidationError` when a scope matching a known `CHAIN_RULES` trigger (pre-1970 + flooring/windows_doors) is missing from the breakdown. Wired live into both handlers' chain block via `build_chain_breakdown`. Tests added (`tests/test_cost_breakdown_guard.py`, 5 tests, incl. tamper case). |
| `4503aab` | refactor | **Task 6 — cleanup on canonical path only.** Completed return hint on `_phase_from_scope`, added `PolicyService.__init__` docstring. Confirmed no in-function local imports of safety/policy modules remain (DI is parameter-level, extending the existing `policy_service`/`green_team_service` pattern). `legacy/` untouched. |

## Test status

| Stage | Result |
|---|---|
| Baseline (NOTES_BASELINE.md, pre-move) | 1 failed, 127 passed, 4 skipped, 2 errors (legacy-path: ADK stream, Vertex billing, stale `test_price_model.py`) |
| After tasks 1–2 | 126 passed, 4 skipped, 0 failed (legacy tests quarantined out of `tests/`) |
| After task 3 | 129 passed, 4 skipped |
| After task 4 | 132 passed, 4 skipped |
| After task 5 | 137 passed, 4 skipped |
| **Final (after task 6)** | **137 passed, 4 skipped, 0 failed** |

Task-3 test before/after (fail → pass on the same assertions): `needs_intervention` key absent (KeyError) → present with `True` for low-confidence cost estimation and CRITICAL-risk expert interview; `False` for low-risk input.

## End-to-end audit-trail example (Task 4)

A real Tab-1 request (`handle_expert_interview`) through a real `PolicyService` + `AuditStore` (temp JSONL):

```
request trace_id: st-ceb7ccbcb657
  AUDIT | st-ceb7ccbcb657 | structural_check | expert_interviewer / assess_risk / - | allow | 2026-08-11T20:23:39.280089+00:00
  AUDIT | st-ceb7ccbcb657 | semantic_check   | expert_interviewer / assess_risk / - | allow | 2026-08-11T20:23:39.280993+00:00
jsonl file lines on disk: 2 | read_all retrievable: 2
```

Persistence target: `data/audit_trail.jsonl` (JSONL, one compact JSON blob per line; verifiable via `AuditStore.read_all()`).

## Intentionally left untouched

- Do-not-touch list: `orchestrator/handlers.py` existing business logic (estimate math, prompts, return contracts), `price_model.py` / `structural_cost.py` `CHAIN_RULES` calculation internals, `renovai/rag/*`, `red_team_tests.py`, `policies.yaml` permissions, Tab 1/Tab 2 UI layout (only added wiring/state below the surface).
- Pre-existing failures recorded in `NOTES_BASELINE.md` were **not** fixed (per-refactor rule); the two legacy-path/Vertex-billing items and stale `test_price_model.py` no longer run because they were quarantined under `legacy/`.
- `handle_construction_planning` was intentionally **not** given a Green-Team gate — Task 3 scoped the gate to `cost_estimation`, `due_diligence`, `expert_interview` only.

## Open questions / decisions made under uncertainty (TODO-flagged in code)

1. **audit_trail.py schema extension (Task 4).** The per-check requirement (role, action, resource, decision) could not be represented by `AuditEntry`'s existing request-level fields without stuffing them into `action_type`/`agent_response_summary`. I extended `AuditEntry` with four **optional** fields (`role`, `action`, `resource`, `decision`, all default `None`) so old entries still load (`read_all` unaffected) — a backward-compatible extension rather than a redesign. Flagged in code comment.

2. **audit_trail.py pre-existing JSONL bug.** `AuditStore.append()` wrote `entry.to_json()` which uses `indent=2`, so each entry spanned multiple lines and `read_all()`/`count()` could never parse it back — contradicting the documented "one JSON per line" format. Fixed by writing compact single-line JSON in `append()`; `to_json()` is unchanged for display. This was required for Task 4's "retrievable trail" proof, not a redesign.

3. **`cost_estimator/produce_estimate` is denied by real `policies.yaml`.** Per the actual role config, `produce_estimate` belongs to `construction_planner`; `cost_estimator`'s `can_execute` is `[predict_cost, find_similar, scope_match, compute_breakdown]`. So a real `PolicyService` would deny `handle_cost_estimation` at the structural gate. This is **pre-existing** (the handler isn't wired to the Streamlit UI, so it never surfaced) and I left `policies.yaml` untouched per the no-touch rule. TODO worth deciding: re-map the handler's role/action to `construction_planner/produce_estimate` or leave as-is.

4. **No confidence score exists on the due-diligence path.** `handle_due_diligence` passes a constant neutral `confidence=0.90` to `GreenTeamService.evaluate()` (which requires a float) and lets the semantic risk level (`report.overall_risk`) drive the gate — reuse of the existing signal, not a new per-input heuristic. Documented in the handler.

5. **cost_estimation confidence heuristic.** No `model_confidence` existed inside `handle_cost_estimation` (only in the now-removed legacy FastAPI path). The gate derives confidence from the existing data-scarcity signal (`len(similar) < 3`), mirroring the legacy `"high" if >= 3 similar quotes` invariant. Against the real 30-quote corpus `find_similar_quotes` always returns `top_k=3`, so in production this gate is effectively "off"; the low-confidence path is exercised deterministically by the test (which patches the quote count). If a stronger live signal is wanted, the construction-planner-style corpus/fallback/hardcoded confidence mix could be reused — left as a TODO decision.

6. **`google.genai` lazy import.** `policy_service._llm_semantic_check` imports `from google import genai` inside `try/except ImportError` at call time. `google-genai` isn't pinned in `requirements.txt`; the import is already guarded and degrades gracefully, so I left it rather than expanding the requirements trim.

7. **Test doubles.** Three existing test files' fake `check_semantic` implementations had strict signatures; extending `check_semantic` with optional `role`/`action` kwargs required adding `**kwargs` to those fakes (`test_tab2_construction_planning.py`, `test_handlers_cost_estimation.py`, `test_green_team_gate.py`). Mechanical, no business logic touched.