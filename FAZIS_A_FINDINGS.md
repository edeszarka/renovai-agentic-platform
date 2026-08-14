# Fázis A — Findings Report (read-only investigation)

**Model configured for this session:** `deepseek-v4-flash-free` (exact model ID `opencode/deepseek-v4-flash-free`, per the session config — stated, not guessed).
**Date:** 2026-08-14. **Scope:** read-only; no files modified, no branches, no commits.

> Path note: the four reference documents were provided as `/mnt/user-data/uploads/…`, which does not exist on this Windows host. They were located at their real location and read there:
> `C:\Users\Edesz\Downloads\0{1..4}_*.md`. Evidence below cites codebase paths relative to the repo root `C:\repok\renovai-capstone`.

---

## 1. Raw source data check — does building era/material/type exist in the raw source text?

**Answer: YES.** The info exists, but in two distinct forms:

- **(a) Structurally,** the building era and material are **only** embedded in the *filename* and in the `source:` / `address:` front-matter fields of the xlsx-derived markdown quotes — there is **no structured `building_type` / `building_era` column** anywhere in the quote pipeline output. See `data/processed/quotes_md/1015 Csalogány utca 12. fsz majdnem komplett, 1930-as évek, van salak, 32nm.md:2-11` (front-matter has `has_slag: false` but no era/type field; era appears only in `source:`).
- **(b) In free text,** era, material, and type are frequently stated explicitly in the work/notes narrative.

`data/sample_quotes/` itself is **empty** (only a README, `data/sample_quotes/README.md`). The real corpus lives in `data/raw/quotes/<year>/*.xlsx` (48 files) and their processed form `data/processed/quotes_md/*.md` (47 files).

**Three concrete example snippets with source files:**

1. **Building TYPE (panel) named explicitly, with a structural consequence:**
   `data/processed/quotes_md/1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm.md:110`
   > „…a panelben beton födémet vésni nem lehet és el kell, hogy férjen a tálca alatt a saját szifonja"

2. **Building ERA + FLOOR CONSTRUCTION TYPE (boltíves = brick-vault, pre-1900s era) with a mandatory technology rule:**
   `data/processed/quotes_md/komplett, 1900-as évek, van salak, 80-85nm.md:67`
   > „Boltíves födém esetén közvetlenül a födémről TILOS indítani a falat, mert a boltív leszakad, ezért kell a két gerenda között kvázi egy új födémet csinálni…"

3. **Building ERA + MATERIAL (12–15 cm slag bed, 1930s era) in demolition narrative:**
   `data/processed/quotes_md/1015 Csalogány utca 12. fsz majdnem komplett, 1930-as évek, van salak, 32nm.md:74`
   > „…le kell szedni a salakot 12-15cm vastagságban, különben túl lesz terhelve a födém."

**Supporting corpus-wide evidence** (grep across `data/processed/quotes_md/`):
- Era strings appear in virtually every quote filename/`source:` line (e.g. `1900-as évek`, `1920-as évek`, `1930-as évek`, `1960-as évek`, `1970-es évek`, `1980-as évek`, `1967`, `1958`, `2004`, `2005`, `2011`, `2013`, `kb. 2012`).
- Free-text era/age references: `komplett, 1958 építés éve, van salak, 51nm.md:43` → „65 ÉVES, RÉGI VAKOLATRA ÚJRA BURKOLNI NEM LEHET!"
- Material references: `kohósalak`/`salak`/`misung`/`metlaki` (e.g. `1125 György Aladár utca 7. komplett, 1960-as évek…:50,107,142`; `1092 Ráday utca 5…:56,95`), `tégláig` (plaster removed down to brick, e.g. `1081 II. János Pál pápa tér 22…:47`), `panel` (`1035…:110`, `1173 Budapest Újlak utca…:79`).
- **Correction to a working-hypothesis assumption:** because era/type live in the *filename* and *notes text*, they are **not currently extracted into any structured field** of the DB `quotes` table (see §4), so they cannot be used to filter cost estimates today.

