# RenovAI Architecture

## Canonical product path (current phase)

> **`app/streamlit_app.py` + `orchestrator/handlers.py` is the canonical, live
> product path for the current phase** — Streamlit-only, two tabs
> ("Vevői Felkészítő" / buyer prep, "Felújítási Tervező" / renovation planner),
> direct in-process calls through the orchestrator handlers. No MCP, no ADK,
> no FastAPI. The Streamlit app drives the orchestrator handlers in-process
> over the SemVer'd handler return contracts, and `policies.yaml` is enforced
> via `orchestrator/policy_service.py`.

Everything in this file describes the full past architecture. Treat the
section below, "Legacy entry points", as the authority on what is *not* part of
the current product. New work should extend the canonical path; none of the
legacy entry points are maintained or deployed.

## Legacy entry points (not part of the current product)

Each of the following shipped in earlier phases but is **not** on the canonical
path today. They live under `legacy/` (see `legacy/README.md`):

- `renovai/api/main.py` — FastAPI REST/SSE surface built for an earlier,
  deprecated cost-prediction path (`predict()`), superseded by
  `scope_matched_estimate()`.
- `app/agent.py` + `app/agent_runtime_app.py` — Vertex AI Agent Engine
  (ADK) hosting shell for Cloud deployment; a course deliverable for
  Vertex AI Agent Engine compatibility.
- `orchestrator/agent.py` — the ADK-wrapped orchestrator agent used for the
  MCP-first/ADK demo; superseded by direct handler calls from Streamlit.
- `mcp_server/` — standalone MCP server (stdio/SSE) exposing the three tools
  for external MCP clients; not used by the current Streamlit deploy.
- root `Dockerfile` + `deployment/cloud_run/` — container deploy targets for
  the ADK/FastAPI runtime; not used for the current Streamlit-only deploy.

## Problem statement

First-time and lower-income apartment buyers in Hungary lack access to
contractor-grade due diligence. They don't know what questions to ask
sellers, can't independently verify whether a renovation quote is fair,
and don't recognise red flags common in older Budapest housing stock
(kohósalak slag under floors, shared plumbing stacks requiring co-owner
approval, undocumented electrical work). This knowledge already exists —
inside fragmented Excel quotes and contractor know-how — but never
reaches the buyers who need it most before they sign a purchase contract.

## Why this must be an agent, not a static FAQ

Each apartment is different — the right questions, the right red flags,
and the right cost range depend on the building era, district, and
condition of that specific unit. A static FAQ cannot reason over 30 real
historical quotes to find the 3 most comparable cases, cross-check a
buyer's planned scope against what got excluded in similar quotes, and
explain the gap in plain Hungarian. That requires retrieval, reasoning,
and tool composition — i.e., an agent.

## System diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     Buyer (terminal / UI)                     │
└──────────────────┬──────────────────────────┬───────────────┘
                   │                          │
                   ▼                          ▼
┌──────────────────────────────┐  ┌──────────────────────────┐
│   ADK Orchestrator Agent      │  │   Any MCP Client         │
│   (orchestrator/agent.py)     │  │   (tenant union, civic   │
│   Intent classification +     │  │    tech app, CLI)        │
│   tool composition            │  │                          │
└──────────┬───────────────────┘  └──────────┬───────────────┘
           │                                  │
           └──────────┬───────────────────────┘
                      │ MCP stdio protocol
                      ▼
┌──────────────────────────────────────────────────────────────┐
│                   MCP Server (mcp_server/server.py)            │
│                                                               │
│  estimate_renovation_cost  get_due_diligence_advice            │
│  query_renovation_market                                      │
│                                                               │
│  "Open infrastructure" — any MCP client can call these tools  │
└───────┬─────────────────────────────┬────────────────────────┘
        │                             │
        ▼                             ▼
┌───────────────────┐   ┌────────────────────────────────────┐
│   Agent Skills     │   │   Sandboxed Ingestion              │
│   (.agent/skills/) │   │   (sandbox/ingest_sandboxed.py)    │
│                    │   │                                    │
│  cost_estimate     │   │  Untrusted XLSX → subprocess       │
│  due_diligence     │   │  → parser → scratch JSON           │
│  market_data_query │   │  → validation → confirm → merge    │
└───────────────────┘   └────────────────────────────────────┘
        │                             │
        ▼                             ▼
┌──────────────────────────────────────────────────────────────┐
│              renovai/ (core library, unchanged)                │
│                                                               │
│  predictor.price_model   advisor.pre_purchase                 │
│  db.text_to_sql         ingestion.quote_parser               │
│  rag.pipeline           ingestion.inflation_calc             │
└──────────────────────────────────────────────────────────────┘
```

## Data limitation (n=30)

The price model is trained on 30 historical quotes, most of which lack
floor-area data. This is a known limitation, and the system is designed
to be honest about it rather than overstate confidence:

1. **Primary signal**: RAG-grounded similar-case comparison — the system
   finds the 3 most comparable historical quotes and presents them
   alongside the regression estimate, so the buyer can judge relevance.
2. **Secondary signal**: the ML regression number, clearly labelled with
   the number of similar cases found.
3. **Warnings**: when fewer than 3 comparable cases exist, a warning is
   appended to every response.
4. **Citations**: every answer cites which historical quote(s) informed
   it, so the buyer can trace the evidence.

## MCP-first design

All three capabilities are exposed as MCP tools on stdio transport.
This is a deliberate "open infrastructure" decision: a tenant union
or civic-tech group could connect their own MCP client to this server
and offer renovation due-diligence through their own interface, without
touching RenovAI's codebase.

## Skills architecture

Each capability also exists as a standard Agent Skill (SKILL.md +
scripts/ + references/) so that an orchestrator can route to them
declaratively. The skill entry point and the MCP tool call the same
underlying renovai/ function — they are two interfaces onto one
implementation.

## Sandboxing

Buyer-uploaded XLSX files (quotes from contractors they're evaluating)
are untrusted. They could contain malicious macros or malformed
structures. The sandboxed ingestion pipeline:

1. Spawns a subprocess with no network access
2. The subprocess mounts data/ as read-only
3. The parser output goes to a scratch JSON file (not the DB)
4. The parent process validates against the RenovationQuote schema
5. Merge into the real DB only on explicit confirmation

For production, a Docker container with seccomp and --network=none
provides full isolation.
