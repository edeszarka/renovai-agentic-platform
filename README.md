# RenovAI — Renovation Advisor & Cost Estimator

A proof-of-concept tool that helps Hungarian apartment buyers who are considering
purchasing flats that need renovation. It combines machine learning for cost
estimation with a RAG-based advisory system using Gemini.

**Agents for Good track — Capstone submission.**

The three core capabilities (cost estimate, due-diligence advisory, market data
query) are exposed as [MCP](https://modelcontextprotocol.io) tools so that any
MCP client — not just this codebase — can use them. A tenant union or civic-tech
group could plug into this server later without touching RenovAI's code.

## Core Modules

1. **MCP Server** (`mcp_server/server.py`): Exposes three tools
   (`estimate_renovation_cost`, `get_due_diligence_advice`,
   `query_renovation_market`) over stdio (local) or SSE (Cloud Run).
2. **Pre-purchase Advisor (RAG-powered)** (`renovai/advisor/`): Generates
   questions for the seller, an inspection checklist, and identifies red flags.
3. **Renovation Cost Estimator (ML)** (`renovai/predictor/`): Predicts
   renovation costs from apartment parameters (district, area, rooms, scope),
   adjusted for current inflation, with per-category breakdowns.
4. **Agent Skills** (`.agent/skills/`): Nine standard Agent Skills for
   declarative routing by an orchestrator (cost estimation, due diligence,
   market data, and construction-role specialists).
5. **Sandboxed Ingestion** (`sandbox/`): Buyer-uploaded XLSX quotes are parsed
   in an isolated subprocess with no network access and read-only data mount.
6. **FastAPI REST API** (`renovai/api/main.py`): Exposes estimation, advisory,
   market query, and background ingestion over HTTP.
7. **Streamlit UI** (`app/streamlit_app.py`): Interactive buyer-facing frontend
   (Vevői Felkészítő + Felújítási Tervező tabs) calling the core library directly.
8. **Inflation Calculator** (`renovai/ingestion/inflation_calc.py`): Adjusts
   historical quote prices to current values using KSH CPI data.
9. **ADK Orchestrator Agent** (`orchestrator/agent.py`): Intent classification
   and tool composition on top of Google's Agent Development Kit (stretch goal).

## Architecture

```
                        ┌───────────────────────────────────────────────┐
                        │                 Buyer (user)                   │
                        │   terminal  ·  Streamlit UI  ·  any MCP client  │
                        └───────┬────────────────────┬──────────────────┘
                                │                    │
                 natural-language question            │ HTTP (FastAPI)
                                │                    ▼
                                │         ┌────────────────────────────┐
                                │         │  FastAPI REST API          │  scripts/run_api.py
                                │         │  /estimate  /advise      │  renovai/api/main.py
                                │         │  /query     /ingest/…      │
                                │         └─────────────┬──────────────┘
                                │                       │
                 ┌──────────────┴──────────────┐        │
                 │ ADK Orchestrator Agent      │        │
                 │  (orchestrator/agent.py)    │        │
                 │  intent classification +    │        │
                 │  tool composition           │        │
                 └──────────────┬──────────────┘        │
                                │                       │
                        MCP stdio / SSE                 │
                                ▼                       ▼
        ┌───────────────────────────────────┐   ┌────────────────────────────┐
        │        MCP Server (renovai)        │   │      Streamlit UI          │
        │        mcp_server/server.py        │   │      app/streamlit_app.py  │
        │                                   │   │  calls renovai/ functions  │
        │  estimate_renovation_cost           │   │  directly (no MCP)         │
        │  get_due_diligence_advice           │   └────────────┬───────────────┘
        │  query_renovation_market            │                │
        └──────┬──────────────────┬───────────┘                │
               │                  │                             │
               ▼                  ▼                             ▼
  ┌────────────────────┐  ┌────────────────────┐   ┌──────────────────────────┐
  │     Agent Skills   │  │  Sandboxed Ingest   │   │      renovai/ core        │
  │    .agent/skills/  │  │   sandbox/*.py     │   │  predictor.price_model     │
  │   (9 skills,       │  │  untrusted XLSX →  │   │  advisor.pre_purchase      │
  │   SKILL.md +       │  │  subprocess → JSON │   │  rag.pipeline (Gemini +    │
  │   scripts/ + refs) │  │  → validate → merge │  │    ChromaDB embeddings)    │
  └────────────────────┘  └────────────────────┘   │  db.text_to_sql            │
                                                    │  ingestion.quote_parser   │
                                                    │  ingestion.inflation_calc │
                                                    └────────────┬─────────────┘
                                                                 │
                              ┌──────────────────────────────────┼──────────────────────┐
                              ▼                                  ▼                      ▼
               ┌────────────────────────┐          ┌────────────────────┐   ┌────────────────────────┐
               │  Data / ML artifacts   │          │ Vector store       │   │ Database (SQLite)      │
               │  data/models/*.joblib  │          │ data/chroma_db/    │   │ data/renovai.db        │
               │  data/processed/json   │          │ (ChromaDB)         │   │ quotes · line_items    │
               └────────────────────────┘          └────────────────────┘   │ cpi_records · …        │
                                                                             └────────────────────────┘
```

Key points:

- **MCP-first**: orchestration, civic-tech/tenant-union clients, and the demo all
  talk to the same three MCP tools. The REST API and Streamlit UI are thin
  wrappers over the same `renovai/` core.
- **One implementation, two interfaces**: each Agent Skill's entry point and the
  corresponding MCP tool call the same underlying `renovai/` function.
- **Text-to-SQL** (`renovai/db/text_to_sql.py`) converts Hungarian market
  questions to SQLite `SELECT` statements via Groq (default), DeepSeek, or
  Gemini, returning the generated SQL alongside results for auditability.

## Tech Stack

- **Language:** Python 3.11+
- **Package/dev:** `uv` (uv sync, uv run)
- **APIs:** FastAPI, MCP (Model Context Protocol), Google ADK
- **LLM/Embeddings:** Google Gemini (Generative AI)
- **Vector DB:** ChromaDB
- **ML:** Scikit-learn, TabPFN, Joblib
- **Text-to-SQL LLM:** Groq (default) · DeepSeek · Gemini
- **Data:** Pandas, Openpyxl, SQLAlchemy, Alembic, AIOSQLite
- **UI:** Streamlit

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url>
   cd renovai-capstone
   ```

2. **Install dependencies:**
   ```bash
   uv sync
   ```

3. **Configure environment:**
   Copy `.env.example` to `.env` and fill in your API keys:
   ```bash
   cp .env.example .env
   ```
   Minimum required keys:
   - `GOOGLE_API_KEY` — Gemini chat/embeddings
   - `SQL_PROVIDER` + `GROQ_API_KEY` (or `DEEPSEEK_API_KEY`) — Text-to-SQL
   - `TABPFN_TOKEN` — only needed to re-train the price model
   - `DATABASE_URL`, `CHROMA_DB_PATH`, path variables have sane defaults

4. **Run Ingestion & Training (Initial Setup):**
   ```bash
   uv run python scripts/run_ingestion.py --adjust-inflation
   uv run python scripts/build_vector_store.py
   uv run python scripts/train_model.py
   ```

   > The shipped artifacts (`data/`) are git-ignored, so a fresh clone must be
   > re-ingested from raw XLSX quotes before the MCP/API predictors work.

## Running the MCP Server

```bash
uv run python -m mcp_server.server
```

The server listens on stdio using the MCP protocol. Any MCP client can connect
to it. To smoke-test, send an `initialize` handshake followed by `tools/list`
(the demo below does this automatically):

```bash
@('{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}','{"jsonrpc":"2.0","id":1,"method":"tools/list"}') | uv run python -m mcp_server.server
```

### Cloud Run / SSE transport

Set `CLOUD_RUN=true` (and optionally `PORT`) to serve the same tools over SSE
instead of stdio:

```bash
CLOUD_RUN=true uv run python -m mcp_server.server
```

## Running the FastAPI REST API

```bash
uv run python scripts/run_api.py            # default http://0.0.0.0:8000
uv run python scripts/run_api.py --port 8000 --reload
```

Interactive docs at `http://localhost:8000/docs`. Endpoints: `/health`,
`/estimate`, `/advise`, `/query`, `/query/stream`, `/ingest/quotes`,
`/ingest/status/{id}`.

## Running the Streamlit UI

```bash
uv run streamlit run app/streamlit_app.py
```

## Running the ADK Orchestrator (stretch)

```bash
uv run python -m orchestrator.agent "55 m²-es lakást nézek a 8. kerületben, villany és vízvezeték is kell, mennyit költsek felújításra és mire figyeljek vásárlás előtt?"
```

The orchestrator classifies the Hungarian question and delegates to one or more
MCP tools, composing a cited answer in Hungarian.

## Demo

```bash
uv run python -m demo.run_demo
```

Starts the MCP server in-process, performs the full init handshake, and runs the
three example queries (cost, due diligence, market data).

## Sandboxed Quote Ingestion

```bash
# Parse a buyer's XLSX quote in an isolated subprocess
uv run python -m sandbox.ingest_sandboxed path/to/contractor_quote.xlsx

# Parse and merge into the database (with explicit confirmation)
uv run python -m sandbox.ingest_sandboxed path/to/contractor_quote.xlsx --merge
```

Buyer-uploaded files are untrusted: the parse runs in a subprocess with no
network access and a read-only data mount, writes to a scratch JSON, is
validated against the `RenovationQuote` schema, and only merges into the DB on
explicit `--merge`.

## Agent Skills

`orchestrator/skill_registry.py` loads every skill in `.agent/skills/`:

| Skill | Purpose |
| --- | --- |
| `cost_estimate` | Total renovation cost in HUF |
| `due_diligence` | Pre-purchase questions, checklist, red flags |
| `market_data_query` | Aggregate/statistical questions over the DB |
| `construction-planner` / `masonry-specialist` / `structural-core-specialist` | Structure & construction role guidance |
| `expert-interviewer` | Interview for the buyer |
| `finishing-specialist` / `finishing-interior-specialist` | Finishing/interior role guidance |

## Data Limitation Note

The price model is trained on n=47 historical quotes (2023–2026), most without
floor-area data. The system is designed to be honest about this: the primary
signal is RAG-grounded similar-case comparison, the ML regression number is
secondary. Every response includes the number of similar cases found and a
warning when the corpus is thin.

See `docs/ARCHITECTURE.md` for full details.