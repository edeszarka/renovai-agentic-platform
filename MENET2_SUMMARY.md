# MENET2_SUMMARY — Chain Rules, Structural Cost & Owner-Purchased Pricing

**Session model:** `opencode/deepseek-v4-flash-free` (exact model ID `opencode/deepseek-v4-flash-free`, stated per session config).
**Date:** 2026-08-15. **Branches:** `feature/menet2-chain-rules-and-pricing` (all T0–T5). No upstream target yet (human decision: do NOT merge to main this session).

---

## Scope of Menet 2

Carry Menet 1's deferred items forward: implement the doc 02 `CHAIN_RULES`
rule engine (era/branch-aware), building type/era weighting, structural-cost
chains and minimums, the doc 04 owner-purchased product pricing catalog, then
wire the 21-case golden dataset into a scoring harness and refresh
`docs/ARCHITECTURE.md`. **Explicitly NOT wired this session:** floor_number,
elevator_type and renovation_completeness (captured in T2B/2C but **deliberately
not weighted** into `scope_matched_estimate`).

---

## Task-by-task summary

### T0 — Baseline
- Full suite re-verified and clean at session start on `da3e67b`: **212 passed / 4 skipped** (matches Menet-1 end-of-branch figure after T5 commit).

### T1 — Chain rule engine (doc 02) + structural cost
- `renovai/predictor/structural_cost.py` implements the doc 02 `CHAIN_RULES`
  structed config (era/type-gated branches) and structural helpers.
- Branch A = pre-1970 tégla full-slag `ajto_padlo_lanc`; branches B/C =
  post-1970 tégla / csúszózsalus / panel `misung_subfloor_leveling`.
- Chain costs are **area-scaled** (base + per-sqm) from a **55 m² reference**
  (doc 03 item 3 reference), the point/range figures matching doc 03: type 1
  full-slag 2.0–2.85M, type 2 misung 1.25–1.6M.
- Pydantic guard (`CostBreakdownOutput`) fails loudly if a defined chain is
  silently omitted — a defined chain can never drop out of an estimate.
- Additional structural pieces: ceiling-height wall-area multiplier
  (`ceiling_height_multiplier`, reference 2.75 m), infrastructure minimums
  (electrical 300k, gas heating 800k), logistics surcharge (15%), elevator
  surcharge placeholder, chimney-technician conditional inclusion.
- Era mapping follows the canonical int-year representative (Menet 1), not band keys.

### T2 — Building type/era weighted estimate
- `price_model.py` weights `scope_matched_estimate` by building type/era
  similarity; canonical type matching and a self-normalizing weighted mean
  (`571af9e`, `dded26f`).
- T2B: re-verification of type/era weighting (`MENET2_TASK2_VERIFICATION.md`),
  including **VÁLYOG removal** (`da3e67b`).
- T2C: capture of `renovation_completeness` (DB column + ingestion),
  `floor_number` and `elevator_type` (`MENET2_TASK2C_SUMMARY.md`, `19586ca`,
  `e6d24b6`, `9950f6b`). Captured but **not weighted** — see Scope.

### T3 — Owner-purchased product pricing catalog (doc 04) — `63ba386`
- `renovai/predictor/product_pricing.py`: structured lookup API over eight
  doc-04 sections (tile, laminate+XPS, sanitary, interior doors, paint, lamps,
  kitchen, appliances), each on the six-tier ladder
  (alsó … luxus) where applicable, ranges stored as `(low, high)`.
- **Confirmed NEW functionality**: owner-purchased / `tulajdonosi beszerzés`
  items were priced *nowhere* in the handler flow; `pricing.md` skill docs were
  orphaned (never loaded by the Python path). The catalog is the first real
  single-source-of-truth for these.
- Wired into `handle_construction_planning` (new `Tulajdonosi beszerzés
  (termékek)` phase, `data_source=product_catalog_doc04`) and
  `handle_cost_estimation` (additive step 7 into `base_low/mid/high`,
  `adjustments["owner_purchased_huf"]`, `owner_purchased_items` in output).
- Doc 04 is canonical for appliances; the doc 03 #6 vs doc 04 §8 conflict
  stays **explicitly unresolved** (reconciliation note carried on the section).
