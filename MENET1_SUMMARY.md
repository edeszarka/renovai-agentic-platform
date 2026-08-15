# MENET1_SUMMARY — Building Taxonomy Data Layer & Golden Dataset Expansion

**Session model:** `opencode/deepseek-v4-flash-free` (exact model ID `opencode/deepseek-v4-flash-free`, stated per session config).
**Date:** 2026-08-14. **Branches:** `feature/building-taxonomy-data-layer` (T1–T4, merged to `main` via PR #6) and `feature/menet1-golden-expansion` (T5).

---

## Scope of Menet 1

Canonicalize the building **taxonomy** (`building_type` / `building_era`) into a single, structured, int-representative data layer, and bring the doc-03 calibration eval corpus up to full coverage. **Out of scope (deferred to Menet 2):** rule-engine logic (`CHAIN_RULES` expansion, `price_model.py` weighting, product pricing decisions, and wiring the golden dataset into a scoring harness).

---

## Task-by-task summary

### T1 — DB schema: `building_type` / `building_era` columns
- Added `building_type` (enum) and `building_era` (Integer) columns to the `Quote` table (`renovai/db/models.py`).
- Alembic migration `6f5825579375_add_building_taxonomy_columns_to_quotes.py`.
- Decision recorded: `building_era` is the **canonical representative year (int)**, matching the backfill extractor and `ApartmentInput`.

### T2 — Backfill extraction (`scripts/backfill_building_taxonomy.py`)
- Extracts `building_type` and `building_era` from quote filenames/front-matter.
- Era extraction handles exact years, ranges (midpoint), decades (+5), and bare years with bounds checking.
- Applies to DB rows with dry-run support; emits a summary report of covered/missing rows.

### T3 — UI → handler wiring (`building_type`)
- `ApartmentInput` gained a `building_type` field; Streamlit Tab 1/Tab 2 params now send `building_type`.
- `handle_cost_estimation` / `handle_construction_planning` pass it through to `ApartmentInput`.

### T4 — Era canonicalization (int, not band keys)
- `ApartmentProfile.building_era_approx` is now `Optional[int]` (representative year), not a band-key `Literal`.
- `ERA_MAP` keyed by representative years; `_era_label()` maps int year → band text for RAG queries.
- `generate_report` now forwards the canonical int era to `ApartmentInput` (previously dropped as `None`).
- Fixed a latent bug: `handle_due_diligence` defaulted to `"1960_1990"`, which is **not a valid Literal member** and failed `ApartmentProfile` validation; now defaults to `1975`.
- Call sites updated: `run_advisor.py` (bands → years), legacy/MCP profiles pass `None` explicitly.
- Added end-to-end test proving the int era reaches price-predictor features.

### T5 — Golden dataset expansion (12 missing doc-03 items)
- `renovai/evals/golden_dataset.json` expanded **9 → 21 cases** (all doc-03 items 1–19 now covered).
- Added: `drywall_ceiling_010` (#4), `appliance_package_011` (#6), `screed_subfloor_concrete_012` (#8), `old_plaster_rewalling_013` (#9), `amperage_upgrade_32a_014` (#10), `multisplit_ac_installation_015` (#11), `water_outlet_016` (#12), `monosplit_ac_3_5kw_017` (#13), `interior_door_installation_018` (#15), `thermostat_valve_replacement_019` (#16), `laminate_flooring_installation_020` (#18), `paint_quantity_estimation_021` (#19).
- Figures pulled **verbatim** from doc 03; cross-references to `pricing.md` cited in per-case `notes`.
- Item 6 (`appliance_package_011`) carries the mandatory **PENDING RECONCILIATION** flag (doc 03 flat vs doc 04 6-tier appliance pricing — FAZIS_A_FINDINGS.md §3). Not resolved by design.
- Added `tests/test_golden_dataset_schema.py` (9 tests): valid JSON, exactly 21 cases, no duplicate `case_id`s, all required keys, rubric is a non-empty string list, params is a dict, HUF `min ≤ max` pairs, and the reconciliation flag present.

---

## Commit list (whole session, oldest → newest)

| Commit | Task | Summary |
|---|---|---|
| `8e858e5` | Phase 0 | chore(baseline): record pre-refactor test baseline |
| `1b79397` | Phase 0 | docs(architecture): declare Streamlit+handlers canonical path and legacy entry points |
| `2b2370c` | Phase 0 | refactor(legacy): consolidate non-canonical entry points under `legacy/` |
| `50c2b20` | Phase 0 | feat(handlers): wire Green Team human-in-the-loop gate into live handlers |
| `85edd24` | Phase 0 | feat(policy): persist per-check audit trail on every allow/deny resolution |
| `ef39d6f` | Phase 0 | feat(structural): add Pydantic guard for chain cost-breakdown output |
| `4503aab` | Phase 0 | refactor(handlers,policy): complete type hints and docstrings on canonical path |
| `9a45dd2` | Phase 0 | docs(phase-0): add PR-style PHASE_0_SUMMARY.md |
| `32bf33b` | Menet 1 setup | docs(menet1): record test baseline and add Fázis A findings |
| `b2ac1c7` | **T1** | feat(db): add building_type enum and building_era columns to Quote |
| `24e75c2` | **T2** | feat(backfill): extract building taxonomy from quote corpus |
| `874d247` | **T3** | feat(ui): wire building_type through to ApartmentInput |
| `6736386` | **T4** | feat(advisor): canonicalize building_era to int in pre_purchase |
| `2d1ebd2` | **T5** | feat(evals): add 12 doc-03 golden cases and schema validation |

Merged to `main` via PR #6 (`b793647`). T5 is on `feature/menet1-golden-expansion`, ready for PR review.

---

## Test counts

| Milestone | Passed | Skipped | Notes |
|---|---|---|---|
| Pre-Menet1 baseline (`NOTES_BASELINE_MENET1.md`, branch start) | 137 | 4 | 4 fixture-missing skips in `test_quote_parser.py` |
| After T1–T4 (merged to `main`, start of T5) | 164 | 4 | Confirmed by running the full suite at T5 start |
| After T5 (this branch) | **173** | 4 | +9 new `test_golden_dataset_schema.py` tests |

Run command: `python -m pytest -q` → `173 passed, 4 skipped, 11 warnings`.

---

## Validation performed in T5

- `golden_dataset.json` parsed → **21 cases, all unique, required keys present**.
- `tests/test_golden_dataset_schema.py` → 9/9 pass.
- Full suite → 173 passed.
- **Note:** the existing RAG eval runner (`renovai/evals/runner.py`, `scripts/run_evals.py`) uses a *different* schema (`EvalExample`: id/question/keywords/sources/category) and cannot consume `golden_dataset.json` (`case_id/input/expected_output/rubric`). No dry-run mode exists in that runner, and it requires `GOOGLE_API_KEY` + ChromaDB. Per the task constraint (no calc/runner code changes in Menet 1), the schema test is the authoritative structural validation for the new cases. Wiring `golden_dataset.json` into a scoring harness is a Menet 2 concern.

---

## Open items / TODOs

- **[Deferred to user]** Appliance pricing conflict: doc 03 #6 flat ranges vs doc 04 §8 six-tier pricing (FAZIS_A_FINDINGS.md §3). `appliance_package_011` flagged **PENDING RECONCILIATION**; do not treat its figures as final.
- **TODO in case `old_plaster_rewalling_013`:** doc 03 item 9 gives **no HUF figures** (only the qualitative "replaster 2–3 cm, old mortar likely reaches brick"). Marked TODO; cost figures must be defined in Menet 2 before numeric scoring.
- **Menet 2 (next session):** expand `CHAIN_RULES` (branch A/B/C by type+era, amperage, waterproofing type, spaletta, 10-floor restriction, etc.), resolve appliance pricing, define cost figures for item 9, and wire `golden_dataset.json` into an evaluator.

---

MENET1_COMPLETE: feature/menet1-golden-expansion, 75a443b, 173 passed / 4 skipped
