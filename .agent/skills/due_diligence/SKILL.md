---
name: due_diligence
description: >
  Generate a pre-purchase due-diligence advisory report with questions for the
  seller, an inspection checklist, and building-specific red flags for a
  Hungarian apartment.  Triggers when the buyer asks what to check before
  buying, what questions to ask the seller, or what red flags to watch for.
trigger_conditions:
  - User asks about pre-purchase inspection, due diligence, red flags
  - User asks what questions to ask the seller before buying
  - User mentions building type (panel, tégla, újépítés) and condition
  - Hungarian keywords: "mire figyeljek", "ellenőrzés", "átvilágítás",
    "kérdések az eladóhoz", "piros zászlók", "kockázat"
  - User provides or implies a specific apartment (district, size, condition)
not_trigger_when:
  - User only wants a cost estimate without advisory (use cost_estimate)
  - User only asks about market statistics (use market_data_query)
  - User uploads a quote for comparison (use sandboxed ingestion)
depends_on:
  - renovai.advisor.pre_purchase
  - renovai.rag.pipeline (requires populated ChromaDB)
  - renovai.predictor.price_model
  - renovai.ingestion.inflation_calc
calls:
  renovai_function: renovai.advisor.pre_purchase.generate_report
  mcp_tool: get_due_diligence_advice