---

## 2. Golden dataset gap analysis (doc 03 ↔ `renovai/evals/golden_dataset.json`)

`golden_dataset.json` contains **9 cases**. Doc 03 contains **19 numbered items**.
**7 of the 19 items have an exact golden counterpart (identical HUF figures). 12 are MISSING.**
The working hypothesis stated „9 of its ~19 items already exist verbatim" — **this is not confirmed; the accurate count is 7/19.** (Two of the 9 golden cases — `ajto_padlo_lanc_pre1970_008`, `high_ceiling_painting_plastering_009` — have **no counterpart in doc 03**; they trace instead to `structural_cost.py` CHAIN_RULES and `ceiling_height_multiplier()`.)

| Doc 03 item | Short description | Status | Golden case_id |
|---|---|---|---|
| 1 | Falazás: 1920 előtt vs 1920–1965 födém, 10 m fal, ytong | **MATCHED** (identical: 402 000 / 320 000–420 000; 249 000 / 280 000–380 000) | `wall_floor_reinforcement_001` (golden_dataset.json:3-46) |
| 2 | Fűrészporos tapéta vs meszelt fal (+80–100 e anyag, +180–280 e munka) | **MATCHED** | `sawdust_wallpaper_vs_lime_002` (:49-84) |
| 3 | Aljzat: kohósalak/homok vs ragasztó/misung, 55 nm 2 cm | **MATCHED** (700–850 e / 1.3–2 M; 650–750 e / 600–850 e) | `subfloor_leveling_slag_vs_compound_003` (:87-130) |
| 4 | Gipszkarton álmennyezet 9 500–11 500 / 5 000–6 200 Ft/nm | **MISSING** | — (data exists only in `.agent/skills/structural-core-specialist/references/pricing.md:60-67` and handler Phase 2b, `orchestrator/handlers.py:1202-1204`) |
| 5 | Burkolás lapmérettől (9–12 e / 6–8 e; 13–16 e / 7–9 e Ft/nm) | **MATCHED** | `tile_size_cost_comparison_004` (:133-172) |
| 6 | Háztartási gép csomag | **MISSING** | — (no golden case; see §3 conflict) |
| 7 | Cement vs diszperziós vízszigetelés (130–180 e; 70–100 e) | **MATCHED** | `cement_vs_dispersion_waterproofing_005` (:175-217) |
| 8 | Esztrich 6–9 e Ft/nm | **MISSING** | — (data exists in `.agent/skills/masonry-specialist/references/pricing.md:12-18`) |
| 9 | 40–60 éves vakolt fal fürdőben — újra kell vakolni | **MISSING** | — |
| 10 | 32 amper bővítés mérőóra-szabványosítással ~260–320 e | **MISSING** | — (only flat constant `ELECTRICAL_STANDARDIZATION_MINIMUM = 300_000`, `renovai/predictor/structural_cost.py:247`) |
| 11 | Multisplit klíma | **MISSING** | — |
| 12 | Egy víz-kiállás 70–120 e | **MISSING** | — |
| 13 | Monosplit 3,5 kW ~170–220 e | **MISSING** | — |
| 14 | Spaletta bontás utáni helyreállítás (13–18 e / 40–65 e; 17–21 e / 50–85 e) | **MATCHED** | `window_spaletta_restoration_006` (:220-260) |
| 15 | Beltéri ajtók beszerelése | **MISSING** | — (data exists in `.agent/skills/finishing-interior-specialist/references/pricing.md:83-111` and doc 04 §4) |
| 16 | Kiállás cseréje termosztátos szelepre | **MISSING** | — |
| 17 | Falak súlya / törmelék (110–150 / 70–95 zsák, tonnák, bontás/lécipelés/sitt díjak) | **MATCHED** | `wall_debris_weight_estimation_007` (:263-319) |
| 18 | Laminált lerakás 5 500–7 500 Ft/nm + 9–14 e ragasztó | **MISSING** | — (data exists in `.agent/skills/finishing-interior-specialist/references/pricing.md:49-72`) |
| 19 | Festékmennyiség 45 l színes + 45–60 l fehér | **MISSING** | — (data exists in `.agent/skills/finishing-interior-specialist/references/pricing.md:32-47`) |

