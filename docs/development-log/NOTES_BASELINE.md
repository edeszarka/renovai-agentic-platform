# NOTES_BASELINE.md — Pre-refactor test baseline

Captured on branch `refactor/phase-0-streamlit-canonical` (base commit `3d84872`,
same tree as `main`). **No files modified yet.**

## Command
```
& .\.venv\Scripts\python.exe -m pytest tests --continue-on-collection-errors -q
```

## Result
```
1 failed, 127 passed, 4 skipped, 23 warnings, 2 errors in 41.09s
```

## Failures / errors
| Item | Kind | Reason |
|---|---|---|
| `tests/integration/test_agent.py::test_agent_stream` | FAILED | AssertionError — ADK agent stream produced no usable text content (environment/model related). |
| `tests/test_price_model.py` | ERROR (collection) | Test module imports `train_model` from `renovai.predictor.price_model`; that symbol was removed when the estimator moved to `scope_matched_estimate()`. Hangs the whole suite without `--continue-on-collection-errors`. |
| `tests/integration/test_agent_runtime_app.py::test_agent_stream_query` | ERROR | `google.genai.errors.ClientError: 403 ... BILLING_DISABLED` on project `project-d065e38c-b25a-4843-973` (environmental — Vertex AI billing not enabled). |
| 4 skipped | — | Pre-existing skips (non-fatal). |

## Note
The two integration errors above are **legacy-path** (ADK / Vertex AI Agent Engine
entry points). `tests/test_price_model.py` is also stale (tests the removed
`train_model` path, superseded by `scope_matched_estimate`).

## Per-refactor rule
Per task instructions, pre-existing failures must **not** be fixed as part of
this task without explicit approval. This file records the "before" state so the
refactor's after-test-run can be compared against a real baseline.