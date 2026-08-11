# RenovAI legacy/

Everything under `legacy/` is **not part of the current product**. The
canonical, live path is `app/streamlit_app.py` + `orchestrator/handlers.py`
(Streamlit-only, direct in-process handler calls). See
`docs/ARCHITECTURE.md` for the full breakdown.

These directories and files shipped in earlier phases or served alternate
deployment targets. They are kept for reference and reproducibility but are
not exercised by the canonical test suite (`pytest tests`) and are not
deployed.

## What lives here

| Path | What it is |
| --- | --- |
| `adk_agent/` | Vertex AI Agent Engine (ADK) hosting shell: `agent.py`, `agent_runtime_app.py`, and `app_utils/` (telemetry, typing, Agent Runtime deploy script). |
| `adk_agent_stretch_goal/` | The ADK-wrapped orchestrator agent (`agent.py`) used for the MCP-first/ADK demo. |
| `renovai_api/` | Former `renovai/api/` FastAPI REST/SSE surface (`main.py`, `errors.py`, `schemas.py`, `feedback.py`) built for the deprecated `predict()` cost path. |
| `mcp_server/` | Standalone MCP server (stdio/SSE) exposing the three tools for external MCP clients. |
| `deployment/` | Container/deploy targets: root `Dockerfile` and `cloud_run/` (Dockerfile, Dockerfile.gvisor, health.py, service.yaml). |
| `tests/` | Tests quarantined out of the canonical suite: `test_agent.py`, `test_agent_runtime_app.py` (require a live Google Cloud project / MCP server) and `test_price_model.py` (targets the removed `train_model()` estimator). |
| `requirements-legacy.txt` | Dependencies only needed by the quarantined ADK/Agent Engine code: `google-adk`, `google-cloud-aiplatform`, `google-cloud-logging`. |

## Running anything here

These were written against the old module layout. Imports inside the moved
files were updated to the new `legacy.*` package paths, so e.g. the MCP
server can still be launched with:

```
uv run python -m legacy.mcp_server.server
```

but nothing here is maintained, tested, or deployed as part of the current
canonical path.