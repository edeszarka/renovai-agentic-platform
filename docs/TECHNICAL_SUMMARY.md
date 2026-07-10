# RenovAI — Technical Architecture Summary (Interview Talking Points)

---

## 1. System Architecture

The system follows a **modular, service-oriented architecture** with four primary layers:

**Ingestion & Feature Engineering Pipeline:**
- Raw XLSX contractor quotes → `sandbox.ingest_sandboxed` (isolated subprocess) → `quote_parser` → structured `RenovationQuote` Pydantic model → SQLAlchemy ORM persistence + inflation-adjusted JSON export + Markdown rendering for RAG ingestion
- **Sandboxed design**: buyer-uploaded XLSX files are parsed in a subprocess with no network access and read-only data mounts (container-level isolation via Docker with seccomp profile)

**ML Prediction Layer** (`renovai/predictor/`):
- Feature extraction via keyword-based line-item classification into 9 work categories (demolition, plumbing, electrical, tiling, etc.)
- Two estimation paths: (1) `scope_matched_estimate()` — DB-backed per-scope average aggregation with area scaling and inflation adjustment; (2) `predict()` — scikit-learn regression pipeline (legacy, `joblib`-serialized)
- Similar-case retrieval using Euclidean distance on numeric features (district, line item counts, cost ratios)
- Inflation adjustment via linear interpolation of quarterly KSH CPI series (separate labor/materials indices)

**RAG Pipeline** (`renovai/rag/`):
- Hybrid retrieval: semantic search (ChromaDB + Gemini embeddings) + keyword search (BM25), merged with bonus scoring for overlap, filtered by similarity threshold, truncated to token budget
- Recursive semantic chunking of Markdown documents (H3 → paragraphs → sentences) with configurable overlap
- Multi-model fallback for generation: tries up to 8 Gemini model variants in sequence with exponential backoff
- Citation extraction and confidence scoring based on source count

**Agent & API Layer**:
- **MCP Server** (`mcp_server.server`): 3 tools (`estimate_renovation_cost`, `get_due_diligence_advice`, `query_renovation_market`) exposed via Model Context Protocol with SSE transport for Cloud Run
- **ADK Orchestrator** (`orchestrator.agent`): Google Agent Development Kit-based router using `gemini-2.5-flash` with graceful degradation to keyword-based fallback
- **FastAPI** (`renovai/api/main.py`): REST endpoints for health, estimate, advise, query (with SSE streaming), and async ingestion
- **Streamlit UI** (`app/streamlit_app.py`): 3-tab interface (pre-visit advisor, post-visit valuation with buy-vs-new comparison, NL-to-SQL market query)

**Data Flow**: User input → scope extraction → parallel ML estimation + RAG retrieval → combined report with cost ranges, similar cases, inspection checklist, and red flags → buy-vs-new comparison using KSH median new-build prices

---

## 2. Tech Stack Justification

| Component | Technology | Why |
|---|---|---|
| **Language** | Python 3.11+ | ML ecosystem dominance, async support, type hints |
| **Prediction** | scikit-learn + joblib | Lightweight, well-understood regression; `joblib` for model serialization |
| **Vector Store** | ChromaDB (persistent client) | Self-contained, no external service; local persistence; cosine distance; metadata filtering |
| **Embeddings & Generation** | Google Gemini (`google-generativeai`) | Competitive quality; multi-model fallback (8 variants); built-in safety; Hungarian language support |
| **Hybrid Retrieval** | `rank-bm25` | Lightweight keyword search complement to semantic; no external Elasticsearch needed |
| **Databases** | SQLAlchemy 2.0 async + aiosqlite / Alembic | Async ORM with migration support; SQLite for local dev; PostgreSQL-compatible for production |
| **API** | FastAPI + uvicorn | Async-native, auto-docs, SSE streaming, Pydantic validation |
| **Agent Framework** | Google ADK (`google-adk`) + MCP | Protocol-level tool interoperability; any MCP client can consume these tools without coupling to RenovAI's code |
| **Deployment** | Docker → Cloud Run / Vertex AI Agent Runtime | Serverless, auto-scaling, managed infrastructure |
| **Infrastructure as Code** | Terraform (Google provider) | Version-controlled GCP resource provisioning (BigQuery telemetry, storage, IAM, Agent Runtime) |
| **Configuration** | Pydantic Settings + `.env` | Type-safe config, env var injection, multi-provider LLM routing (Gemini, Groq, DeepSeek, Ollama) |
| **Caching** | `diskcache` | Disk-based embedding cache avoids redundant API calls across runs |
| **Retry** | `tenacity` | Exponential backoff for API calls; differentiates retryable vs. quota errors |

