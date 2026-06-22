# RenovAI — Renovation Advisor & Cost Estimator

A proof-of-concept tool that helps Hungarian apartment buyers who are considering purchasing flats that need renovation. It combines machine learning for cost estimation with a RAG-based advisory system using Gemini.

**Agents for Good track — Capstone submission.**

The three core capabilities (cost estimate, due-diligence advisory, market data query) are exposed as [MCP](https://modelcontextprotocol.io) tools so that any MCP client — not just this codebase — can use them. A tenant union or civic-tech group could plug into this server later without touching RenovAI's code.

## Core Modules

1.  **MCP Server:** Exposes three tools (`estimate_renovation_cost`, `get_due_diligence_advice`, `query_renovation_market`) via stdio MCP protocol.
2.  **Pre-purchase Advisor (RAG-powered):** Generates questions for the seller, an inspection checklist, and identifies red flags.
3.  **Renovation Cost Estimator (ML):** Predicts renovation costs based on apartment parameters (district, area, rooms, etc.), adjusted for current inflation.
4.  **Agent Skills:** Three standard Agent Skills (`.agent/skills/`) for declarative routing by an orchestrator.
5.  **Sandboxed Ingestion:** Buyer-uploaded XLSX quotes are parsed in an isolated subprocess with no network access and read-only data mount.
6.  **FastAPI REST API:** Exposes all functionality via a modern web API.
7.  **Inflation Calculator:** Adjusts historical quote prices to current values using KSH CPI data.

## Tech Stack

- **Backend:** Python 3.11+
- **API:** FastAPI / MCP (Model Context Protocol)
- **LLM/Embeddings:** Google Gemini (Generative AI)
- **Vector DB:** ChromaDB
- **ML:** Scikit-learn, Joblib
- **Data:** Pandas, Openpyxl

## Installation & Setup

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd renovai-capstone
    ```

2.  **Install dependencies:**
    ```bash
    uv sync
    ```

3.  **Configure environment:**
    Copy `.env.example` to `.env` and fill in your API keys:
    ```bash
    cp .env.example .env
    ```

4.  **Run Ingestion & Training (Initial Setup):**
    ```bash
    python scripts/run_ingestion.py --adjust-inflation
    python scripts/build_vector_store.py
    python scripts/train_model.py
    ```

## Running the MCP Server

```bash
uv run python -m mcp_server.server
```

The server listens on stdio using the MCP protocol. Any MCP client can connect to it. To test with the MCP CLI tool:

```bash
# List available tools
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | uv run python -m mcp_server.server
```

## Running the ADK Orchestrator (stretch)

```bash
uv run python -m orchestrator.agent "55 m²-es lakást nézek a 8. kerületben, villany és vízvezeték is kell, mennyit költsek felújításra és mire figyeljek vásárlás előtt?"
```

## Try it (example Hungarian queries)

Paste these into the orchestrator or send them as raw MCP tool calls:

1. **Cost + advisory (combined):**
   ```
   55 m²-es lakást nézek a 8. kerületben, villany és vízvezeték is kell, mennyit költsek felújításra és mire figyeljek vásárlás előtt?
   ```

2. **Market data query:**
   ```
   Mennyibe került átlagosan a villanyszerelés a 2023-as arakban?
   ```

3. **Due diligence only:**
   ```
   Mire figyeljek egy 1970-es panel lakás vásárlásánál a 13. kerületben?
   ```

## Demo

```bash
uv run python -m demo.run_demo
```

## Sandboxed Quote Ingestion

```bash
# Parse a buyer's XLSX quote in an isolated subprocess
uv run python -m sandbox.ingest_sandboxed path/to/contractor_quote.xlsx

# Parse and merge into the database (with explicit confirmation)
uv run python -m sandbox.ingest_sandboxed path/to/contractor_quote.xlsx --merge
```

## Data Limitation Note

The price model is trained on n=30 historical quotes, most without floor-area data. The system is designed to be honest about this: the primary signal is RAG-grounded similar-case comparison, the ML regression number is secondary. Every response includes the number of similar cases found and a warning when the corpus is thin.

See `docs/ARCHITECTURE.md` for full details.
