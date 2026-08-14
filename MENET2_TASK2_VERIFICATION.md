# MENET2_TASK2_VERIFICATION — Type × Era Similarity Weighting: Self-Review Findings

**Branch:** `feature/menet2-chain-rules-and-pricing`
**Commits reviewed:** `dded26f` (Task 2), `571af9e` (verification follow-up)
**Date:** 2026-08-14

---

## Summary of the change under review

Task 2 added a **building type × era similarity factor** to the corpus weighting in
`renovai/predictor/price_model.py`. Before this change, quote selection ignored the
building taxonomy entirely (the original bug report). After, each scope's quote weights
combine the existing IVW/era weights with a `building_type_similarity` × `era_similarity`
factor. A regression test (`test_building_type_era_affects_estimate`) proved the two
requests now diverge.

This document records the four self-review checks run against that change, the fixes made,
and the verdict on whether Task 3 can proceed on top of it.

---

## CHECK 1 — Regression test strength: why is the total delta small?

**Original test inputs:** `panel / 1975` vs `tégla / 2005`, same scope (plumbing,
electrical, flooring, full demolition), 55 m², district 5.

**Measured outputs (committed code):** 5,323,799 vs 5,317,910 HUF → **Δ ≈ 0.11%**.

The regression test passed, but a ~0.1% total delta is a weak guarantee if it came from a
rounding artifact. Three sub-checks:

### (a) Is the factor doing any work? — YES
- `sim_min` across scopes drops to **0.35 (A) / 0.3 (B)** (era distances of 60–100+ years
  hit the era-similarity floor), i.e. weights well below 1.0 are applied.
- **Per-scope weighted per-sqm averages DO differ** between the two requests:
  - plumbing: 26,556 → 26,587 HUF/m²
  - electrical: 29,026 → 28,988 HUF/m²
  - demolition: 19,754 → 19,654 HUF/m²
  - flooring: 17,824 → 17,824 HUF/m² (identical — dominated by one NULL-typed quote)

The test now asserts **per-scope similarity weights < 1.0 and per-scope average divergence**
in addition to the nonzero total delta, so the regression cannot be satisfied by rounding
noise.

### (b) Why is the *total* delta only ~0.2%?
Three compounding, data-driven reasons (not a logic bug):
1. **Type coverage is 5/47 quotes.** Only 5 corpus quotes carry a non-NULL
   `building_type` (see CHECK 2). The IVW-dominant quotes in every scope are NULL-typed,
   so they receive era-only weights — identical for both requests.
2. **The era distribution is clustered pre-1935 (21 of 47 quotes).** The corpus is
   dominated by old buildings; both a 1975 and a 2005 request down-weight those same old
   quotes similarly, so the two weight vectors stay close.
3. **The era curve is gentle** (10-year plateau, then linear descent to a 0.3 floor over
   ~100 years), by design — small era differences should not collapse the estimate.

### (c) Extreme-case stress: can the factor ever move the total >1%?
Tested `panel/1975` vs `tégla/2005` and vs `vályog/2010` (max type distance), all on the
committed code:
- Committed curves (`band=10`, `floor=0.3`): tégla −0.11%, vályog −0.33%.
- Sharpened era curve (`band=25`): tégla −0.30%, vályog −0.51%.
- Maximum stress (`band=0`, `floor=0.05`, only exact-era keeps 1.0): tégla −1.12%,
  vályog −1.27%.

Even pushing the era curve to its absolute extreme stays just over −1%. The factor is doing
real per-scope work but, on this sparse corpus, its *total-estimate* footprint is small.

**Verdict:** The test is honest — it asserts real weighting work, not a fabricated
delta. The small total delta is a corpus-coverage reality (see CHECK 2), documented here
so future sessions don't chase a nonexistent bug. **Acceptable for Task 3.**

---

## CHECK 2 — Backfill coverage: why 5/47 type vs 47/47 era?

**Counts from the backfill summary:** 47/47 quotes have `building_era`; only 5/47 have
`building_type` (4 `PANEL`, 1 `TEGLA_CSALADI_HAZ`).

