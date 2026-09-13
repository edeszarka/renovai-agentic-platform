# Canonical Work-Category Taxonomy (Phase 1)

**Status:** proposed — awaiting repo-owner review.
**Scope:** research artifact only. This document does **not** change any code,
database schema, or customer-facing estimate. It reconciles the three existing
category lists and extends them bottom-up from the real corpus vocabulary.

---

## 1. Methodology

**Goal.** The repository carries three overlapping category taxonomies:

| # | Source | Count | Role |
|---|--------|-------|------|
| A | `renovai/db/models.py::SEED_WORK_CATEGORIES` | 17 | rows seeded into `work_categories`; `line_items.category_key` points here |
| B | `.agent/skills/cost_estimate/references/work_categories.md` | 11 | cost-estimate skill reference table |
| C | `renovai/predictor/price_model.py::SCOPE_CATEGORY_MAP` | 7 | customer-facing `needs_*` scope buckets |

This document proposes **one canonical taxonomy** that covers A, B and the real
vocabulary, and maps every key of A and B onto it.

**Corpus extraction (read-only).** `scripts/build_category_taxonomy.py` opened
the application database read-only (SQLite `mode=ro`) and extracted:

- **47 quotes**, **1 056 line items**
- **666 distinct `name_hu` strings** (the input to clustering)
- 797 distinct `notes_hu` strings were also extracted but are **not** used in
  this pass; they are available for a follow-up disambiguation pass on the
  ambiguous cases listed in §6.

**Clustering.** The distinct `name_hu` strings (with frequencies) and the three
existing taxonomies were sent to the LLM, which was asked to (1) produce one
canonical taxonomy reconciling A/B/C and the vocabulary, (2) map every A key and
every B key onto it, and (3) assign each distinct raw phrasing to exactly one
canonical key (batched, 100 phrasings per call). No price data was involved at
any point; this is pure text classification.

**LLM provider actually used (for provenance):**

- **DeepSeek** — `deepseek-chat` (primary, via `DEEPSEEK_API_KEY`)
- 8 calls total: 1 reconciliation + 7 vocabulary-assignment batches.
- **All 8 calls were served by `deepseek` / `deepseek-chat`.** Gemini was
  configured only as an optional secondary and was never needed.
- The dedicated helper `scripts/taxonomy_llm_client.py` logs the provider/model
  for every call and was unit-tested for its error policy
  (`tests/test_taxonomy_llm_client.py`).

Machine-readable clustering output (local, not committed):
`data/reports/category_taxonomy_clusters.json`.

**How much to trust this.** The clustering is a strong first pass; the member
lists below are the model's assignments, not a hand-verified ground truth. §6
lists the specific cases a human should double-check before this taxonomy is
used to write to any database column.

---

## 2. Canonical taxonomy (17 categories)

Keys are normalised to ASCII `snake_case` for consistency. The Hungarian/English
labels and definitions are the authoritative names.

