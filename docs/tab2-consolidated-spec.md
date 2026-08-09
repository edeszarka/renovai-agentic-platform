# RenovAI Tab 2 Real-Data Estimator — Consolidated Design Spec

Status as of this writing. Source of truth for the implementation prompts
that follow. Supersedes any earlier partial descriptions in this thread.

## The core finding this whole investigation converged on

Neither Streamlit tab calls the corpus-based estimator:
- **Tab 1 (Buyer Prep)** → `handle_expert_interview()` — qualitative
  red-flags/risk output only. No numeric estimate at all.
- **Tab 2 (Renovation Planner)** → `handle_construction_planning()` —
  fully hardcoded phase constants, zero corpus/inflation awareness.
- **`handle_cost_estimation()` → `scope_matched_estimate()`** — the
  correctly-fixed, per-quote-inflation-corrected, corpus-based
  estimator — is reachable by NONE of Streamlit, FastAPI `/estimate`
  (uses deprecated `predict()`), or the MCP tool (also `predict()`).
  Only reachable via the ADK agent orchestrator, not in active use.

**Implication:** the redesign work below isn't complete until
`handle_construction_planning()` is rewired to actually source its
phase costs from it. That's the last, necessary step — not optional
follow-on polish.

## Decisions locked in this conversation

1. **Ingestion** — quotes stored at nominal (non-inflated) price;
   `quote_year` derived from `data/raw/quotes/<year>/` folder name,
   Jan-1 convention. **DONE**, merged via `fix/per-quote-inflation-compounding`.
2. **Inflation** computed dynamically at estimate time per quote —
   `compound_inflation_factor(quote_year, today, price_index)` — never
   baked into storage. **DONE**.
3. **Per-category (work-class) breakdown** — already computed
   internally in `scope_matched_estimate()` but currently collapsed
   into one blended total before returning. Needs to be **surfaced** in
   the return value. **NOT YET BUILT.**
4. **Weighting** = existing IVW (`weight = 1/variance`) **×** recency.
   Recency redefined this conversation: NOT a fixed time window (a
   6-month window was tested and found to always return zero
   qualifying quotes, given year-only date granularity — a silent
   no-op). Instead: **per category, whichever year is the most recent
   one actually present in that category's data gets 2x weight; every
   other year gets 1x.** Adapts automatically to data availability.
   **NOT YET BUILT.**
5. **Composition**: `sum(category_avg_per_sqm for active categories) ×
   target_area + CONTINGENCY`. This pattern already exists in
   `scope_matched_estimate()` — reuse it, just make the per-category
   breakdown that feeds it externally visible.
6. **Structural/fixed-cost add-ons** (chain costs, infra minimums,
   chimney, elevator, ceiling multiplier, logistics %) stay OUTSIDE the
   per-sqm model — they don't scale with area. They're sourced from a
   2025-dated expert reference and currently get ZERO inflation. They
   need their OWN inflation factor (2025 → today), separate from the
   per-quote corpus inflation, centralized in `structural_cost.py` so
   both handlers benefit without duplicating logic. **NOT YET BUILT.**
7. **District** — confirmed fully inert everywhere: dead UI widgets in
   both tabs (assigned, then never passed to any handler), and in the
   calculation pipeline it only ever affected `find_similar_quotes()`'s
   ranking — which the UI doesn't even call or display. Only remaining
   cleanup: drop `"district"` from `find_similar_quotes`'s `num_cols`
   (`price_model.py:303-309`). Low priority — nothing currently
   surfaces that ranking to a user anyway.
8. **Legitimate differentiators to KEEP** (explicit user requirement,
   and already meaningfully wired into Tab 2's current hardcoded
   logic): building era, floor construction/material, wall condition,
   ceiling height, floor number/elevator presence, gas heating. This
   logic must survive the rewiring — layered on top of corpus-derived
   base costs as adjustments/surcharges, the way it already layers on
   top of hardcoded constants today. Not being discarded.
