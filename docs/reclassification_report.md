# Line-item reclassification report (Phase 2)

Backfill of the canonical taxonomy (`docs/category_taxonomy.md`) onto every existing line item, plus the repo-owner's special re-examination of the `szigeteles` category. The legacy `category_key` column is untouched; all new assignments live in `line_items.category_key_v2`.

## Task A — backfill tier split

Total line items backfilled: **1056**

| Tier | Items |
|------|------:|
| lookup | 1056 |
| groq | 0 |
| deepseek | 0 |
| unresolved | 0 |

**1056/1056 (100.0%) were resolved by the tier-1 exact lookup — no LLM call was made for the existing corpus.** The Groq/DeepSeek tiers exist for future ingestion of new `name_hu` strings that are not yet in the mapping.

## Task B — `szigeteles` re-examination

Items re-examined: **53**

| Outcome | Items |
|---------|------:|
| szigeteles | 1 |
| futes_rendszer | 16 |
| furdo | 33 |
| needs_human_review | 3 |

Provider tiers used for the re-examination: `{'groq': 53}`

### Flagged `needs_human_review` (3)

| raw `name_hu` | LLM category | reason |
|---------------|--------------|--------|
| `Utcafront hőszigetelése: 5cm multipor` | needs_human_review | Exterior facade insulation (utcafront) is not interior thermal insulation |
| `Vízszigetelés` | needs_human_review | Waterproofing without explicit bathroom context; location ambiguous per rule 2. |
| `Utcafront hőszigetelése` | needs_human_review | Exterior facade insulation ('Utcafront') is excluded from 'szigeteles' per rule 3. |

Kept as `szigeteles` (interior thermal/acoustic insulation):
- `Multipor hő-hang szigetelés folyósóval közös falra és utcai falra`


## Category histogram: before vs after

Before = legacy `category_key` (produced by `repository.assign_category()`), normalised to ASCII for comparison. After = new `category_key_v2`. `needs_human_review` is stored as SQL NULL.

| category | before | after | Δ |
|----------|-------:|------:|---:|
| egyeb | 645 | 102 | -543 |
| egyeb_komuves | 14 | 124 | +110 |
| burkolas | 46 | 107 | +61 |
| szallitas | 40 | 93 | +53 |
| gletteles_festes | 66 | 90 | +24 |
| furdo | 3 | 81 | +78 |
| futes_rendszer | 0 | 79 | +79 |
| viz_futes | 79 | 54 | -25 |
| nyilaszaro | 0 | 73 | +73 |
| villany | 38 | 70 | +32 |
| bontas | 57 | 48 | -9 |
| gipszkarton | 0 | 40 | +40 |
| vakolas | 30 | 31 | +1 |
| klima | 26 | 28 | +2 |
| konyha | 3 | 23 | +20 |
| parketta | 9 | 9 | +0 |
| needs_human_review | 0 | 3 | +3 |
| szigeteles | 0 | 1 | +1 |
| **total** | **1056** | **1056** | |

**`egyeb` catch-all: 645 → 102 (-543, -84% change).**

## Notes / out of scope

- The LLM was used for text classification only; no price arithmetic was performed here.
- `renovai/predictor/price_model.py::SCOPE_CATEGORY_MAP` is unchanged; the canonical categories feed the existing 7 scope buckets as documented.
- Backlog (not actioned): air-conditioning (`klima`) costs likely should not scale with total apartment area the way other categories do — AC pricing is closer to a per-unit/per-capacity cost, similar to how `renovai/predictor/structural_cost.py` keeps certain fixed-cost structural add-ons outside the per-sqm scaling model. This is a future pricing-model change, not a categorization change.