- Skill docs (`pricing.md` ×2) updated with a "single source of truth is
  `product_pricing.py`" pointer.
- Tests: 25 new → product_pricing (22) + handler wiring (3).

### T4 — Golden-dataset validation (21 cases) — `dc4b50d`
- Built `scripts/validate_golden_dataset.py`: a structured scorer that maps
  each golden `case_id` onto the actually-implemented engine callables
  (chain trigger/cost, ceiling-height multiplier, product_pricing lookup /
  door_install) and evaluates the specific numeric rubric bullets that exist.
- **Honesty rule**: prose/knowledge-base cases Tasks 1-3 do not implement as a
  callable are reported **NOT-SCORABLE** with a reason — never fabricated as
  pass/fail. 100% pass is not expected and is not claimed.
- `appliance_package_011` remains **PENDING RECONCILIATION** (flag preserved,
  only the flag bullet is scored).
- Guarded by `tests/test_golden_validation_harness.py` (6 tests): all 21 cases
  processed, one status each, 003/009/018 PASS, 011 stays flagged.
- **Two-input support** (`cd79b45` follow-up): `subfloor_leveling_slag_vs_compound_003`
  carries two named sub-scenarios (type1 branch A / type2 branch B/C), each
  evaluated and reported separately under one case_id. No golden-schema change
  needed — case 003 already carried both type1/type2 params.
- **Source-cited recalibration** (`cd79b45`): `ajto_padlo_lanc` reference area
  corrected from 30 m² to 55 m², figures re-pinned to doc 03 item 3 type 1
  (2.0–2.85M); `misung_subfloor_leveling` re-pinned to doc 03 item 3 type 2
  (1.25–1.6M, low bound 1.21M→1.25M gap closed).

#### Task 4 pass/fail table (honest, current state)

| case_id | status | reason |
|---|---|---|
| wall_floor_reinforcement_001 | NOT-SCORABLE | item-level masonry price; no callable (Menet 3 candidate) |
| sawdust_wallpaper_vs_lime_002 | NOT-SCORABLE | item-level wall-prep price; near-win (Menet 3 candidate) |
| subfloor_leveling_slag_vs_compound_003 | **PASS** | two-input case: type1 (branch A, ajto_padlo_lanc 2.0–2.85M) + type2 (branch B/C, misung 1.25–1.6M) both green at 55 m² |
| tile_size_cost_comparison_004 | NOT-SCORABLE | item-level tile labor-by-size; near-win (Menet 3 candidate) |
| cement_vs_dispersion_waterproofing_005 | NOT-SCORABLE | item-level waterproofing price; no callable (Menet 3 candidate) |
| window_spaletta_restoration_006 | NOT-SCORABLE | item-level spaletta price; no callable (Menet 3 candidate) |
| wall_debris_weight_estimation_007 | NOT-SCORABLE | item-level debris estimation; no callable (Menet 3 candidate) |
| ajto_padlo_lanc_pre1970_008 | **PASS** | recalibrated to doc 03 item 3 type 1 (55 m² → 2.0–2.85M point 2.425M) — source-cited fix |
| high_ceiling_painting_plastering_009 | **PASS** | multiplier 3.5, ref 2.75, factor 1.273 ✓ |
| drywall_ceiling_010 | NOT-SCORABLE | item-level drywall price; no callable (Menet 3 candidate) |
| appliance_package_011 | PASS-FLAGGED | PENDING RECONCILIATION preserved; not scored (doc 03 vs doc 04 conflict) |
| screed_subfloor_concrete_012 | NOT-SCORABLE | item-level screed price; no callable (Menet 3 candidate) |
| old_plaster_rewalling_013 | NOT-SCORABLE | TODO — no HUF figures in doc 03 #9 |
| amperage_upgrade_32a_014 | NOT-SCORABLE | item-level 32A upgrade price; flat 300k ≠ doc 03 260–320k; no callable (Menet 3 candidate) |
| multisplit_ac_installation_015 | NOT-SCORABLE | item-level AC install price; no callable (Menet 3 candidate) |
| water_outlet_016 | NOT-SCORABLE | item-level outlet price; no callable (Menet 3 candidate) |
| monosplit_ac_3_5kw_017 | NOT-SCORABLE | item-level AC price; no callable (Menet 3 candidate) |
| interior_door_installation_018 | **PASS** | all 8 door/install variants match doc-04 golden ranges exactly ✓ |
| thermostat_valve_replacement_019 | NOT-SCORABLE | item-level valve price; no callable (Menet 3 candidate) |
| laminate_flooring_installation_020 | **FAIL** | owner-material present ✓; contractor install labor + adhesive is pricing.md text only — **deferred** (known documented gap, needs a coherent contractor labor structure) |
| paint_quantity_estimation_021 | NOT-SCORABLE | item-level paint-quantity estimation; no callable (Menet 3 candidate) |

