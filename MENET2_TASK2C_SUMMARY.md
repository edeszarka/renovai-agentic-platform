# MENET2_TASK2C_SUMMARY — Floor/Elevator/Completeness Capture + Floor Regex Fix

**Branch:** `feature/menet2-chain-rules-and-pricing`
**Deliverable for:** Task 2 follow-up (Task 2C) — fix the confirmed floor-number
regex bug, investigate elevator presence, add `renovation_completeness`, and
re-verify Task 2's weighting effect + scope usage.

---

## Task A — Floor regex fix (`renovai/ingestion/address_extractor.py`)

**Confirmed bug:** `ADDRESS_REGEX` required a floor digit to be immediately
followed by whitespace before `emelet`, so `"2. emelet"` (digit-period-space)
and `", 3. emelet"` (comma field-separator) never matched — verified: all 47
`floor_number` values were NULL despite floor clauses in 18 filenames.

**Fix:** the two broken floor groups (`_\s+(\d+)[_\s]+emelet` + a `em(?:elet)?`
variant, groups 4/5) are replaced by one tolerant group:

```
(?:[_\s,.]*(\d+)[_\s.]*emelet)?
```

(group 4) and the extraction line changed to `floor = match.group(4)` (the old
`group(5)` reference, which no longer exists, is removed). Accepts any
separator run (period/comma/space/underscore) before and after the digit.

**Verified on the 3 user-quoted filenames:**

| filename | floor extracted |
|---|---|
| `1092 Ráday utca 5. 2 emelet, komplett, ...` | `2. emelet` |
| `1126 Hollósy Simon utca 30. 2 emelet, komplett, ...` | `2. emelet` |
| `1065 Bajcsy-Zsilinszky út 19, 3. emelet, részleges, ...` | `3. emelet` |

**Regression tests** added in `tests/test_quote_parser.py` (3 floor-variant
cases + no-floor-clause) and `tests/test_backfill_taxonomy.py`
(`test_task2c_floor_regression_three_filenames`).

**Ground-floor convention (aligned with the codebase, confirmed from code):**
`floor_number = 1` is the ground floor — `structural_cost.elevator_surcharge`
treats `("none", 1)` as 0 floors above ground and `("none", 5)` as 4
(`4 × 50_000 Ft`); `chimney_technician_cost(1)` = base, `(3)` = base + 2 steps.
So `fsz` / `fszt` / `földszint` → 1 (NOT 0), `N. emelet` / `N emelet` → N.
`extract_floor_number` was updated from ground→0 to ground→1 accordingly (tests
updated).

**Commit:** `9950f6b` — `fix(ingestion): floor regex tolerates 'N. emelet'/comma; ground floor = 1`

## Task B — Elevator presence (`elevator_type`)

**Investigation:** grep across all 47 markdown bodies for
`lift|felvitel|gyalog|teherlift|személylift|nincs lift|lift nélkül`.

- 6/47 files mention a lift **in free text** (above the "mentions exist" bar):
  - `1035 Szellő utca` — "ha lehet használnia liftet", "csak akkor érvényes, ha lehet használni a liftet"
  - `1024 Rózsahegy utca` — "Abban az esetben, ha lehet liftet használni"
  - `1123 Kék Golyó utca` — "ha lehet használni a liftet. Ha nem lehet használni, +200 000 Ft"
  - `1139 Teve utca` — "ha van lifthasználat"
  - `1173 Újlak utca` — "ha lehet használni a liftet"
  - `komplett, 1958 építés éve, 58nm` — "lehet anyagmozgatásra használni a liftet"
- **No** explicit `nincs lift` / `lift nélkül` / `gyalog` anywhere → no `"none"` and no size (small/large) determinable.
- All 6 are conditional-use quotes that presuppose a lift exists → generic `"present"`.