| key | Hungarian label | English label | Definition | Used in customer estimates? |
|-----|-----------------|---------------|------------|-----------------------------|
| `bontas` | Bontás | Demolition | Építési szerkezetek, burkolatok, válaszfalak és beépített elemek elbontása, visszabontása; sitt képzése. | ✅ `needs_full_demolition` |
| `viz_futes` | Víz és fűtés | Plumbing & heating | Vízvezeték-, csatorna-, gáz- és fűtéscsövek szerelése, cseréje, átalakítása; radiátorok és szaniter bekötések. | ✅ `needs_plumbing` |
| `villany` | Villanyszerelés | Electrical | Elektromos hálózat, vezetékek, kapcsolók, konnektorok, mérőóra-szabványosítás és kaputelefon szerelése, bővítése. | ✅ `needs_electrical` |
| `klima` | Klíma | Air conditioning | Klímaberendezések telepítése, áthelyezése, karbantartása, hűtőközeg leeresztése/feltöltése. | ✅ `needs_ac` |
| `burkolas` | Burkolás | Tiling & surface covering | Hidegburkolatok (csempe, járólap) és melegburkolatok (laminált, vinyl) lerakása, fugázása, szegélyezése. | ✅ `needs_flooring` |
| `parketta` | Parketta | Parquet flooring | Parketta lerakása, csiszolása, lakkozása és javítása. | ✅ `needs_flooring` |
| `nyilaszaro` | Nyílászáró csere | Windows & doors | Ablakok, ajtók, redőnyök cseréje, beépítése, javítása és festése. | ✅ `needs_windows_doors` |
| `szigeteles` | Szigetelés | Insulation | Hő- és hangszigetelés (multipor, EPS, ásványgyapot); a gyakorlatban ide csoportosítva a fürdő vízszigetelése és a kéménybélelés is — lásd §6. | ✅ `needs_insulation` |
| `vakolas` | Vakolás | Plastering | Falak, mennyezetek vakolása, javítása, horonyvakolása, felületi egyenetlenségek megszüntetése. | ❌ not yet used in customer-facing estimates |
| `gletteles_festes` | Glettelés és festés | Painting & finishing | Felületek glettelése, csiszolása, mélyalapozása, festése és tapétázása. | ❌ not yet used in customer-facing estimates |
| `egyeb_komuves` | Egyéb kőműves | Misc masonry | Falazás, betonozás, aljzatkiegyenlítés, szintelőkészítés és egyéb kőműves munkák. | ❌ not yet used in customer-facing estimates |
| `szallitas` | Szállítás/segédmunka | Logistics & assistance | Anyagok mozgatása, sitt elszállítása, takarítás és egyéb segédmunkák. | ❌ not yet used in customer-facing estimates |
| `konyha` | Konyhabútor | Kitchen cabinetry | Konyhabútor, konyhapult, szagelszívó és beépíthető konyhai gépek beépítése, cseréje. | ❌ not yet used in customer-facing estimates |
| `furdo` | Fürdőszoba | Bathroom | Fürdőszobai szaniterek, zuhanykabinok, kádak, mosdók, csaptelepek és fürdőszobabútor beépítése, cseréje. | ❌ not yet used in customer-facing estimates |
| `futes_rendszer` | Fűtésrendszer | Heating system | Kazánok, radiátorok, konvektorok és fűtési rendszerek telepítése, cseréje, karbantartása, átmosása. | ❌ not yet used in customer-facing estimates |
| `gipszkarton` | Gipszkarton/álmennyezet | Drywall & suspended ceilings | Gipszkarton válaszfalak, álmennyezetek, dobozolások és dekorációs elemek készítése. | ❌ not yet used in customer-facing estimates |
| `egyeb` | Egyéb | Other | Minden egyéb, a fenti kategóriákba nem sorolható tétel (bútor, háztartási gép, lakberendezés, fizetési ütemezés, meta sorok). | ❌ not yet used in customer-facing estimates |

---

## 3. The 7 customer-facing scope buckets

Only these 7 buckets are currently wired into `SCOPE_CATEGORY_MAP` and therefore
into the live estimate. The canonical categories that feed them:

| scope bucket | canonical keys |
|--------------|----------------|
| `needs_plumbing` | `viz_futes` |
| `needs_electrical` | `villany` |
| `needs_flooring` | `burkolas`, `parketta` |
| `needs_full_demolition` | `bontas` |
| `needs_windows_doors` | `nyilaszaro` |
| `needs_insulation` | `szigeteles` |
| `needs_ac` | `klima` |

The remaining 9 canonical categories (`vakolas`, `gletteles_festes`,
`egyeb_komuves`, `szallitas`, `konyha`, `furdo`, `futes_rendszer`,
`gipszkarton`, `egyeb`) exist in the 17-seed list but are **not yet used in
customer-facing estimates**. Wiring them in is a separate, larger change.

---

## 4. Reconciliation of the legacy lists

### 4.1 `SEED_WORK_CATEGORIES` (17) → canonical

Every seed key is preserved; the canonical key is the ASCII-normalised form.