Summary: **PASS=4, FAIL=1, PASS-FLAGGED=1, NOT-SCORABLE=15** (of 21).
Fully scorable: **003, 008, 009, 018** = 4/21. `020` is the single FAIL —
deferred, not silently dropped. 15 NOT-SCORABLE are item-level price questions
the whole-apartment CHAIN_RULES architecture has no callable for; 13 are
structural gaps, 2 (`002`, `004`) are near-wins (small lookup callable needed).

### T5 — `docs/ARCHITECTURE.md` refresh — `7381d98`
- Added "Menet 2: chain rules, structural cost & owner-verified pricing"
  section describing `structural_cost.py` (era-gated chains, ceiling-height
  multiplier, minimums) and `product_pricing.py` (doc 04 catalog) as
  single-source-of-truth predictor modules, plus the Task 4 validator.

---

## Menet 3 candidate — contractor trade-pricing catalog

The 15 NOT-SCORABLE golden cases are item-level price questions with **no
callable entry point** in the whole-apartment CHAIN_RULES architecture. The
natural fix is a **contractor trade-pricing catalog** mirroring
`renovai/predictor/product_pricing.py`'s structure — but for contractor
labor/material of a single work category instead of owner-purchased products.
This is new functionality, not a bug; a future session should split the golden
dataset into apartment-level cases (testing CHAIN_RULES / whole-apartment
estimate) versus item-level cases (testing the new catalog).

**Structural gaps (13 — need the new catalog):**

| case_id | item |
|---|---|
| `wall_floor_reinforcement_001` | masonry floor-reinforcement/wall-building material+labor by era |
| `cement_vs_dispersion_waterproofing_005` | cement vs dispersion waterproofing material |
| `window_spaletta_restoration_006` | spaletta frame restoration (two solutions) |
| `wall_debris_weight_estimation_007` | debris bag-count/tonnage/removal estimation |
| `drywall_ceiling_010` | suspended-ceiling per-sqm labor+material |
| `screed_subfloor_concrete_012` | screed per-sqm combined/labor/material |
| `old_plaster_rewalling_013` | replaster (no HUF figures exist yet — define first) |
| `amperage_upgrade_32a_014` | 32A upgrade + meter standardization |
| `multisplit_ac_installation_015` | multisplit install labor by config |
| `water_outlet_016` | per-outlet base + fitting |
| `monosplit_ac_3_5kw_017` | monosplit 3.5kW with units |
| `thermostat_valve_replacement_019` | valve+stub material and per-outlet labor |
| `paint_quantity_estimation_021` | paint volume estimation |

**Near-wins (2 — small lookup callable, data already documented):**

| case_id | item |
|---|---|
| `sawdust_wallpaper_vs_lime_002` | wall-prep extra material+labor (figures in pricing.md §2) |
| `tile_size_cost_comparison_004` | tile install labor by size (material already in TILE_PRICES) |

**Explicitly deferred (1):** `laminate_flooring_installation_020` — contractor
install labor (5.5k–7.5k/sqm) + adhesive (9k–14k) needs the same coherent
contractor-labor structure above, not an ad-hoc patch.

---

## Commit list (whole session, oldest → newest)

