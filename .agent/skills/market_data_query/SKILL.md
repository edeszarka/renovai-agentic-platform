---
name: market_data_query
description: >
  Query the renovation market database using natural Hungarian questions.
  Triggers when the buyer asks about aggregate statistics, trends, or
  comparisons across the historical quote corpus — average costs per
  district or category, most common work items, price trends over time.
trigger_conditions:
  - User asks about averages, trends, or market-level statistics in Hungarian
  - Questions starting with "átlagosan", "mennyi a", "melyik kerületben", "hány"
  - User wants to compare costs across districts or building types
  - Hungarian keywords: "átlag", "statisztika", "összehasonlítás", "tendencia"
not_trigger_when:
  - User asks for a specific apartment's estimate (use cost_estimate)
  - User asks for pre-purchase advisory questions (use due_diligence)
  - User uploads a file (use sandboxed ingestion)
depends_on:
  - renovai.db.text_to_sql.TextToSQLEngine
  - Database must be populated (run ingestion first)
  - GOOGLE_API_KEY must be set for Gemini Text-to-SQL
calls:
  renovai_function: renovai.db.text_to_sql.TextToSQLEngine.query
  mcp_tool: query_renovation_market