**Summary:** matched = items 1, 2, 3, 5, 7, 14, 17 (7 items). Missing = items 4, 6, 8, 9, 10, 11, 12, 13, 15, 16, 18, 19 (12 items).

**Reverse observation (golden cases not in doc 03):**
- `ajto_padlo_lanc_pre1970_008` (golden_dataset.json:322-358) — „ajtó/padló-lánc" chain-reaction cost 2.5–3 M Ft. Rooted in `structural_cost.py` CHAIN_RULES `ajto_padlo_lanc` (`renovai/predictor/structural_cost.py:104-134`), not doc 03.
- `high_ceiling_painting_plastering_009` (:360-389) — belmagasság-szorzó. Rooted in `ceiling_height_multiplier()` (`structural_cost.py:49-60`); related to doc 02's „Belmagasság-szorzó szabály" but is not a numbered doc 03 item.

---

## 3. Pricing conflict — doc 03 item 6 vs doc 04 section 8

Doc 04 §8 itself flags the conflict in its footnote (doc 04:90). The two sources describe the **same product set but produce non-identical, tier-less vs tiered figures**. Doc 03 #6 gives one flat range per appliance (doc 03:25-30); doc 04 §8 gives six quality tiers per appliance (doc 04:80-88).

| Appliance | doc 03 #6 (flat, single range) | doc 04 §8 — tier ranges it overlaps | Nature of discrepancy |
|---|---|---|---|
| Főzőlap (4-zónás, **nem indukciós**) | 100–140 e Ft | Alsó 45–55 · AK 55–65 · KK 65–85 · FK 90–120 · **Prémium 120–180** | No single doc 04 tier contains 100–140. It sits above KK (65–85) and straddles the FK/Prémium boundary (120). doc 04 does not distinguish induction vs non-induction. |
| Elektromos sütő | 100–140 e Ft | Alsó 60–90 · AK 90–110 · KK 110–130 · FK 130–170 · Prémium 180–220 | No single tier contains 100–140; spans the AK/KK/FK boundary (90–140). Prémium starts at 180 (above doc 03). |
| Mosogatógép (12 terítékes) | 120–160 e Ft | Alsó 100–120 · AK 100–120 · KK 120–140 · FK 140–180 · Prémium 180–250 | No single tier contains 120–160; sits on the KK/FK boundary. Prémium starts at 180 (above doc 03 upper). |
| Szagelszívó (kihúzható, beépíthető) | 30–50 e Ft | Alsó 25–35 · AK 30–40 · KK 35–45 · FK 40–55 · Prémium 80–150 | doc 03's 30–50 spans **three** tiers (AK/KK/FK) — the widest mismatch. doc 04 doesn't specify „kihúzható". Prémium (80–150) is far above doc 03. |
| Hűtő (**alul fagyasztós**) | 130–170 e Ft | Alsó 120–150 · AK 120–150 · KK 140–180 · FK 160–220 · Prémium 200–260 | No single tier contains 130–170; straddles KK/FK (140–170). doc 04 doesn't specify „alul fagyasztós". |

Additional set-level differences (documented, not resolved):
- doc 03 #6 has **no mosógép or mikró**; doc 04 §8 includes both.
- doc 03 #6 explicitly says **non-induction** hob; doc 04 does not qualify it.
- doc 03 #6 presents a single "package" value with no quality tier anchor; doc 04 §8 is tiered by quality.

Decision on which number wins / how to deduplicate is left to the user (per instruction, no resolution attempted).

---

## 4. RAG wiring scope check