| seed key | canonical key |
|----------|---------------|
| `bontás` | `bontas` |
| `víz_fűtés` | `viz_futes` |
| `villany` | `villany` |
| `klíma` | `klima` |
| `vakolás` | `vakolas` |
| `burkolás` | `burkolas` |
| `glettelés_festés` | `gletteles_festes` |
| `parketta` | `parketta` |
| `egyéb_köműves` | `egyeb_komuves` |
| `szállítás` | `szallitas` |
| `szigetelés` | `szigeteles` |
| `nyílászáró` | `nyilaszaro` |
| `konyha` | `konyha` |
| `fürdő` | `furdo` |
| `fűtés_rendszer` | `futes_rendszer` |
| `gipszkarton` | `gipszkarton` |
| `egyéb` | `egyeb` |

### 4.2 `work_categories.md` (11) → canonical

| skill-doc key | canonical key | note |
|---------------|---------------|------|
| `villany` | `villany` | |
| `viz_futes` | `viz_futes` | accent-free spelling of the seed key |
| `burkolas` | `burkolas` | |
| `bontas` | `bontas` | |
| `festes` | `gletteles_festes` | the seed splits painting from plastering; canonical follows the seed |
| `nyilaszaro` | `nyilaszaro` | |
| `konyha` | `konyha` | |
| `furdo` | `furdo` | |
| `futes_rendszer` | `futes_rendszer` | |
| `szigeteles` | `szigeteles` | |
| `teljes` | *(none)* | "full renovation" is a scope selector, not a work category |

### 4.3 `SCOPE_CATEGORY_MAP` (7) → canonical

See §3. `SCOPE_CATEGORY_MAP` itself is **not modified**.

---

## 5. Synonym clusters (real corpus phrasings)

Each canonical category below lists representative **raw `name_hu` phrasings**
from the corpus (with frequency) that the LLM grouped into it — the
"excelenként más szóval ugyanaz" problem made explicit. The full member list is
in the machine-readable output.

### `bontas` — 13 distinct phrasings
- `Bontás` (30), `1. Bontás` (6), `Plusz bontás` (2), `16. Szoba bontása`, `Plusz bontás: salakkimerés 15cm-ben`, `Letakarás, fóliázás, bontás`, `Betonfal vágás`

### `viz_futes` — 36 distinct phrasings
- `Vízszerelés` (8), `Víz, fűtés szerelés` (5), `Víz-gáz-fűtés szerelés` (3), `Víz-gáz-fűtés szerelés, gáz meo, új gázcső készítés` (3), `Vízvezeték szerelés` (2), `2. Vízvezeték szerelés` (2), `19. Gázszerelés`, `21. Wc ejtő-és nyomócső csere`

### `villany` — 30 distinct phrasings
- `Villanyszerelés` (22), `Kaputelefon` (9), `Kaputelefon beüzemelése, új készülékkel` (5), `Kaputelefon szerelés` (3), `Hálózatbővítés` (3), `Villanyszerelés, 32 amperra történő hálózatbővítéssel` (2), `32 amperre történő bővítés, mérőóraszabványosítással` (2)

### `klima` — 15 distinct phrasings
- `Klíma` (13), `Klíma: 2 db beltéri, 2 db kültéri` (2), `Klíma alapszerelés`, `Klíma horony vésés`, `Klíma készülék áthelyezése`, `Klíma gáz leeresztése majd feltöltése`

### `burkolas` — 66 distinct phrasings
- `Burkolás` (24), `Csempe, járólap` (5), `Burkolatváltók` (4), `Laminált padló, szegőléc` (4), `Laminált padló` (3), `Vinyl úsztatva rakása és saját szegélyének ragasztása` (2), `Pozitív sarkok 45 fokos gérvágása` (2), `Hidegburkolat`, `Nagy lapos burkolás esetén`, `Konyha csempezés`

### `parketta` — 7 distinct phrasings
- `Parketta csiszolás` (3), `20. Parketta szegőléc ragasztással`, `12. Parketta csiszolás, lakkozás`, `Parketta csiszolás, lakkozás`, `Parketta és szegőléc ragasztása`, `Ragasztott szalagparketta esetén`, `Parketta csiszolása`

