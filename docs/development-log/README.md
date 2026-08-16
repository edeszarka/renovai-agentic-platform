# Development Log

Summaries of the markdown files in this folder, for quick orientation.

## Phase 0 — Streamlit-canonical refactor

- **PHASE_0_SUMMARY.md** — Records the `refactor/phase-0-streamlit-canonical` branch work: moving non-canonical entry points under `legacy/`, adding the Green Team gate, audit trail, and Pydantic cost-breakdown guard. Includes the per-task commit list and final test status (137 passed, 4 skipped).

- **NOTES_BASELINE.md** — The pre-refactor test baseline captured before any Phase 0 changes, documenting the then-failing legacy-path tests (ADK stream, Vertex billing, stale `test_price_model.py`).

## Menet 1 — Building taxonomy data layer

- **FAZIS_A_FINDINGS.md** — Read-only investigation report confirming that building era/type/material info exists only in quote filenames and free-text notes, not in structured DB columns. The evidence base for the Menet 1 taxonomy work.

- **NOTES_BASELINE_MENET1.md** — The pre-Menet-1 test baseline (137 passed, 4 skipped) captured on the `feature/building-taxonomy-data-layer` branch before any changes were made.

- **MENET1_SUMMARY.md** — Summarizes the full Menet 1 session: canonicalizing `building_type`/`building_era` into the DB, the backfill script, UI wiring, era canonicalization to int years, and expanding the golden dataset from 9 to 21 cases.

## Menet 2 — Chain rules & pricing

- **MENET2_SUMMARY.md** — Summarizes the Menet 2 session: the doc-02 `CHAIN_RULES` rule engine, structural-cost chains and minimums, building type/era weighting, owner-purchased product pricing, and the golden-dataset scoring harness.

- **MENET2_TASK2_VERIFICATION.md** — Self-review of Task 2's type × era similarity weighting in `price_model.py`, checking the regression test strength and whether the change is sound enough for Task 3 to build on.

- **MENET2_TASK2B_SUMMARY.md** — Follow-up fix (Task 2.5) for `building_type` under-detection in the backfill (5/47 → 47/47 corpus coverage) and removal of the VÁLYOG taxonomy value.

- **MENET2_TASK2C_SUMMARY.md** — Follow-up (Task 2C) fixing the floor-number regex in the address extractor, investigating elevator presence, and adding `renovation_completeness` capture.

## Backlog

- **BACKLOG.md** — Informal list of future feature ideas, research topics, and nice-to-haves (e.g. RAG → agentic retrieval, EVALS, auth, dark mode).