---

## 3. Complex Engineering Challenges

### 3a. Hybrid Retrieval with Result Fusion
The `retriever.py` module implements a **late-fusion hybrid search** combining semantic (ChromaDB cosine distance) and keyword (BM25) scores. The innovation is the fusion strategy: results appearing in both sets receive a +0.15 boost, recognizing that consensus between two independent retrieval methods is a stronger relevance signal. Scores are independently normalized (cosine distance → similarity, BM25 → min-max scaled) before fusion, then threshold-filtered and top-ranked. This compensates for the small corpus (30 quotes) where pure semantic search can miss keyword-specific matches.

### 3b. Robust XLSX Ingestion from Heterogeneous Real-World Documents
The `ingestion/` module parses **unstructured, multi-style XLSX contractor quotes** — a classic messy data problem. The pipeline:
- **Column detection** (`column_detector.py`): auto-detects 4 spreadsheet styles (text-only, standard 5-column, multi-phase, scope-only) by matching Hungarian column headers against canonical names
- **Row classification** (`row_classifier.py`): state-machine-driven walk through rows identifying headers, phase labels, line items, section headers, total rows, and text summaries via keyword patterns
- **Number parsing** (`number_parser.py`): handles diverse Hungarian HUF formats (`1,690,000`, `963118 Ft`, `nettó 2835000 Ft`, `3798118 Ft.`)
- **Metadata extraction** (`metadata_extractor.py`): regex-based extraction of timeline, payment schedules, slag complications, VAT status, EUR rates, and material brands from free-text notes
- This is effectively a **mini ETL engine** for semi-structured financial documents with no standardized schema

### 3c. Recursive Semantic Chunking with Token Budget Management
The `chunker.py` module implements a **recursive, coherence-aware document splitting** strategy for RAG. Rather than naive fixed-length splits, it:
1. Parses YAML front matter for metadata preservation
2. Splits by H2 section headers (preserving document structure)
3. Recursively sub-splits by H3, then paragraphs (with sliding window overlap), then sentences
4. Each level respects a configurable `max_tokens` budget (using `tiktoken` cl100k_base)
5. Chunk IDs encode source, section, and position for traceability

This ensures that RAG retrieval operates on semantically coherent units rather than arbitrary token windows, significantly improving citation accuracy.

---

## 4. MLOps / Production Readiness

### Containerization
- **Dockerfile**: Multi-stage; Python 3.11-slim base; layers ordered for cache efficiency (dependencies first, code second); baked-in model artifacts (ChromaDB, joblib, inflation CSVs)
- **Sandbox Dockerfile**: Minimal image with only openpyxl + pydantic; designed for `--network=none --read-only` runtime flags (production-grade isolation for untrusted XLSX)

### Deployment
- **Cloud Run**: MCP server deployed via `uvicorn` with SSE transport; environment variable-driven transport selection (`CLOUD_RUN=true`)
- **Vertex AI Agent Runtime**: ADK agent deployed via `deploy.py` with update/create logic; infrastructure managed by Terraform
- **Terraform**: Full GCP resource provisioning — BigQuery datasets, log sinks, IAM, storage buckets, Agent Reasoning Engine with lifecycle management

### Observability & Telemetry
- **OpenTelemetry**: Configured via `app/app_utils/telemetry.py`; GenAI telemetry capture with content masking option
- **Google Cloud Logging**: Structured logging from agent runtime
- **BigQuery Telemetry Pipeline**: Log sinks route GenAI inference logs and user feedback to BigQuery partitioned tables; external tables over GCS completions data; SQL view joining both sources for analysis
- **Feedback Model**: Pydantic-validated feedback ingestion (score, text, session_id, user_id) for continuous improvement

### Testing
- **pytest** with `pytest-asyncio` (auto mode, function-scoped loop)
- 7 test files covering: price model/features, RAG pipeline, advisor/report generation, quote parsing, inflation calculation, vector store, database operations
- Unit tests use extensive mocking (Gemini client, ChromaDB, joblib) for deterministic CI runs
- **Evals module** (`renovai/evals/`): golden dataset of 4 evaluation examples with keyword-match and source-recall scoring; Rich-formatted report output

### Data Versioning & Pipeline Scripts
- **Alembic** for database schema migrations
- **6 CLI scripts** (`scripts/`): `run_ingestion.py` (with `--adjust-inflation` flag), `build_vector_store.py`, `train_model.py`, `run_evals.py`, `query_rag.py`, `query_sql.py`
- **Disk-based caching** (`diskcache`) for embeddings, avoiding redundant API calls during development iteration