### `nyilaszaro` — 52 distinct phrasings
- `Beltéri ajtók berakása` (12), `Bejárati ajtó` (3), `1 beltéri ajtó áthelyezése` (2), `Kültéri nyílászárók: kívül színes, belül fehér…` (2), `Beltéri ajtók beszerelése` (2), `Kültéri nyílászárók javítása, festése` (2), `Opcionális műanyag ablak`, `Műanyag ablak és szúnyogháló berakása`, `Redőny gurtni és automata cseréje: 5db`, `Ablakok bontása és újak berakása`

### `szigeteles` — 25 distinct phrasings
- `Kéménybélelés` (12), `Fürdő vízszigetelése` (5), `Fürdők vízsszigetelése` (4), `Kéménybélelés, engedélyekkel` (2), `Mindkét fürdő vízszigetelése` (2), `Utcafront hőszigetelése: 5cm multipor`, `Multipor hő-hang szigetelés folyósóval közös falra és utcai falra`, `Vízszigetelés, hajlaterősítéssel`

### `vakolas` — 19 distinct phrasings
- `Vakolás` (13), `8. Vakolás`, `5. Vakolás: fürdő, wc, ajtókávák, hornyok`, `7. Vakolás: fürdő, wc, kopogó vakolatok`, `Javító vakolás`, `Helységben vakolat tégláig verése és újravakolása: cementes vakolás`, `Vakolás, horonyjavítás`

### `gletteles_festes` — 57 distinct phrasings
- `Glettelés-csiszolás-mélyalapozás, festés` (9), `Tapétázás` (8), `Glettelés, festés` (5), `Glettelés festés` (3), `Festék` (3), `Tapéta` (3), `Színes festék` (3), `Glettelés, csiszolás, festés` (2), `Dekortapéták` (2), `Fehér festék` (2)

### `egyeb_komuves` — 82 distinct phrasings
- `Betonozás` (13), `Egyéb köműves munkák` (9), `Aljzatkiegyenlítés` (8), `Falazás` (5), `Beton előtti szintelőkészítés` (5), `Betonozás előtti szintkiegyenlítés` (3), `Laminált padló előtti aljzatkiegyenlítés` (2), `Falazás, gerendák közti beton alap készítéssel (födém megerősítés)` (2), `Födém megerősítés és a falak alatti sáv alap elkészítése`

### `szallitas` — 46 distinct phrasings
- `Segédmunka` (25), `Felújítás során keletkező törmelék elszállítása` (13), `Felújítás alatt keletkező sitt elszállítása` (4), `Felújítás során, de már a bontás után keletkező törmelék elszállítása` (3), `Sittelszállítás: 1 kanyar` (2), `Segédmunkák` (2), `15. Felújítás során szükséges anyagok mozgatása/egyéb segédmunkák` (2), `Szállítási költségünk`

### `konyha` — 22 distinct phrasings
- `Magasfényű konyhabútor` (2), `Konyhapult cseréje, konyhapulttal, szagelszívó cseréjével…`, `Konyhai szagelszívó`, `Konyhai páraelszívók elvezetése`, `Beépíthető 2 főzőzónás fözőlap: 5 db`, `Beépíthető hűtő: 5 db`, `Mosogató medence: 5 db`, `Indukciós főzőlap`, `Elektromos sütő`

### `furdo` — 43 distinct phrasings
- `Szaniterek` (2), `2db fürdőszoba bútor` (2), `Zuhanyüveg` (2), `Mosdó csaptelep` (2), `Wc csésze` (2), `Zuhanykabin: 5 db`, `Mosdó kagyló: 5db`, `Aszimmetrikus fürdőkád saját előlapjával`, `Bepíthető wc tartály`, `Kádtöltő csaptelep`

### `futes_rendszer` — 39 distinct phrasings
- `Cirkó kazán` (8), `Cirkó kazán garanciális beüzemelése` (7), `Radiátorok` (5), `Cirkó kazán garanciális beüzemelés` (3), `Fűtési rendszer átmosása` (2), `Cirkó kazán, indító idommal és iszap szűrővel` (2), `Gázkonvektorok cseréje`, `Immergas Vixtrix Erp kombi gázkazán`

