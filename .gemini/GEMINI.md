# RenovAI 2.0 — Project DNA

This file is the architectural north star. Every agent reads this first before
any other context. If there is a conflict between this file and any other
instruction, this file wins.

## Project Identity

```yaml
project:
  name: renovai-capstone
  version: 2.0.0
  paradigm: spec-driven-development
  language: python 3.11+
  agent_framework: google-adk 2.1+
  communication_protocol: mcp (model context protocol)
  transport: sse for cloud-run, stdio for local
```

## Architecture Pattern

**Multi-Agent Routing.** A Gateway agent classifies every incoming Hungarian
question by intent, routes to exactly one specialized sub-agent, collects the
structured result, and composes the final Hungarian-language answer. Each
sub-agent owns a bounded domain and communicates exclusively via MCP tools
— no shared memory, no side channels, no implicit state.

```
User Query
  │
  ▼
┌──────────┐
│  Gateway │  ← Intent classification + routing
└────┬─────┘
     │
     ├──→ Ingestion Agent     (XLSX parse + HUF extraction)
     ├──→ Market Analyst      (Text-to-SQL + pricing stats)
     └──→ Due Diligence       (RAG advisory + red flags)
```

## Agent Definitions

### Gateway Agent

- **ID**: `renovai-gateway`
- **Model**: `gemini-2.5-flash`
- **Responsibility**: Entry point for all user queries. Classifies intent using
  keyword markers and optional LLM re-classification on uncertainty.
- **Routing Table**:

```yaml
routing_table:
  - intent: cost_estimation
    keywords: [mennyibe kerül, költség, ár, forint, nm ára, mennyit költsek]
    route_to: ingestion (if xlsx attached) | market-analyst (if params only)
  - intent: market_query
    keywords: [átlag, statisztika, tendencia, összehasonlítás, melyik kerület]
    route_to: market-analyst
  - intent: due_diligence
    keywords: [mire figyeljek, kockázat, ellenőrzés, piros zászló,
               átvilágítás, red flag]
    route_to: due-diligence
  - intent: combined
    keywords: [multiple intent markers present]
    route_to: gateway-composes (calls multiple sub-agents, merges results)
```

- **Uncertainty Handling**: If no intent matches with >80% confidence, respond
  with a clarification question listing the three supported domains. Never
  guess or hallucinate.

### Ingestion Agent

- **ID**: `renovai-ingestion`
- **Model**: `gemini-2.5-flash` (for structural metadata extraction only)
- **Responsibility**: Parses buyer-uploaded XLSX contractor quotes in an
  isolated sandbox. Handles HUF number parsing, column detection, row
  classification, metadata extraction, and optional inflation adjustment.

```yaml
sandbox:
  required: true
  constraints:
    - no network access
    - read-only data mount
    - seccomp profile
    - output to scratch temp dir only
pipeline:
  - detect_style_and_columns
  - classify_rows
  - parse_line_items
  - extract_metadata_from_notes
  - calculate_totals
  - validate_against_schema
  - export_adjusted_json
  - write_markdown_for_rag
mcp_tools_served:
  - estimate_renovation_cost
```

### Market Analyst Agent

- **ID**: `renovai-market-analyst`
- **Model**: `gemini-2.0-flash` (for SQL generation, cheaper)
- **Responsibility**: Answers market-level questions by converting natural
  Hungarian questions to SQL via TextToSQLEngine. Supports Groq, DeepSeek,
  or Gemini backends.

```yaml
sql_providers:
  - groq (via openai-compatible api)
  - gemini (via google-generativeai)
  - deepseek (paid fallback, selected by env var SQL_PROVIDER)
safety:
  - only SELECT statements permitted
  - schema is read-only
  - generated_sql is logged for audit
mcp_tools_served:
  - query_renovation_market
```

### Due Diligence Agent

- **ID**: `renovai-due-diligence`
- **Model**: `gemini-2.5-flash` (for complex advisory generation)
- **Responsibility**: Generates pre-purchase advisory reports using RAG
  pipeline. Runs hybrid retrieval against the quote corpus, assembles
  context within token budget, calls Gemini with structured prompt.

```yaml
retrieval:
  type: hybrid
  semantic_backend: chromadb
  keyword_backend: rank-bm25
  fusion: boost-overlap (+0.15 for results in both sets)
chunking:
  strategy: recursive-by-heading
  max_tokens: 512
  overlap_tokens: 64
fallback_model_chain:
  - gemini-2.5-flash
  - gemini-2.5-flash-lite
  - gemini-2.0-flash
  - gemini-2.0-flash-lite
rate_limiting:
  - on 429: skip to next model, do not retry quota errors
  - on other errors: retry up to 3 times with exponential backoff
mcp_tools_served:
  - get_due_diligence_advice
```

## Quality Gates

Every agent output MUST pass these gates before returning to the user:

```yaml
quality_gates:
  - every_response_must_include_data_limitation_note:
      condition: corpus_size < 3 similar cases
      template: "Figyelem: az adatbázis mindössze {n} hasonló idézetet tartalmaz."
  - every_huf_figure_must_include_inflation_date:
      format: "inflation_adjusted_to: YYYY-MM-DD"
  - every_citation_must_use_FORRAS_format:
      format: "[FORRÁS {n}: {source_file} | {section}]"
  - every_sql_query_must_be_logged:
      fields: [generated_sql, row_count, error_if_any]
  - sandbox_must_return_validated_schema:
      schema: RenovationQuote (pydantic)
      on_failure: return structured error, never auto-merge
  - gateway_must_label_merged_sources:
      rule: when combining two sub-agent responses, label each section
```

## Telemetry & Observability

```yaml
telemetry:
  provider: opentelemetry
  genai_content_capture:
    production: NO_CONTENT (metadata only)
    development: ALL (prompts + responses)
  bigquery_sinks:
    - name: genai_inference_logs
      type: log-sink
      destination: partitioned table by day
    - name: user_feedback
      type: log-sink
      schema: Feedback (score, text, session_id, user_id)
  dashboard:
    completions_view: joins gcs external table + log export
```

## Non-Goals

The following are explicitly out of scope for RenovAI 2.0:

- Real-time multi-user collaboration
- Automatic contractor matching or bidding platform
- Image recognition of building defects (future)
- Energy efficiency certification calculation
- Legal document generation (contracts, permits)
