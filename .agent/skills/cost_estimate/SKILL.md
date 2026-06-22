---
name: cost_estimate
description: >
  Estimate total renovation cost in HUF for a Hungarian apartment.
  Triggers when the buyer asks for a price range, budget estimate, or cost
  breakdown for renovation work — especially when the question includes
  district, apartment size, room count, and scope keywords such as
  "villany", "vízvezeték", "burkolat", "bontás", or "teljes felújítás".
trigger_conditions:
  - User mentions renovation cost, budget, or price in Hungarian
  - User provides district (kerület), square meters, or room count
  - User lists specific work items (electrical, plumbing, flooring, etc.)
  - Input includes "mennyibe kerül", "költség", "ára", "költségek"
not_trigger_when:
  - User only asks about market trends or aggregate statistics (use market_data_query)
  - User only asks about pre-purchase inspection or red flags (use due_diligence)
  - User uploads a quote file for comparison (use sandboxed ingestion + comparison)
depends_on:
  - renovai.predictor.price_model
  - renovai.ingestion.inflation_calc
  - Data pipeline must have been run (trained model + price index)
calls:
  renovai_function: renovai.predictor.price_model.predict
  mcp_tool: estimate_renovation_cost