| Commit | Task | Summary |
|---|---|---|
| `a9a1430` | Menet 1 | docs(menet1): pin final commit hash in MENET1_COMPLETE |
| `36a9d63` | Phase 0 | chore(gitignore): ignore audit trail and db backup artifacts |
| `46285e6` | **T1** | feat(structural_cost): implement doc 02 branch A/B/C chain selection |
| `dded26f` | **T2** | feat(price_model): weight scope estimates by building type/era similarity |
| `571af9e` | **T2** | fix(price_model): canonical type matching and self-normalizing weighted mean |
| `67981ae` | **T2** | docs: Task 2 type/era weighting self-review verification |
| `9950f6b` | **T2C** | fix(ingestion): floor regex tolerates 'N. emelet'/comma; ground floor = 1 |
| `e6d24b6` | **T2C** | feat(db): add renovation_completeness column to quotes |
| `19586ca` | **T2C** | feat(ingestion): extract floor/elevator/completeness and apply to DB |
| `cf23c7f` | **T2C** | docs: MENET2_TASK2C_SUMMARY |
| `da3e67b` | **T2B** | docs: Task 2B deliverables (VÁLYOG removal + weighting re-verification) |
| `63ba386` | **T3** | feat(pricing): add owner-purchased product catalog (doc 04) and wire it into handlers |
| `dc4b50d` | **T4** | feat(evals): Task 4 golden-dataset validation harness for 21 cases |
| `7381d98` | **T5** | docs: Task 5 ARCHITECTURE — chain rules, structural cost & product pricing path |
| `cd79b45` | **T4-fix** | fix(structural_cost): correct ajto_padlo_lanc reference area from 30m² to 55m² per doc 03 source |
| `6ba00a5` | **T4-finalize** | feat(evals): two-input harness support (case 003) + misung low-bound recalibration to doc 03 + Menet-3 scoping |

---

## Test counts

| Milestone | Passed | Skipped | Notes |
|---|---|---|---|
| Baseline before T3 (`da3e67b`) | 212 | 4 | 4 fixture-missing skips in `test_quote_parser.py` |
| After T3 (`63ba386`) | **237** | 4 | +25 (22 product_pricing + 3 handler wiring) |
| After T4+T5 (`7381d98`) | **242** | 4 | +5 Task-4 harness guard tests |
| Final (`6ba00a5`) | **243** | 4 | +1 two-input harness guard test (case 003); misung/ajto_padlo_lanc coefficients re-anchored to 55 m² |

Run command: `python -m pytest -q` → `243 passed, 4 skipped`.

---

## Validation performed

- Full suite green at baseline (212/4), at T5 (242/4), and at final (243/4).
- `scripts/validate_golden_dataset.py` runs the 21-case golden set against the
  implemented callables → honest PASS/FAIL/NOT-SCORABLE table (above).
  Final: PASS=4 (003, 008, 009, 018), FAIL=1 (020, deferred), PASS-FLAGGED=1
  (011), NOT-SCORABLE=15.
- `appliance_package_011` PENDING RECONCILIATION note verified present and not
  silently scored.
- Ruff clean on new Task 4 files.

---

## Open items / TODOs

- **Item-level pricing gap (major, Menet 3):** 15/21 golden cases are
  item-level price questions with no callable in the whole-apartment
  architecture → NOT-SCORABLE. Scope defined in the "Menet 3 candidate"
  section above (13 structural gaps + 2 near-wins).
- **Laminate install labor (deferred):** 020's contractor labor (5.5k–7.5k/sqm
  + adhesive 9k–14k) is pricing.md text only; owner-side material is
  implemented. Needs the coherent contractor labor structure (Menet 3), not an
  ad-hoc patch.
- **`old_plaster_rewalling_013`:** doc 03 #9 gives no HUF figures (TODO in case) —
  define before numeric scoring.
- **Amperage 014:** flat constant 300k vs doc 03 260–320k range — reconcile
  within the Menet 3 contractor catalog.
- **[Deferred to user]** Appliance pricing conflict doc 03 #6 vs doc 04 §8
  (FAZIS_A_FINDINGS.md §3): stays PENDING RECONCILIATION; product_pricing uses
  doc 04 canonical. Resolve before trusting package totals.
- **[Not wired this session]** floor_number / elevator_type /
  renovation_completeness captured but not weighted into
  `scope_matched_estimate`.
- **No merge to main** — per human decision, review this branch as a whole.

---

MENET2_COMPLETE: feature/menet2-chain-rules-and-pricing, d3dc7cb, 243 passed / 4 skipped