**Extraction rule** (in `scripts/backfill_building_taxonomy.py`): explicit
absence (`nincs lift` / `lift nélkül` / `lift nincs`) → `"none"`; any other
lift word (`\blift\w*\b`) in the body → `"present"`; no mention → `None`
(do not force a value). Kept honest: the value is `"present"` (lift exists,
size unknown), NOT `small`/`large`.

**Applied:** 6/47 → `elevator_type = "present"` (41 NULL).

**Commit:** `19586ca` (with Task C extraction).

## Task C — `renovation_completeness` column + extraction

**Schema:** new nullable `VARCHAR` column on `quotes`
(`renovai/db/models.py`), migration
`alembic/versions/2d3124aea397_add_renovation_completeness_to_quotes.py`
(chain: `13b971e8e43f → 6f5825579375 → 2d3124aea397`). Applied via
`alembic upgrade head`.

**Extraction** (strict literal-token grammar, values `"reszleges" | "komplett" | None`):

- `komplett` (bare token) → `komplett`; `részletes és komplett` → `komplett`
- `részleges` (bare token) → `reszleges`
- blends `majdnem komplett` (×2) and `többnyire komplett` (×1) → `None`
  (grammar is wider than the strict binary; **reported, not force-fit**)

**Split (44/47 strict):** `komplett` 20, `reszleges` 24, neither/unclear 3
(2× `majdnem komplett`, 1× `többnyire komplett`).

**Commit:** `e6d24b6` (schema), `19586ca` (extraction + backfill).

## Task D — Task 2 weighting effect + scope usage re-verified

Re-verified against current code; the earlier Task 2/E conclusion still holds:

1. **`scope_matched_estimate`** (`renovai/predictor/price_model.py:286`) weights
   quotes by **IVW × recency × building-similarity** only. The similarity term is
   `building_similarity_weight(apt, quote) = building_type_similarity × era_similarity`
   (`price_model.py:156`) — **type and era are the only quote fields that feed
   the empirical weighting** (the panel/tégla Δ of +0.13%, stress −0.92%/−1.06%
   from Task 2E).
2. **Not weighted here:** `renovation_completeness` is captured on the model but
   read **nowhere** in estimation (only backfill + tests reference it) — it is
   genuinely capture-only until a completeness-vs-price correlation is measured.
3. **`floor_number` / `elevator_type`** are NOT part of the empirical weighting
   either. They enter the price only as additive structural surcharges that
   already existed: `elevator_surcharge` (`structural_cost.py:403`, charges only
   `"none"` as `max(0, floor-1) × 50_000`) and `chimney_technician_cost`
   (`structural_cost.py:426`, steps with floor). With all corpus rows
   `elevator_type = "present"` (or NULL), those surcharges charge nothing new.

**Conclusion:** Task 2's weighting effect is confirmed and unchanged; the three
newly captured fields are captured-for-future-use and do not disturb it.

## Backfill & test status

**DB (SQLite `data/renovai.db`, head `2d3124aea397`):**

| field | populated |
|---|---|
| `floor_number` | 18/47 |
| `elevator_type` | 6/47 (all `present`) |
| `renovation_completeness` | 44/47 (20 komplett, 24 reszleges, 3 None) |
| `building_type` / `building_era` | 47/47 (unchanged from Task 2B) |

**Tests:** full suite **212 passed, 4 skipped** (up from 196). `ruff`: no new
findings on changed paths (the 5 reported are pre-existing, incl. the
`address_parts` dead assignment that predates this task).

## Commits (this task)

1. `9950f6b` — floor regex fix + ground-floor=1 + regression tests
2. `e6d24b6` — `renovation_completeness` column + migration
3. `19586ca` — floor/elevator/completeness extraction + DB backfill

---

TASK2C_COMPLETE: floor_number 18/47, elevator_type 6/47 (all "present", no explicit absence found), renovation_completeness 44/47 (20 komplett / 24 reszleges / 3 neither — majdnem/többnyire komplett, reported not force-fit), Task 2 weighting effect confirmed unchanged (type×era only)
