"""Phase 3 — leave-one-out validation of the category taxonomy.

For every quote in the corpus this harness holds that quote out, estimates its
renovation cost using only the *other* quotes, and compares the estimate to the
held-out quote's own real, already-known line-item totals. It does this twice
for every fold:

* **Arm 0** — ``scope_matched_estimate(..., category_column="category_key")``
  (the legacy categorization).
* **Arm 1** — ``scope_matched_estimate(..., category_column="category_key_v2")``
  (the canonical Phase-2 categorization).

The only difference between the two arms is which category column the estimator
reads; the query pool, the held-out input and the target date are identical.

Hard guarantees enforced here:

* **No LLM calls.** This is pure deterministic arithmetic over already
  categorized data. No provider client is imported or constructed.
* **No data leakage.** Each fold runs against a throwaway copy of the database
  with the held-out quote's row (and its line items) deleted, so the held-out
  quote can never enter the query pool. Its real totals are read from the
  *source* database only after both estimates have been computed, and only as
  ground truth. The estimator is never handed a price for the held-out quote.
* **No price arithmetic reinterpreted.** All estimate math stays in
  ``renovai/predictor/price_model.py``; this module only reads its output.

Ground-truth convention
-----------------------
A scope's ground truth is the sum of the held-out quote's line-item
``total_cost_huf`` grouped into the seven ``SCOPE_CATEGORY_MAP`` scopes using
the canonical ``category_key_v2`` column (falling back to the legacy
``category_key`` for the few rows left ``NULL`` by the Phase-2 re-examination).
The same grouping is used to derive each fold's ``needs_*`` flags, but each arm
derives them from its *own* column, exactly as the specification requires.

Usage::

    python -m scripts.loocv_validation \
        --database-url sqlite+aiosqlite:///data/renovai.db \
        --out-dir docs
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import selectinload

from renovai.db.models import Quote
from renovai.db.session import get_engine, get_session_maker
from renovai.ingestion.inflation_calc import load_price_index
from renovai.ingestion.inflation_models import PriceIndex
from renovai.predictor.feature_extractor import ApartmentInput
from renovai.predictor.price_model import (
    SCOPE_CATEGORY_MAP,
    SCOPE_NAMES_ORDERED,
    scope_matched_estimate,
)

logger = logging.getLogger("renovai.loocv")

ARM0 = "category_key"
ARM1 = "category_key_v2"
ARM0_LABEL = "Arm 0 (category_key)"
ARM1_LABEL = "Arm 1 (category_key_v2)"
REFERENCE_TRUTH_COLUMN = "category_key_v2"

# Quote never stored a room count, and scope_matched_estimate() does not read
# num_rooms anywhere in its logic. A single documented placeholder is used for
# every fold rather than inventing a per-quote value.
PLACEHOLDER_NUM_ROOMS = 0

_ACCENT_MAP = str.maketrans("áéíóöőúüű", "aeiooouuu")
_COMPLETENESS_ORDER = ("komplett", "reszleges", "unknown")


def _fold(value: Optional[str]) -> Optional[str]:
    """Lower-case and strip Hungarian accents from a category key."""
    if value is None:
        return None
    return value.strip().lower().translate(_ACCENT_MAP)


#: scope -> set of accent-folded canonical keys (the bridge between the legacy
#: SCOPE_CATEGORY_MAP keys and the ASCII category_key_v2 values).
_FOLDED_SCOPE_KEYS: Dict[str, set] = {
    scope: {_fold(k) for k in info["keys"]}
    for scope, info in SCOPE_CATEGORY_MAP.items()
}


def scope_for_category(category_value: Optional[str]) -> Optional[str]:
    """Map a category value (either column) to a SCOPE_CATEGORY_MAP scope."""
    folded = _fold(category_value)
    if folded is None:
        return None
    for scope in SCOPE_NAMES_ORDERED:
        if folded in _FOLDED_SCOPE_KEYS[scope]:
            return scope
    return None


def derive_needs(quote: Quote, category_column: str) -> Dict[str, bool]:
    """Derive the seven ``needs_*`` flags from one categorization column.

    A flag is True iff the quote has at least one line item in that scope under
    the requested column. No price field is read.
    """
    active = set()
    for item in quote.line_items:
        scope = scope_for_category(getattr(item, category_column, None))
        if scope is not None:
            active.add(scope)
    return {name: (name in active) for name in SCOPE_NAMES_ORDERED}


def build_apartment_input(
    quote: Quote, category_column: str
) -> Optional[ApartmentInput]:
    """Build the held-out quote's descriptive input, or None if unusable.

    Only descriptive metadata is used. ``district`` defaults to 0 when absent
    (the estimator does not read it for scoring). ``num_rooms`` is the single
    placeholder constant.
    """
    if (
        quote.area_sqm is None
        or quote.building_type is None
        or quote.building_era is None
    ):
        return None
    building_type = getattr(quote.building_type, "value", quote.building_type)
    return ApartmentInput(
        district=quote.district if quote.district is not None else 0,
        total_area_sqm=float(quote.area_sqm),
        num_rooms=PLACEHOLDER_NUM_ROOMS,
        building_type=str(building_type),
        building_era=int(quote.building_era),
        **derive_needs(quote, category_column),
    )


def ground_truth_by_scope(
    quote: Quote, category_column: str = REFERENCE_TRUTH_COLUMN
) -> Dict[str, int]:
    """The held-out quote's real totals per scope (ground truth).

    Uses the canonical column with a legacy fallback for ``NULL`` rows. Reads
    only after the estimates have been computed by the caller.
    """
    totals: Dict[str, int] = {}
    for item in quote.line_items:
        cost = item.total_cost_huf
        if not cost:
            continue
        value = getattr(item, category_column, None)
        if value is None:
            value = item.category_key
        scope = scope_for_category(value)
        if scope is not None:
            totals[scope] = totals.get(scope, 0) + cost
    return totals


def _category_estimate(estimate: Optional[dict], scope: str) -> Optional[int]:
    if not estimate:
        return None
    categories = estimate.get("categories") or {}
    entry = categories.get(scope)
    if not entry:
        return None
    return int(entry["estimate_huf"]["mid"])


def _signed_deviation_pct(estimate: int, truth: int) -> float:
    return (estimate - truth) / truth * 100.0


@dataclass
class CategoryOutcome:
    category: str
    ground_truth_huf: int
    arm0_estimate_huf: int
    arm0_deviation_pct: float
    arm1_estimate_huf: int
    arm1_deviation_pct: float
    closer_arm: str


@dataclass
class FoldResult:
    quote_id: str
    file_name: str
    year: int
    completeness: Optional[str]
    area_sqm: float
    building_type: Optional[str]
    outcomes: List[CategoryOutcome] = field(default_factory=list)
    arm0_estimate_mid_huf: int = 0
    arm1_estimate_mid_huf: int = 0
    arm0_scopes: List[str] = field(default_factory=list)
    arm1_scopes: List[str] = field(default_factory=list)
    training_quote_ids: List[str] = field(default_factory=list)
    # Descriptive inputs actually handed to the estimator, for leak auditing.
    arm0_input: Dict[str, Any] = field(default_factory=dict)
    arm1_input: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SkippedQuote:
    quote_id: str
    file_name: str
    reason: str


@dataclass
class LOOCVResult:
    folds: List[FoldResult]
    skipped: List[SkippedQuote]
    coverage_warnings: List[str]
    corpus_quote_count: int
    indexed_year_min: Optional[int]
    indexed_year_max: Optional[int]


async def _load_quotes(database_url: str) -> List[Quote]:
    engine = get_engine(database_url)
    session_maker = get_session_maker(engine)
    try:
        async with session_maker() as session:
            stmt = select(Quote).options(selectinload(Quote.line_items))
            return list((await session.execute(stmt)).scalars().all())
    finally:
        await engine.dispose()


def check_year_coverage(
    years: Iterable[int], price_index: PriceIndex
) -> tuple[List[str], Optional[int], Optional[int]]:
    """Warn loudly if any target year is outside the indexed CPI range."""
    indexed = [r.year for r in price_index.materials] + [
        r.year for r in price_index.labor
    ]
    if not indexed:
        warning = (
            "Price index is EMPTY: no materials/labor CPI records loaded. "
            "Every estimate is an uninflated comparison."
        )
        return [warning], None, None
    lo, hi = min(indexed), max(indexed)
    warnings: List[str] = []
    for year in sorted(set(years)):
        if year < lo or year > hi:
            message = (
                f"YEAR OUT OF INDEX RANGE: quote year {year} falls outside the "
                f"indexed CPI range {lo}-{hi}. The index clamps to the nearest "
                "record (it does NOT extrapolate); results for this fold are "
                "inflated/deflated using a boundary value, not a real one."
            )
            logger.warning(message)
            warnings.append(message)
    return warnings, lo, hi


async def _execute_fold(
    source_db_path: Path,
    quote_id: Any,
    price_index: PriceIndex,
    target_date: date,
    arm0_input: ApartmentInput,
    arm1_input: ApartmentInput,
    workdir: Path,
) -> tuple[Optional[dict], Optional[dict], List[str]]:
    """Run both arms for one fold against a copy of the DB minus the quote.

    Returns ``(arm0_result, arm1_result, training_quote_ids)``. The database is
    copied so the estimator's own "load every quote" query physically cannot
    see the held-out row.
    """
    temp_path = workdir / f"fold_{quote_id}.db"
    shutil.copy2(source_db_path, temp_path)
    engine = get_engine(f"sqlite+aiosqlite:///{temp_path.as_posix()}")
    session_maker = get_session_maker(engine)
    try:
        async with session_maker() as session:
            held_out = await session.get(Quote, quote_id)
            if held_out is not None:
                await session.delete(held_out)
                await session.commit()

        async with session_maker() as session:
            training_ids = [
                str(row) for row in (await session.execute(select(Quote.id))).scalars()
            ]

        arm0_result = await scope_matched_estimate(
            arm0_input,
            session_maker,
            price_index,
            target_date,
            category_column=ARM0,
        )
        arm1_result = await scope_matched_estimate(
            arm1_input,
            session_maker,
            price_index,
            target_date,
            category_column=ARM1,
        )
        return arm0_result, arm1_result, training_ids
    finally:
        await engine.dispose()
        temp_path.unlink(missing_ok=True)


async def run_loocv(
    database_url: str,
    price_index: PriceIndex,
    *,
    workdir: Optional[Path] = None,
) -> LOOCVResult:
    """Run the full leave-one-out sweep over every quote in the corpus."""
    url = make_url(database_url)
    if not url.database:
        raise ValueError(f"database_url has no file path: {database_url!r}")
    source_db_path = Path(url.database)
    if not source_db_path.exists():
        raise FileNotFoundError(source_db_path)

    quotes = await _load_quotes(database_url)
    years = [q.quote_date.year for q in quotes if q.quote_date is not None]
    coverage_warnings, year_min, year_max = check_year_coverage(years, price_index)

    owns_workdir = workdir is None
    workdir = (
        Path(tempfile.mkdtemp(prefix="renovai_loocv_"))
        if workdir is None
        else Path(workdir)
    )
    workdir.mkdir(parents=True, exist_ok=True)

    folds: List[FoldResult] = []
    skipped: List[SkippedQuote] = []
    try:
        for quote in quotes:
            quote_id = str(quote.id)
            if (
                quote.area_sqm is None
                or quote.building_type is None
                or quote.building_era is None
            ):
                missing = []
                if quote.area_sqm is None:
                    missing.append("area_sqm")
                if quote.building_type is None:
                    missing.append("building_type")
                if quote.building_era is None:
                    missing.append("building_era")
                reason = "missing required field(s): " + ", ".join(missing)
                logger.info("Skipping %s: %s", quote.file_name, reason)
                skipped.append(SkippedQuote(quote_id, quote.file_name, reason))
                continue
            if quote.quote_date is None:
                reason = "missing quote_date (cannot build target_date)"
                logger.info("Skipping %s: %s", quote.file_name, reason)
                skipped.append(SkippedQuote(quote_id, quote.file_name, reason))
                continue

            arm0_input = build_apartment_input(quote, ARM0)
            arm1_input = build_apartment_input(quote, ARM1)
            assert arm0_input is not None and arm1_input is not None
            if not any(
                arm0_input.model_dump()[n] for n in SCOPE_NAMES_ORDERED
            ) and not any(arm1_input.model_dump()[n] for n in SCOPE_NAMES_ORDERED):
                reason = "no line items in any of the 7 scoped categories"
                logger.info("Skipping %s: %s", quote.file_name, reason)
                skipped.append(SkippedQuote(quote_id, quote.file_name, reason))
                continue

            target_date = date(quote.quote_date.year, 1, 1)
            arm0_result, arm1_result, training_ids = await _execute_fold(
                source_db_path,
                quote.id,
                price_index,
                target_date,
                arm0_input,
                arm1_input,
                workdir,
            )

            # Ground truth is read only now, after both estimates have run.
            truth = ground_truth_by_scope(quote)
            outcomes: List[CategoryOutcome] = []
            for scope in SCOPE_NAMES_ORDERED:
                scope_truth = truth.get(scope, 0)
                if scope_truth <= 0:
                    continue  # never score a category the quote has no work in
                arm0_est = _category_estimate(arm0_result, scope)
                arm1_est = _category_estimate(arm1_result, scope)
                if arm0_est is None or arm1_est is None:
                    continue
                arm0_dev = _signed_deviation_pct(arm0_est, scope_truth)
                arm1_dev = _signed_deviation_pct(arm1_est, scope_truth)
                if abs(arm0_dev) < abs(arm1_dev):
                    closer = ARM0
                elif abs(arm1_dev) < abs(arm0_dev):
                    closer = ARM1
                else:
                    closer = "tie"
                outcomes.append(
                    CategoryOutcome(
                        category=scope,
                        ground_truth_huf=scope_truth,
                        arm0_estimate_huf=arm0_est,
                        arm0_deviation_pct=round(arm0_dev, 3),
                        arm1_estimate_huf=arm1_est,
                        arm1_deviation_pct=round(arm1_dev, 3),
                        closer_arm=closer,
                    )
                )

            if not outcomes:
                reason = "no scorable categories (no estimator output)"
                logger.info("Skipping %s: %s", quote.file_name, reason)
                skipped.append(SkippedQuote(quote_id, quote.file_name, reason))
                continue

            folds.append(
                FoldResult(
                    quote_id=quote_id,
                    file_name=quote.file_name,
                    year=quote.quote_date.year,
                    completeness=quote.renovation_completeness,
                    area_sqm=float(quote.area_sqm),
                    building_type=str(
                        getattr(quote.building_type, "value", quote.building_type)
                    ),
                    outcomes=outcomes,
                    arm0_estimate_mid_huf=int(
                        (arm0_result or {}).get("estimate_mid_huf", 0)
                    ),
                    arm1_estimate_mid_huf=int(
                        (arm1_result or {}).get("estimate_mid_huf", 0)
                    ),
                    arm0_scopes=sorted(
                        n for n in SCOPE_NAMES_ORDERED if arm0_input.model_dump()[n]
                    ),
                    arm1_scopes=sorted(
                        n for n in SCOPE_NAMES_ORDERED if arm1_input.model_dump()[n]
                    ),
                    training_quote_ids=training_ids,
                    arm0_input=arm0_input.model_dump(),
                    arm1_input=arm1_input.model_dump(),
                )
            )
    finally:
        if owns_workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    return LOOCVResult(
        folds=folds,
        skipped=skipped,
        coverage_warnings=coverage_warnings,
        corpus_quote_count=len(quotes),
        indexed_year_min=year_min,
        indexed_year_max=year_max,
    )


# ---------------------------------------------------------------------------
# Aggregation and reporting
# ---------------------------------------------------------------------------


def mape(deviations_pct: Iterable[float]) -> Optional[float]:
    """Mean absolute percentage error from signed percentage deviations."""
    values = [abs(v) for v in deviations_pct]
    if not values:
        return None
    return sum(values) / len(values)


def _completeness_key(completeness: Optional[str]) -> str:
    return completeness if completeness in ("komplett", "reszleges") else "unknown"


def compute_metrics(result: LOOCVResult) -> Dict[str, Any]:
    """Per-arm MAPE overall, per category, and per renovation completeness."""
    rows = [(fold, outcome) for fold in result.folds for outcome in fold.outcomes]
    per_arm: Dict[str, Any] = {}
    for arm, attr in ((ARM0, "arm0_deviation_pct"), (ARM1, "arm1_deviation_pct")):
        overall = mape(getattr(outcome, attr) for _, outcome in rows)
        per_category = {
            scope: mape(
                getattr(outcome, attr)
                for _, outcome in rows
                if outcome.category == scope
            )
            for scope in SCOPE_NAMES_ORDERED
        }
        per_completeness = {}
        for completeness in _COMPLETENESS_ORDER:
            selected = [
                getattr(outcome, attr)
                for fold, outcome in rows
                if _completeness_key(fold.completeness) == completeness
            ]
            per_completeness[completeness] = {
                "mape_pct": mape(selected),
                "row_count": len(selected),
            }
        per_arm[arm] = {
            "mape_pct": overall,
            "row_count": len(rows),
            "per_category_mape_pct": per_category,
            "per_completeness": per_completeness,
        }
    return {
        "fold_count": len(result.folds),
        "row_count": len(rows),
        "per_arm": per_arm,
        "closer_arm_counts": {
            ARM0: sum(1 for _, o in rows if o.closer_arm == ARM0),
            ARM1: sum(1 for _, o in rows if o.closer_arm == ARM1),
            "tie": sum(1 for _, o in rows if o.closer_arm == "tie"),
        },
    }


def _format_pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _format_huf(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def select_case_studies(
    result: LOOCVResult, count: int = 2
) -> tuple[List[FoldResult], str]:
    """Pick ``count`` komplett quotes from the most recent year available.

    Falls back to the closest available and returns a note describing any
    deviation from the ideal selection.
    """
    komplett = [f for f in result.folds if f.completeness == "komplett"]
    if not komplett:
        return [], "No `komplett` quotes with usable data exist in the corpus."
    most_recent = max(f.year for f in komplett)
    same_year = sorted(
        (f for f in komplett if f.year == most_recent), key=lambda f: f.file_name
    )
    if len(same_year) >= count:
        note = (
            f"Selected from the {len(same_year)} `komplett` quote(s) in the "
            f"most recent usable year ({most_recent})."
        )
        return same_year[:count], note
    chosen = same_year
    year = most_recent
    remaining = [f for f in komplett if f.year != most_recent]
    remaining.sort(key=lambda f: (-f.year, f.file_name))
    chosen = chosen + remaining[: count - len(chosen)]
    note = (
        f"Only {len(same_year)} `komplett` quote(s) exist in the most recent "
        f"usable year ({year}); filled the remainder with the closest available "
        "years."
    )
    return chosen[:count], note


def _case_study_table(fold: FoldResult) -> List[str]:
    lines = [
        f"**{fold.file_name}** — {fold.year}, {fold.area_sqm:.0f} m², "
        f"building_type={fold.building_type}, completeness={fold.completeness}",
        "",
        "| category | ground truth | Arm 0 estimate | Arm 0 dev % | "
        "Arm 1 estimate | Arm 1 dev % | closer arm |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for outcome in fold.outcomes:
        lines.append(
            f"| {outcome.category} | {_format_huf(outcome.ground_truth_huf)} | "
            f"{_format_huf(outcome.arm0_estimate_huf)} | "
            f"{outcome.arm0_deviation_pct:+.1f}% | "
            f"{_format_huf(outcome.arm1_estimate_huf)} | "
            f"{outcome.arm1_deviation_pct:+.1f}% | {outcome.closer_arm} |"
        )
    lines.append("")
    return lines


def render_report(result: LOOCVResult, metrics: Dict[str, Any]) -> str:
    """Render the human-readable Markdown report."""
    lines: List[str] = [
        "# Leave-One-Out Validation: legacy vs. canonical categorization",
        "",
        "**Phase 3 report.** Generated by `scripts/loocv_validation.py`.",
        "",
        "## Method",
        "",
        f"- Corpus: **{result.corpus_quote_count} quotes**; "
        f"**{len(result.folds)} folds ran** and **{len(result.skipped)} skipped**.",
        "- For each fold the held-out quote is removed from a throwaway copy of "
        "the database, so it cannot enter the query pool (no leakage). Both "
        "arms estimate from the other quotes only.",
        "- Arm 0 reads `category_key`; Arm 1 reads `category_key_v2`; everything "
        "else (query pool, area, type/era, target date) is identical.",
        "- Target date is Jan 1 of the held-out quote's own year, matching the "
        "ingestion convention, so the quote's own totals are directly comparable "
        "without a separate inflation adjustment.",
        "- Ground truth is the held-out quote's real `total_cost_huf` per scope "
        "under the canonical `category_key_v2` column (legacy fallback for "
        "`NULL` rows). No price is ever fed into an estimate.",
        "",
    ]

    if result.coverage_warnings:
        lines += ["## Price-index coverage warnings", ""]
        lines += [f"- {w}" for w in result.coverage_warnings]
        lines.append("")

    lines += [
        "## Aggregate MAPE (lower is better)",
        "",
        f"Overall weighted-by-row MAPE across {metrics['row_count']} quote×category "
        "observations:",
        "",
        "| arm | overall MAPE | rows |",
        "|---|---:|---:|",
    ]
    for arm, label in ((ARM0, ARM0_LABEL), (ARM1, ARM1_LABEL)):
        arm_metrics = metrics["per_arm"][arm]
        lines.append(
            f"| {label} | {_format_pct(arm_metrics['mape_pct'])} | "
            f"{arm_metrics['row_count']} |"
        )
    lines.append("")

    lines += [
        "### Per-category MAPE",
        "",
        "| category | Arm 0 MAPE | Arm 1 MAPE | delta (A1−A0) | winner |",
        "|---|---:|---:|---:|---|",
    ]
    for scope in SCOPE_NAMES_ORDERED:
        a0 = metrics["per_arm"][ARM0]["per_category_mape_pct"][scope]
        a1 = metrics["per_arm"][ARM1]["per_category_mape_pct"][scope]
        if a0 is None or a1 is None:
            lines.append(
                f"| {scope} | {_format_pct(a0)} | {_format_pct(a1)} | n/a | n/a |"
            )
            continue
        delta = a1 - a0
        winner = ARM1_LABEL if a1 < a0 else (ARM0_LABEL if a0 < a1 else "tie")
        lines.append(
            f"| {scope} | {_format_pct(a0)} | {_format_pct(a1)} | "
            f"{delta:+.1f} pp | {winner} |"
        )
    lines.append("")

    lines += [
        "### Completeness breakdown (reporting dimension)",
        "",
        "| completeness | Arm 0 MAPE | rows | Arm 1 MAPE | rows |",
        "|---|---:|---:|---:|---:|",
    ]
    for completeness in _COMPLETENESS_ORDER:
        c0 = metrics["per_arm"][ARM0]["per_completeness"][completeness]
        c1 = metrics["per_arm"][ARM1]["per_completeness"][completeness]
        rows = max(c0["row_count"], c1["row_count"])
        lines.append(
            f"| {completeness} | {_format_pct(c0['mape_pct'])} | {c0['row_count']} "
            f"| {_format_pct(c1['mape_pct'])} | {c1['row_count']} |"
        )
    lines.append("")
    closer = metrics["closer_arm_counts"]
    lines += [
        f"Per-observation closer arm: **Arm 0 {closer[ARM0]}**, "
        f"**Arm 1 {closer[ARM1]}**, ties {closer['tie']}.",
        "",
    ]

    case_studies, case_note = select_case_studies(result)
    lines += ["## Case studies", ""]
    if not case_studies:
        lines += [case_note, ""]
    else:
        lines += [case_note, ""]
        for fold in case_studies:
            lines += _case_study_table(fold)

    lines += ["## Skipped folds", ""]
    if not result.skipped:
        lines += ["None.", ""]
    else:
        lines += [
            f"{len(result.skipped)} quote(s) were skipped (never crashed on):",
            "",
            "| file | reason |",
            "|---|---|",
        ]
        for skipped in result.skipped:
            lines.append(f"| {skipped.file_name} | {skipped.reason} |")
        lines.append("")

    lines += [
        "## Notes and limitations",
        "",
        f"- `num_rooms` is the documented placeholder constant "
        f"`{PLACEHOLDER_NUM_ROOMS}` for every fold; the estimator does not read it.",
        "- Per-scope estimates come from `scope_matched_estimate`'s debug "
        "breakdown. The overall `estimate_mid_huf` sums only the scopes each "
        "arm selected, so an arm that fails to recognize a category is penalized "
        "in the overall figure as well as per category.",
        "- Ground truth sums all of a quote's line items with a non-zero cost, "
        "matching the estimator's own corpus-aggregation convention (versions "
        "and exclusion flags are not filtered).",
        "- This harness makes no LLM calls and performs no price arithmetic of "
        "its own beyond deviation percentages and MAPE.",
        "",
    ]
    return "\n".join(lines)


def result_to_records(result: LOOCVResult) -> List[Dict[str, Any]]:
    """Flatten the per-fold, per-category outcomes into flat records."""
    records: List[Dict[str, Any]] = []
    for fold in result.folds:
        for outcome in fold.outcomes:
            record = {
                "quote_id": fold.quote_id,
                "file_name": fold.file_name,
                "year": fold.year,
                "completeness": fold.completeness,
                "area_sqm": fold.area_sqm,
                "building_type": fold.building_type,
                "arm0_scopes": ";".join(fold.arm0_scopes),
                "arm1_scopes": ";".join(fold.arm1_scopes),
            }
            record.update(asdict(outcome))
            records.append(record)
    return records


def write_machine_readable(
    result: LOOCVResult, csv_path: Path, json_path: Path
) -> None:
    records = result_to_records(result)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        if records:
            writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)
    payload = {
        "metrics": compute_metrics(result),
        "coverage_warnings": result.coverage_warnings,
        "indexed_year_min": result.indexed_year_min,
        "indexed_year_max": result.indexed_year_max,
        "corpus_quote_count": result.corpus_quote_count,
        "skipped": [asdict(s) for s in result.skipped],
        "records": records,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 3 leave-one-out categorization validation"
    )
    parser.add_argument(
        "--database-url",
        default="sqlite+aiosqlite:///data/renovai.db",
        help="SQLite database URL (the held-out copies are made from its file)",
    )
    parser.add_argument(
        "--materials-cpi", default="data/raw/inflation/materials_cpi.csv"
    )
    parser.add_argument("--labor-cpi", default="data/raw/inflation/labor_cpi.csv")
    parser.add_argument("--out-dir", default="docs")
    parser.add_argument(
        "--report", default="loocv_validation_report.md", help="report filename"
    )
    parser.add_argument("--csv", default="loocv_validation_results.csv")
    parser.add_argument("--json", default="loocv_validation_results.json")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    price_index = load_price_index(Path(args.materials_cpi), Path(args.labor_cpi))
    result = asyncio.run(run_loocv(args.database_url, price_index))
    metrics = compute_metrics(result)

    out_dir = Path(args.out_dir)
    report_path = out_dir / args.report
    csv_path = out_dir / args.csv
    json_path = out_dir / args.json
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(result, metrics), encoding="utf-8")
    write_machine_readable(result, csv_path, json_path)

    print(
        f"Folds: {len(result.folds)} ran, {len(result.skipped)} skipped, "
        f"corpus {result.corpus_quote_count} quotes"
    )
    for arm, label in ((ARM0, ARM0_LABEL), (ARM1, ARM1_LABEL)):
        print(
            f"  {label}: overall MAPE {_format_pct(metrics['per_arm'][arm]['mape_pct'])}"
        )
    print(f"Report: {report_path}")
    print(f"CSV:    {csv_path}")
    print(f"JSON:   {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
