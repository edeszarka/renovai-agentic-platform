# MENET2_TASK2B_SUMMARY — Building-Taxonomy Backfill Fix (PANEL-vs-TEGLA)

**Branch:** `feature/menet2-chain-rules-and-pricing`
**Deliverable for:** Task 2 follow-up (Task 2.5) — fix `building_type` under-detection
(5/47 → 47/47), remove VÁLYOG from the taxonomy, and re-verify Task 2's weighting effect.

---

## Task A — Filename-grammar inventory of the 47-file corpus

All 47 quotes (`data/raw/quotes/*/*.xlsx`) map 1:1 to markdown files in
`data/processed/quotes_md/` (stem-identical). The canonical filename grammar:

```
[<postal> <address>[, <floor: fsz|földszint|fszt|N. emelet|N emelet>],]
 <completeness: komplett|majdnem komplett|részleges|többnyire komplett|
                részletes és komplett|részleges-terasz>,
 <era phrase>, <slag flag: van/nincs salak>,
 [<area Nnm or N-Nnm>], [panel], [optional extras]
```

Per-file measurements (all 47 files):

| Metric | Count |
|---|---|
| Files with an era phrase | 47/47 |
| Literal "panel" token | 4/47 |
| Area `NNnm` in filename | 39/47 |
| Floor clause in filename | 18/47 |
| Exceptions (no area in filename) | 8/47 (files 7, 9, 10, 11, 14, 16, 23, 28) |

The 4 PANEL files (exact matches): Páskom park `(panel)`, Szellő utca `panel, 49nm`,
Újlak `53nm, panel`, Kisfaludy `73nm, panel`. Exactly one file carries `családi ház`.
"Téglási András utca" is a street name (word boundary prevents a false `tégla` match);
body-text type tokens are product terms (fűtéspanel, Ytong tégla).

## Task B — Root cause + fix

**Root cause of 5/47 under-detection:** `extract_building_type` only honored an explicit
type token (`panel`, `családi ház`, `csúszózsalus`, `könnyűszerkezetes`, `vályog`,
`tégla`) and defaulted to `None` otherwise. 42/47 filenames carry **no** type token at
all, so they all fell through to `None`.

**Fix (user-directed PANEL-vs-TEGLA rule):** rewritten `extract_building_type` in
`scripts/backfill_building_taxonomy.py`:

1. `\bpanel\b` (word-bounded, case/accent-insensitive) → `PANEL`
2. `\bcsaládi\s*ház\b` (clear, unambiguous evidence on exactly one file) →
   `TEGLA_CSALADI_HAZ`
3. Otherwise, if the text matches the Task A corpus grammar (an era clause is present —
   all 47 do) → `TEGLA` (default/majority)
4. Text that does NOT look like a corpus filename → `None` (left NULL, listed explicitly)

`csúszózsalus` / `könnyűszerkezetes` branches are dropped (no corpus evidence); `vályog`
is removed entirely. Also added `extract_floor_number` (fsz/fszt/földszint → 0,
`N. emelet`/`N emelet` → N) to support the Task B example-filename tests.

**Tests (`tests/test_backfill_taxonomy.py`):** removed `test_valyog`; changed
`test_ambiguous_*`/`test_teglasi_street_*` to assert the TEGLA default; added both
example filenames:
- `1015 Csalogány utca 12. fsz ... 1930-as évek, van salak, 32nm` → TEGLA, era 1935, floor 0
- `1123 Kék Golyó utca 30. 4 emelet, ... 1930-as évek, van salak, 42nm` → TEGLA, era 1935, floor 4

**Backfill result (re-run over all 47 md files):** 47/47 applied, 0 unresolved:

| building_type | count |
|---|---|
| PANEL | 4 |
| TEGLA | 42 |
| TEGLA_CSALADI_HAZ | 1 |
| NULL | 0 |

## Task C — VÁLYOG removal

`VALYOG_VEGYES` is removed from the `BuildingType` enum in `renovai/db/models.py`
(with a NOTE documenting the deliberate scope reduction) and from migration
`6f5825579375`. The DB column is plain VARCHAR (no CHECK constraint), so no data-layer
change was needed. No other code references VÁLYOG. `_CANONICAL_TYPE_TOKENS` in
`price_model.py` derives from the enum, so it auto-updates. `structural_cost.py` uses
string literals and never referenced vályog.

## Task D — area_sqm / floor_number spot-check (flagged, NOT fixed)

The `area_sqm` / `floor_number` columns pre-date Menet 1. Current DB state:

| column | populated |
|---|---|
| building_era | 47/47 |
| building_type | 47/47 |
| area_sqm | 37/47 |
| floor_number | 0/47 |

**Real gap found (reported per instructions, NOT fixed):**
- `floor_number` is NULL on all 47 quotes despite floor clauses in 18 filenames.
- 10 quotes whose filenames carry `NNnm` still have `area_sqm` NULL (e.g. Csalogány 32nm,
  Szellő 49nm, Lágymányosi 120nm, György Aladár 56nm, Szent László út 32nm, Teve 92nm,
  Újlak 53nm, Szalag 86nm, pápa tér 44nm).
- `renovai/ingestion/address_extractor.py` `ADDRESS_REGEX` fails to capture floors like
  `"4 emelet,"` and `"fsz"` (verified: `extract_address` returns `floor=None` for example
  filenames).
- `renovai/db/repository.py` `upsert_quote` never sets `area_sqm`/`floor_number`.

Out of scope for this task; reported for a future fix.

## Task E — Task 2 weighting effect re-verified on 47/47 coverage

Regression test `test_building_type_era_affects_estimate` (identical scope+area,
`panel/1975` vs `tégla/2005`) re-measured with full coverage:

| scenario | A panel/1975 | B tégla/2005 | Δ (B vs A) | B csúszózsalus/2010 | Δ |
|---|---|---|---|---|---|
| Committed curve (band=10, floor=0.3) | 5,311,155 | 5,317,910 | +0.13% | 5,306,473 | −0.09% |
| Sharpened era (band=25) | 5,320,192 | 5,316,554 | −0.07% | 5,305,353 | −0.28% |
| Max stress (band=0, floor=0.05) | 5,309,381 | 5,260,751 | −0.92% | 5,253,255 | −1.06% |

Per-scope weighted per-sqm averages DO differ (plumbing, electrical, demolition, AC);
`sim_min` drops as low as 0.175 (A) / 0.05–0.3 (B) across scopes. The total delta stays
small because the corpus era distribution is clustered pre-1935 and a "tégla" request now
matches 42/47 quotes (majority), so its weight vector is close to the panel request's.
With `csúszózsalus/2010` (max type distance, since vályog no longer exists) the extreme
stress case crosses −1%. The factor does real per-scope work; footprint is bounded.
`MENET2_TASK2_VERIFICATION.md` updated with the new numbers.

## Test & lint status

- Full suite: **196 passed, 4 skipped** (up from 190 passed before Task 2.5).
- `tests/test_backfill_taxonomy.py`: 28 tests (era 10, type 8, floor 3, taxonomy 3,
  backfill 4).
- `ruff`: 4 findings on the changed paths — all pre-existing (unused imports in
  `models.py` / the test module); zero new findings.

---

TASK2B_COMPLETE: 47/47 building_type coverage (4 PANEL, 42 TEGLA, 1 TEGLA_CSALADI_HAZ, 0 NULL), VÁLYOG removed from taxonomy, Task 2 weighting re-verified