**Which handlers instantiate `RAGPipeline`: exactly ONE — `handle_due_diligence`.**
- Import: `orchestrator/handlers.py:566`; instantiation: `orchestrator/handlers.py:618` (both inside `handle_due_diligence`, :531-683).
- `RAGPipeline` is also instantiated by the eval runner `renovai/evals/runner.py:8,16` (EvalRunner), but no other handler uses it.

**`handle_expert_interview` (handlers.py:685-892): does NOT use RAG.** It is pure rule-based red-flag logic (era/type conditions at :735-826; e.g. pre-1960 slag :735-749, panel 1965–1985 aluminium wiring :752-768, pre-1920 foundation :771-792, betontálcás 1920–1965 :795-811, sawdust wallpaper :814-826).

**`handle_construction_planning` (handlers.py:899-1721): does NOT use RAG.** It uses the DB-backed corpus estimator `scope_matched_estimate` (:991-999) + `renovai.predictor.structural_cost` functions (:1035-1041) + hardcoded 2025 phase constants. No RAG import inside it.
- `handle_cost_estimation` (handlers.py:75-389): also **no RAG** — uses `scope_matched_estimate` + `find_similar_quotes` + `structural_cost` (:123-125, :176-183, :193-199).

**`scripts/build_vector_store.py` ingest sources (confirmed at build_vector_store.py:29-30):**
- `data/processed/quotes_md/` (default `--quotes-md-dir`) via `chunk_all` (chunker.py:221)
- `data/raw/video_transcripts/` (default `--transcripts-dir`) via `chunk_all_transcripts` (chunker.py:292)
- Persists to `data/chroma_db/`. **None** of the four new documents' content is ingested today.

**Existing counterpart data structures for doc 02/doc 04 content types (beyond `CHAIN_RULES`):**

Structured decision-tree/rule content (doc 02) is partially represented as:
- `renovai/predictor/structural_cost.py` — CHAIN_RULES (:104-134), ceiling-height multiplier (:49-60), infrastructure minimums (:247-298), logistics (:304-307), elevator (:316-329), chimney (:338-352).
- `renovai/predictor/price_model.py` — `SCOPE_CATEGORY_MAP` dict (:16-59), per-category `min_premium` constants.
- `.agent/skills/construction-planner/references/sequencing_rules.md` — mandatory phase order + per-phase cost ranges.
- The hardcoded phase branching inside `handle_construction_planning` (handlers.py:1133-1556).

Static pricing-catalog content (doc 04) **already has partial counterparts** as markdown reference tables:
- `.agent/skills/finishing-interior-specialist/references/pricing.md` — tile quality-tier table = doc 04 §1 (:14-21); interior doors = doc 04 §4 (:83-111); interior paint = doc 04 §5 (:41-47); laminate by thickness = doc 04 §2 (:58-72); waterproofing, wallpaper, paint quantity (= doc 03 items).
- `.agent/skills/finishing-specialist/references/pricing.md` — same content (duplicated).
- `.agent/skills/masonry-specialist/references/pricing.md` and `structural-core-specialist/references/pricing.md` — doc 03 items 1/3/8/14/17 and doc 03 #7 partial.
- `legacy/mcp_server/tools/newbuild_comparator.py:15-39` — `NEWBUILD_PRICE_TABLE` dict (per-district new-build Ft/m²; not owner-purchased items, but a priced lookup table).
- `.agent/skills/cost_estimate/references/work_categories.md` and `.agent/skills/market_data_query/references/work_categories.md` — percentage-based category lookup.