9. **`SCOPE_CATEGORY_MAP` expansion** — currently only 4 of 18 seeded
   `WorkCategory` entries are mapped (plumbing, electrical, flooring,
   demolition). The unmerged `numerical_eval_harness` branch already
   added 3 more (windows_doors, insulation, ac) but was built on stale
   pre-inflation-fix code with incompatible internals — **cherry-pick
   only the scope-expansion hunks**, rebase onto the current
   (per-quote-inflation-fixed) `price_model.py`, regenerate golden test
   values (the old golden data assumed the removed 2024-02-15
   baseline). 5-6 more categories have real corpus data but no scope
   flag yet: plastering (20q), painting (34q), masonry/`egyéb_köműves`
   (11q), `szállítás`/transport (28q — flagged risk: may double-count
   against the existing 15% logistics surcharge, needs an explicit
   design call before wiring it in).
10. **`nyílászáró` (windows/doors) and `szigetelés` (insulation)**
    currently show 0 tagged items — confirmed to be a **data-tagging
    problem**, not a real corpus gap. Real window/door and insulation
    line items exist, mis-tagged under `egyéb`/`vakolás`/`víz_fűtés`.
    Re-tagging is recommended over generating new quotes, but the
    keyword rule needs care — repair/paint jobs and bathroom
    waterproofing must not get swept into these categories by mistake.
11. **Known bug surfaced incidentally, unrelated to any of the above but
    blocking**: `handle_cost_estimation()` passes an `ApartmentInput`
    into `find_similar_quotes()`, which does
    `getattr(features, "num_line_items")` — an attribute
    `ApartmentInput` doesn't have. This raises `AttributeError` on
    every call today. This is the ONE handler with correct calculation
    logic, and it currently can't run end-to-end. **Must fix regardless
    of scheduling for anything else.**
12. **Data staleness** — the live `data/renovai.db` was never actually
    regenerated with corrected Jan-1 dates (still has the old
    three-way-mixed dates) and is missing the entire 2025 quote folder
    (9 files never ingested). **Re-ingestion still has not been run.**

## What's NOT yet built (the real remaining work, in dependency order)

| # | Item | Depends on |
|---|---|---|
| A | Run re-ingestion (all 4 year-folders, corrected dates) | Nothing — can run today |
| B | Fix `find_similar_quotes` `AttributeError` | Nothing — can fix today |
| C | Cherry-pick `numerical_eval_harness` scope-expansion, rebase, regenerate goldens | A |
| D | Redesign `scope_matched_estimate()` return shape (per-category breakdown) | A, C |
| E | Add IVW × recency combined weighting (per-category most-recent-year scheme) | D |
| F | Structural add-ons: own 2025→today inflation, centralized in `structural_cost.py` | Nothing — independent |
| G | **Rewire `handle_construction_planning()` (Tab 2) to consume D's per-category breakdown**, keep era/floor/wall/elevator/gas logic layered on top, keep dated fallback constants for thin/empty categories | D, E, F |
| H | Drop `district` from `find_similar_quotes` `num_cols` | Nothing — trivial, low priority |

**G is the step that makes any of this visible in the app you actually
click through.** Everything before it is necessary but silent.

## Suggested sequencing for implementation prompts

Given the size, this should be at least 2-3 separate opencode runs, not
one giant prompt:

1. **Quick wins, independent, low-risk** — B, F, H together (bug fix +
   structural add-on dating + district cleanup). Good Flash candidate.
2. **Data + scope-map foundation** — A, C together (re-ingest, then
   cherry-pick/rebase the scope expansion). Needs Pro — cross-branch
   reconciliation.
3. **The redesign itself** — D, E (per-category return shape + combined
   weighting). Needs Pro — touches core estimator logic, needs careful
   testing against real numbers.
4. **The rewiring** — G (the actual Tab 2 integration). Needs Pro, and
   probably the most important one to review carefully line-by-line
   before merging, since it's the one that changes what you see.