### `gipszkarton` — 36 distinct phrasings
- `Gipszkarton teli álmennyezet` (3), `3 ajtónyílás vágása, profilozás, kartonozás, glettelés, bandázsolás` (2), `Gipszkarton design` (2), `Gipszkarton teli, normál állmennyezet`, `7. Gipszkarton álmennyzet, gipszkarton design csuklya`, `Barisol fólia fogadó szerkezetének kialakítása…`, `5. Gipszkarton válaszfal építése, glettelése, 2x festése szoba felöl`

### `egyeb` — 78 distinct phrasings
- `Egyéb munkák` (4), `Háztartási elektronika` (3), `Egyéb szakipari munkák` (2), `Lakberendezés` (2), `3db gardrób asztalos által gyártott gardrób szekrény` (2), `Előtér cipősszekrény, tv szekrény` (2), `3 db franciaágy, matraccal, ágyráccsal.` (2), `"L" / sarokkanapé` (2), meta rows such as `1.`, `2.`, `Az ár nem tartalmazza`, `Kezdéskor: anyagra: 50%, munkadíjra: 20%`, `771179`

---

## 6. Judgment calls a human should double-check

These are cases where the LLM's grouping is defensible but debatable. They are
**not** errors that block review; they are the decisions worth confirming before
this taxonomy drives any downstream write.

1. **`szigeteles` mixes three different things.** The LLM put all of
   `Kéménybélelés` (12 phrasings), `Fürdő vízszigetelése` (many) and genuine
   thermal insulation (`Utcafront hőszigetelése`, `Multipor hő-hang szigetelés`)
   into one category.
   - `Kéménybélelés` (chimney lining) is arguably a **heating-system** item
     (`futes_rendszer`) or a masonry item.
   - `Fürdő vízszigetelése` (wet-room waterproofing) is arguably a
     **bathroom** (`furdo`) item and is *not* thermal insulation.
   - Recommend: split or re-route these before using `szigeteles` for costing.
2. **`burkolas` vs `parketta` overlap.** The LLM put laminate/vinyl flooring
   under `burkolas`, leaving only parquet-specific work under `parketta`. Both
   feed `needs_flooring`, so the 7-bucket comparison is unaffected, but the
   finer split is fuzzy. Recommend deciding whether laminate/vinyl belong to
   `burkolas` (surface covering) or `parketta` (wood/laminate flooring).
3. **`egyeb_komuves` absorbs `aljzatkiegyenlítés` and `betonozás`.** There is no
   seed key for subfloor leveling; the LLM folded it into misc masonry. If
   subfloor work becomes a cost driver, it may deserve its own category.
4. **Some items in `egyeb` look misassigned.** Examples: `Víz szerelés` (should
   likely be `viz_futes`), `Összes csaptelep elzáró és radiátor szelepek cseréje`
   (`viz_futes`/`futes_rendszer`), `Erkély` / `Terasz` / `13. Erkély tető
   cseréje` (no canonical balcony category), `Vízóra hitelesítés` / `Gázterv`
   (utility/admin, arguably fine as `egyeb`).
5. **`klima` contains two ambiguous one-offs** — `Készülék` and
   `Szerelvényezés-gázfeltöltés` — which lack context in `name_hu` alone; the
   `notes_hu` field (extracted, not yet used) could disambiguate them.
6. **No canonical category for `terasz`/`erkély`.** Balcony/terrace work
   currently falls into `egyeb`. If it matters for estimates, consider adding a
   canonical `terasz_erkely` category (out of scope for this pass).

---

## 7. Notes / out of scope

- **No code, schema, or database changes** were made by this phase. In
  particular, no `category_key_v2` column was added; that is a later phase.
- The live product path (`app/streamlit_app.py`, `orchestrator/handlers.py`,
  `renovai/predictor/price_model.py`) and `renovai/db/repository.py` /
  `renovai/db/models.py` were **not modified**.
- `renovai/rag/gemini_client.py` and `renovai/rag/embedder.py` were **not
  modified**; the dedicated helper `scripts/taxonomy_llm_client.py` was written
  instead.
- `notes_hu` (797 distinct) was extracted but not clustered in this pass.
- Known unrelated issues noticed while reading the corpus but deliberately not
  touched: the `assign_category()` keyword fallback in
  `renovai/db/repository.py` still dumps many items into `egyéb`; fixing that is
  the next phase.