**Content with NO existing counterpart anywhere** (confirmed by grep over `*.md` + `*.py`): household appliances (doc 03 #6 / doc 04 §8), szaniterek (doc 04 §3), lámpák (doc 04 §6), konyhabútor (doc 04 §7), multisplit/monosplit/amperage/water-kiállás/thermostat-valve (doc 03 #10-13, #16). Grep for `mosogatógép|szagelszívó|főzőlap|hűtő|háztartási gép|szaniter|lámpa|konyhabútor|amper` returns no hits in the pricing/tables, only incidental prose.

**UI vs backend gap (confirms the working hypothesis):**
- UI sends `building_type` from a selectbox: `app/streamlit_app.py:442-444` (Tab 1, sent at :544) and `:694-695` (Tab 2).
- But `ApartmentInput` (`renovai/predictor/feature_extractor.py:70-84`) has **no `building_type` field** — only `building_era: Optional[int]`. `handle_cost_estimation` (:145-158) and `handle_construction_planning` (:976-989) both build `ApartmentInput` without it. `QuoteFeatures` (:46-68) likewise lacks it.
- `building_type` is consumed only by advisory paths: `handle_due_diligence` (handlers.py:587) and `handle_expert_interview` (:723, :752) and `renovai/advisor/pre_purchase.py:25`.
- DB: `renovai/db/models.py:11-40` — `Quote` table has `has_slag_complication`, `area_sqm`, `ceiling_height`, `elevator_type`, `has_gas_heating`, `floor_number` but **no `building_type` and no `building_era` column**.

---

## 5. `structural_cost.py` CHAIN_RULES coverage vs doc 02 (sections A/B/C)

`CHAIN_RULES` (`renovai/predictor/structural_cost.py:104-134`) currently contains **exactly ONE rule**:

- `ajto_padlo_lanc` — trigger: `_era_pre_1970(era)` AND (`windows_doors` OR `flooring`) (:95-102, :107-110). Steps: salak feltárul → kitermelés → EPS → esztrich → szintezés → új padló (:111-118). Costs: base + per-sqm (2025-dated, inflated at consumption).

Doc 02 defines **three building-type/era branches** (A: 1970 előtti tégla; B: 1970–1990/95 tégla + tégla falazatú csúszózsalus; C: panel + 1960/70 utáni beton csúszózsalus) each with a decision tree of work-item sub-branches. Coverage per decision point:

| Doc 02 decision point | In CHAIN_RULES? | Where it lives / status |
|---|---|---|
| Branch selection A/B/C by type+era | **NO** | No rule keyed on building **type** at all; the single rule is era-only (<1970). |
| Salak 12–15 cm (A) vs 1–3 cm misung (B/C) | **PARTIAL** | Pre-1970+floor work → `ajto_padlo_lanc`. The B/C „misung-csiszolás only" path is not a rule (it exists only as golden eval case `subfloor_leveling_slag_vs_compound_003` and in masonry pricing.md). |
| Tokostul vs tokba épített ablakbontás | **NO** | Prose question only. |
| Távfűtés vs egyéni fűtés | **PARTIAL** | `gas_heating` flag drives `apply_infrastructure_minimums` (:250-298) and `chimney_technician_cost` (:341-352); no távfűtés branch. |
| **Amperage selection (1×32 vs 3×16)** | **NO** | Only flat `ELECTRICAL_STANDARDIZATION_MINIMUM = 300_000` (:247). No amperage parameter/branch (doc 02:24-26). |
| Klíma db count | **NO** | Only `needs_ac` min_premium 300k (`price_model.py:53-58`). No per-unit/multisplit logic. |
| Kéménybélelés (csak egyéni fűtés; méter az emeletmagasság szerint) | **PARTIAL** | `chimney_technician_cost` + `GAS_HEATING_INFRA_MINIMUM` (:248, :338-352); no bélelés-meter rule. |
| Betonozás (A) vs 1–3 cm aljzatkiegyenlítés (B/C) | **PARTIAL** | Full-slag removal covered by `ajto_padlo_lanc`; the B/C „aljzatkiegyenlítő only" path is not a rule. |
| Vízszigetelés típusa (épített zuhany→cement, tálca/kád→diszperziós; B/C: szintemeléssel) | **NO** | Not a rule. Exists only as handler Phase 5 `has_shower` bump (handlers.py:1383-1389) and eval case `cement_vs_dispersion_waterproofing_005`. The „szintemelés" constraint is absent. |
| Spalettázás ytong vs gipszkarton | **NO** | Only golden eval case `window_spaletta_restoration_006`. |
| Dobozolható wc → gipszkarton | **NO** | Absent. |
| Lichtoff / szagelszívó (panel: nincs, belmagasság elégtelen) | **NO** | Absent. |
| Falazás módja (ytong/tégla/gipszkarton; erától és födémtípustól függ) | **NO** (partially elsewhere) | Handler Phase 2 handles pre-1920 boltíves födém-megerősítés vs 1920–1965 betontálca (handlers.py:1157-1198); panel „falazás nem releváns" absent. |
| Belmagasság-szorzó (2,5–3 m → ×2,5–3; 3,2–4 m → ×3,5; 4 m+ → ×4) | **YES (function, not CHAIN_RULE)** | `ceiling_height_multiplier` (structural_cost.py:49-60) + `apply_height_surcharge` (:62-84), applied in both handlers. Implements doc 02's rule as discrete buckets (2.5 / 2.75 / 3.0 / 3.5 / 4.0). |
| Q3/Q4 glettelés | **PARTIAL** | Q3 referenced in wallpaper path (handlers.py:1346-1350); Q4 absent. |
| Padló melegburkolás (laminált/vinyl/parketta; halszálka/eltolás; hajó/svédpadló) | **NO** | Absent (doc 03 #18 laminált is MISSING from eval too; only in finishing pricing.md). |
| C: 10+ emeletes — falbontás nem engedett | **NO** | `floor_number` exists but no 10-floor restriction rule. |
| **Padlófűtés lehetősége korszak szerint** (A: igen; B: nem; C: igen, egyéni fűtésnél) | **NO** | Absent entirely. |
| Egyéb (kaputelefon, sitt elvitel, segédmunka, cirkó beüzemelés, beltéri ajtó db) | **NO** | Partially absorbed by logistics surcharge (:304-307); otherwise absent. |

**Bottom line:** of doc 02's decision points, only **one is a CHAIN_RULE** (the pre-1970 salak chain) and only **one is a dedicated function** (ceiling-height multiplier). The rest of doc 02's branching (amperage selection, waterproofing type by shower, floor-heating feasibility by era, spaletta type, 10-floor restriction, misung path, panel-specific rules) is either hardcoded in handler phases or **completely absent**.

---

## Open questions for the user

1. **Building-type taxonomy mismatch.** Doc 01 defines `lakás: tégla / panel / csúszózsalus` and `ház: könnyűszerkezetes / tégla családi ház / vályog` with eras `1950 előtt / 1950–70 / 1970–2000 / 2000 után`; the UI (`app/streamlit_app.py:437-444`) uses `tégla / panel / újépítés / ismeretlen`; `ApartmentInput` has no type at all; advisory handlers accept free strings. Which taxonomy becomes canonical, and does it need a DB migration?
2. **Golden dataset count.** Working hypothesis said 9/19 doc-03 items exist; actual is 7/19. Should the 12 missing items be added as golden cases, and should the 2 golden cases without a doc-03 source (`008`, `009`) stay?
3. **Pricing conflict (doc 03 #6 vs doc 04 §8).** Left unresolved by design — which source wins, and should a single deduplicated appliance price table be created?
4. **Where should doc 04's catalog live?** The finishing/masonry skills already hold parts of it in `.agent/skills/*/references/pricing.md` (some duplicated across skills). Should doc 04 content extend those files, or become a new shared structure?
5. **Doc 01's „10/a–f" sub-question letters** are referenced (doc 01:56-60) but never enumerated in any of the four documents. Where is their definition?
6. **`building_era` value format.** The codebase mixes `"1960_1990"` (`pre_purchase.py:588`), int `1960` (`feature_extractor.py:74`), and strings like `"1980 előtti tégla"` (`golden_dataset.json:224`). Which is canonical for the rule engine?
7. **Ceiling-height multiplier fidelity.** Doc 02 says 2,5–3 m → „alapterület × 2,5–3"; the code (`structural_cost.py:52-60`) uses a fixed 2.75 in that band. Is the 2.75 default acceptable, or must the range be modeled continuously?