**Why the asymmetry:**
- **Era** is nearly always derivable from the *filename/front-matter* (e.g. "1900-as évek",
  "1958 építés éve", "2005") — the authoritative source the backfill reads.
- **Type** tokens (tégla, panel, csúszózsalu, …) are almost never in the filename or
  front-matter. They appear only in the *body text*, which the backfill deliberately does
  not parse as a type source.

**Spot-check of 10+ untyped quotes:** every body-text mention of a type token is a
product/material term, not a building descriptor — e.g. "10 cm vastag Ytong tégla fal"
(a *brick product*, not "the house is brick"), "elektromos fűtéspanel" (a *heating panel*,
not "panel-built"). These are exactly the false positives the backfill was built to avoid.

**Verdict:** 5/47 type coverage is a **corpus-limitation, not an extraction failure**. The
backfill's conservative design is correct. The type factor therefore has little leverage on
this dataset today; if wider type coverage is ever needed, it must come from expanding the
quote corpus or a deliberately-scoped body-text heuristic — the latter currently
contradicts the backfill's design contract. **Acceptable for Task 3** (era-similarity alone
provides meaningful per-scope differentiation).

---

## CHECK 3 — Weight normalization: are combined weights renormalized?

**Question:** After multiplying IVW weights by the type/era factor, are the combined
weights normalized (sum = 1) before use?

**Finding:** They are NOT explicitly renormalized — and they do NOT need to be. The
per-scope average is computed as

    avg = Σ(combined_i · x_i) / Σ(combined_i)

via the new `_weighted_mean()` helper, which is **self-normalizing**. A uniform scaling of
all candidates' weights by a constant `c` cancels: `Σ(c·w·x)/Σ(c·w) = Σ(w·x)/Σ(w)`. The
type/era factor changes the *relative* weighting, which is exactly the intended behavior,
and no cross-scope consistency is lost.

**Fix applied:** extracted `_weighted_mean(vals, weights)` in
`renovai/predictor/price_model.py` and added `TestWeightedMeanScaleInvariance` (2 tests)
proving the output is invariant to uniform weight scaling.

---

## CHECK 4 — Accent-folded substring matching: false positives found and fixed

**Question:** with accent-folding, does substring type matching conflate distinct
categories?

**Finding — YES, one real false positive:**
- `tegla` (apartment-brick) ⊂ `teglacsaladihaz` (family-house brick).
- `TEGLA_CSALADI_HAZ` is a **separate doc-01 category** from `TEGLA`. Old substring
  matching returned `1.0` for a user request of "tégla" against a
  `teglacsaladihaz` quote.

**Fix applied:** replaced substring matching with **canonical-token matching** — the
requested type and each quote type are tokenized and folded to canonical doc-01 tokens
(`tégla` → `tegla`, `teglacsaladihaz` → `teglacsaladihaz`), then matched as token sets
via the same similarity scheme. Tests added for both the correct `tegla`↔`tegla` hit and
the `tegla`↔`teglacsaladihaz` non-match.

---

## Test & lint status after the follow-up commit

- Full suite: **190 passed, 4 skipped** (was 176 passed before Task 2).
- New test file `tests/test_price_model_similarity.py`: 13 tests
  (`TestEraSimilarity` 5, `TestBuildingTypeSimilarity` 6, `TestWeightedMeanScaleInvariance` 2).
- `test_building_type_era_affects_estimate` strengthened to assert per-scope weights < 1.0
  and per-scope average divergence, plus the nonzero total delta.
- `ruff`: 5 findings — all pre-existing (unused vars in the baseline test module and a
  pre-existing unused `raw_years`); zero new findings from the Task 2 change or follow-up.

---

## Verdict

| Check | Result |
|---|---|
| CHECK 1 — regression test strength | Strengthened; small total delta is corpus-driven, not a bug |
| CHECK 2 — backfill coverage | 5/47 type = corpus limitation, not extraction failure |
| CHECK 3 — weight normalization | Self-normalizing; no renormalization needed; tested |
| CHECK 4 — substring false positive | **Fixed** (canonical-token matching) + tests |

**All four checks clear.** Task 2 is ready to build on; Task 3 (rule-engine integration and
product pricing decisions) can proceed.
