"""Rule-based estimate audit report for one demonstration apartment.

For a single corpus quote this script renders a standalone, human-readable
audit of ``scope_matched_estimate()``'s output and of the gap between that
output and the quote's own real line items. It answers three questions:

1. What is the full-renovation estimate for this apartment profile, and which
   scope categories contributed to it?
2. Per scope category, how does the estimate compare to the quote's real
   costs, and is the deviation a confident miss (``ALULBECSÜLT`` /
   ``TÚLBECSÜLT``) or an informational floor-fallback artifact?
3. For every category present in the quote's real line items but absent as a
   corpus-backed estimate, why is it missing?

Hard guarantees (same spirit as ``scripts/loocv_validation.py``):

* **No LLM calls, no network.** Pure deterministic arithmetic over already
  categorized data. No provider client is imported or constructed.
* **No data leakage.** The audit reuses the LOOCV leak-guard by delegating to
  ``loocv_validation._execute_fold``: the held-out quote is deleted from a
  throwaway copy of the database before the estimator runs, and the quote's
  real totals are read from the source database only afterwards, as ground
  truth.
* **No price arithmetic reinterpreted.** All estimate math stays in
  ``renovai/predictor/price_model.py``; this module only reads its output and
  computes signed deviations.

Usage::

    python -m scripts.estimate_audit \
        --database-url sqlite+aiosqlite:///data/renovai.db \
        --out-dir docs

    # Audit a specific quote instead of the deterministic default pick:
    python -m scripts.estimate_audit --quote-id <uuid>
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
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.engine import make_url

from renovai.db.models import SEED_WORK_CATEGORIES, Quote
from renovai.ingestion.inflation_calc import load_price_index
from renovai.ingestion.inflation_models import PriceIndex
from renovai.predictor.price_model import SCOPE_CATEGORY_MAP, SCOPE_NAMES_ORDERED
from scripts.loocv_validation import (
    ARM1,
    _category_estimate,
    _execute_fold,
    _load_quotes,
    _signed_deviation_pct,
    build_apartment_input,
    check_year_coverage,
    ground_truth_by_scope,
    scope_for_category,
)

logger = logging.getLogger("renovai.estimate_audit")

# Deviation thresholds. Deliberately asymmetric: underestimating cost is the
# more damaging error for user trust, so it gets the tighter bound.
UNDERESTIMATE_THRESHOLD_PCT = -5.0
OVERESTIMATE_THRESHOLD_PCT = 10.0

FLAG_UNDER = "ALULBECSÜLT"
FLAG_OVER = "TÚLBECSÜLT"
NO_FLAG = ""
NO_FLAG_DISPLAY = "—"

FALLBACK_NOTE_FLOOR = "(floor fallback — not corpus-backed)"
FALLBACK_NOTE_SINGLE = "(single-quote fallback — weakly corpus-backed)"

REASON_NOT_IN_SCOPE = "Not in estimator scope"
REASON_IN_SCOPE_NO_DATA = "In scope, no comparable corpus data"

REFERENCE_TRUTH_COLUMN = "category_key_v2"


# ---------------------------------------------------------------------------
# Canonical taxonomy (the full ~17-key taxonomy, ASCII keys)
# ---------------------------------------------------------------------------


def ascii_key(value: str) -> str:
    """Fold a category key to lowercase ASCII snake_case.

    Handles the Hungarian accents (á→a, é→e, ő→o, ű→u, ...) so a legacy
    ``category_key`` (``víz_fűtés``) and its canonical ``category_key_v2``
    form (``viz_futes``) collapse to the same key.
    """
    folded = unicodedata.normalize("NFKD", value.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    cleaned = "".join(c if c.isalnum() else "_" for c in folded)
    return "_".join(part for part in cleaned.split("_") if part)


#: Canonical taxonomy keys, in the authoritative seed order.
CANONICAL_TAXONOMY_ORDERED: List[str] = [
    ascii_key(key) for key, *_ in SEED_WORK_CATEGORIES
]
CANONICAL_TAXONOMY_SET = set(CANONICAL_TAXONOMY_ORDERED)

#: canonical key -> bilingual label.
CANONICAL_LABELS: Dict[str, str] = {
    ascii_key(key): f"{label_hu} / {label_en}"
    for key, label_hu, label_en, *_ in SEED_WORK_CATEGORIES
}


def canonical_category_key(value: Optional[str]) -> Optional[str]:
    """Map a stored category value to its canonical taxonomy key, or None."""
    if not value:
        return None
    candidate = ascii_key(value)
    if candidate in CANONICAL_TAXONOMY_SET:
        return candidate
    return None


def _item_category(item: Any, category_column: str) -> Optional[str]:
    """Read a line item's category, falling back to legacy for NULL v2."""
    value = getattr(item, category_column, None)
    if value is None:
        value = item.category_key
    return value


# ---------------------------------------------------------------------------
# Deviation flags
# ---------------------------------------------------------------------------


def classify_deviation(deviation_pct: float) -> str:
    """Return the accuracy flag for a signed deviation percentage.

    ``ALULBECSÜLT`` below -5% (estimate too low), ``TÚLBECSÜLT`` above +10%
    (estimate too high), otherwise no flag.
    """
    if deviation_pct < UNDERESTIMATE_THRESHOLD_PCT:
        return FLAG_UNDER
    if deviation_pct > OVERESTIMATE_THRESHOLD_PCT:
        return FLAG_OVER
    return NO_FLAG


def fallback_flag_note(data_quality: str, fallback_used: bool) -> str:
    """Visible informational note for weakly/non corpus-backed estimates."""
    if data_quality == "no_corpus_data":
        return FALLBACK_NOTE_FLOOR
    if fallback_used:
        return FALLBACK_NOTE_SINGLE
    return ""


# ---------------------------------------------------------------------------
# Category grouping over the full taxonomy
# ---------------------------------------------------------------------------


@dataclass
class CategoryGroup:
    category: str
    label: str
    total_huf: int
    item_count: int
    split_item_count: int

    @property
    def cost_tracking(self) -> str:
        if self.item_count == 0:
            return "none"
        if self.split_item_count == 0:
            return "combined"
        if self.split_item_count == self.item_count:
            return "split"
        return "mixed"


def group_quote_categories(
    quote: Quote, category_column: str = REFERENCE_TRUTH_COLUMN
) -> Dict[str, CategoryGroup]:
    """Group a quote's real, cost-bearing line items by canonical category.

    Unlike ``ground_truth_by_scope()`` this keeps the *whole* taxonomy, not
    just the seven scoped categories, so it can explain what the estimate
    leaves out. Uses the canonical column with a legacy fallback for NULL rows,
    matching the ground-truth convention.
    """
    groups: Dict[str, CategoryGroup] = {}
    for item in quote.line_items:
        cost = item.total_cost_huf or 0
        if not cost:
            continue
        raw = _item_category(item, category_column)
        category = canonical_category_key(raw)
        if category is None and raw:
            category = ascii_key(raw)
        if not category:
            continue
        group = groups.get(category)
        if group is None:
            group = CategoryGroup(
                category=category,
                label=CANONICAL_LABELS.get(category, raw or category),
                total_huf=0,
                item_count=0,
                split_item_count=0,
            )
            groups[category] = group
        group.total_huf += cost
        group.item_count += 1
        if item.labor_cost_huf is not None or item.material_cost_huf is not None:
            group.split_item_count += 1
    return groups


def scopes_cost_tracking(
    quote: Quote, category_column: str = REFERENCE_TRUTH_COLUMN
) -> Dict[str, str]:
    """Cost-tracking style (split/mixed/combined) per scoped category."""
    per_scope_split: Dict[str, List[int]] = {}
    for item in quote.line_items:
        if not item.total_cost_huf:
            continue
        scope = scope_for_category(_item_category(item, category_column))
        if scope is None:
            continue
        counters = per_scope_split.setdefault(scope, [0, 0])
        counters[0] += 1
        if item.labor_cost_huf is not None or item.material_cost_huf is not None:
            counters[1] += 1
    result: Dict[str, str] = {}
    for scope, (total, split) in per_scope_split.items():
        if split == 0:
            result[scope] = "combined"
        elif split == total:
            result[scope] = "split"
        else:
            result[scope] = "mixed"
    return result


# ---------------------------------------------------------------------------
# Audit data model
# ---------------------------------------------------------------------------


@dataclass
class ScopeEstimate:
    scope: str
    label: str
    selected: bool
    estimate_mid_huf: int
    data_quality: str
    fallback_used: bool


@dataclass
class CategoryAudit:
    category: str
    scope: str
    label: str
    ground_truth_huf: int
    estimate_huf: Optional[int]
    deviation_pct: Optional[float]
    flag: str
    flag_note: str
    data_quality: str
    fallback_used: bool
    cost_tracking: str


@dataclass
class CoverageGap:
    category: str
    label: str
    scope: Optional[str]
    reason: str
    total_huf: int
    item_count: int


@dataclass
class AuditResult:
    quote_id: str
    file_name: str
    year: int
    completeness: Optional[str]
    area_sqm: float
    building_type: Optional[str]
    building_era: Optional[int]
    district: Optional[int]
    target_date: str
    estimate_low_huf: int
    estimate_mid_huf: int
    estimate_high_huf: int
    contingency_huf: int
    selected_scopes: List[str]
    scope_estimates: List[ScopeEstimate] = field(default_factory=list)
    categories: List[CategoryAudit] = field(default_factory=list)
    coverage_gaps: List[CoverageGap] = field(default_factory=list)
    training_quote_ids: List[str] = field(default_factory=list)
    price_index_min: Optional[int] = None
    price_index_max: Optional[int] = None
    coverage_warnings: List[str] = field(default_factory=list)
    cost_type_caveats: List[str] = field(default_factory=list)
    # Descriptive input actually handed to the estimator, for leak auditing.
    apartment_input: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Audit execution
# ---------------------------------------------------------------------------


def select_default_apartment(quotes: Sequence[Quote]) -> Optional[Quote]:
    """First ``komplett`` quote (by quote_id) usable by the estimator.

    ``build_apartment_input`` returns non-None when area/building-type/era are
    present; a quote_date is additionally required to build the target date.
    """
    for quote in sorted(quotes, key=lambda q: str(q.id)):
        if quote.renovation_completeness != "komplett":
            continue
        if quote.quote_date is None:
            continue
        if build_apartment_input(quote, ARM1) is None:
            continue
        return quote
    return None


def _find_quote(quotes: Sequence[Quote], quote_id: str) -> Optional[Quote]:
    wanted = str(quote_id).replace("-", "").lower()
    for quote in quotes:
        if str(quote.id).replace("-", "").lower() == wanted:
            return quote
    return None


def _build_scope_estimates(estimate: dict, apt_input: Any) -> List[ScopeEstimate]:
    categories = estimate.get("categories") or {}
    estimates: List[ScopeEstimate] = []
    for scope in SCOPE_NAMES_ORDERED:
        entry = categories.get(scope) or {}
        estimate_huf = entry.get("estimate_huf") or {}
        estimates.append(
            ScopeEstimate(
                scope=scope,
                label=SCOPE_CATEGORY_MAP[scope]["label_en"],
                selected=bool(getattr(apt_input, scope, False)),
                estimate_mid_huf=int(estimate_huf.get("mid") or 0),
                data_quality=str(entry.get("data_quality", "unknown")),
                fallback_used=bool(entry.get("fallback_used", False)),
            )
        )
    return estimates


def _build_category_audits(
    estimate: dict,
    truth: Dict[str, int],
    tracking: Dict[str, str],
) -> List[CategoryAudit]:
    categories = estimate.get("categories") or {}
    audits: List[CategoryAudit] = []
    for scope in SCOPE_NAMES_ORDERED:
        ground_truth = truth.get(scope, 0)
        if ground_truth <= 0:
            continue
        estimate_huf = _category_estimate(estimate, scope)
        entry = categories.get(scope) or {}
        data_quality = str(entry.get("data_quality", "unknown"))
        fallback_used = bool(entry.get("fallback_used", False))
        deviation = (
            _signed_deviation_pct(estimate_huf, ground_truth)
            if estimate_huf is not None
            else None
        )
        audits.append(
            CategoryAudit(
                category=scope,
                scope=scope,
                label=str(SCOPE_CATEGORY_MAP[scope]["label_en"]),
                ground_truth_huf=ground_truth,
                estimate_huf=estimate_huf,
                deviation_pct=None if deviation is None else round(deviation, 3),
                flag=NO_FLAG if deviation is None else classify_deviation(deviation),
                flag_note=fallback_flag_note(data_quality, fallback_used),
                data_quality=data_quality,
                fallback_used=fallback_used,
                cost_tracking=tracking.get(scope, "none"),
            )
        )
    return audits


def _build_coverage_gaps(
    groups: Dict[str, CategoryGroup],
    estimate: dict,
    apt_input: Any,
) -> List[CoverageGap]:
    categories = estimate.get("categories") or {}
    ordered = [c for c in CANONICAL_TAXONOMY_ORDERED if c in groups]
    ordered += sorted(c for c in groups if c not in CANONICAL_TAXONOMY_SET)
    gaps: List[CoverageGap] = []
    for category in ordered:
        group = groups[category]
        scope = scope_for_category(category)
        if scope is not None:
            entry = categories.get(scope) or {}
            selected = bool(getattr(apt_input, scope, False))
            corpus_backed = entry.get("data_quality") != "no_corpus_data"
            if selected and corpus_backed:
                continue  # in the estimate as a real, corpus-backed number
            reason = REASON_IN_SCOPE_NO_DATA
        else:
            reason = REASON_NOT_IN_SCOPE
        gaps.append(
            CoverageGap(
                category=category,
                label=group.label,
                scope=scope,
                reason=reason,
                total_huf=group.total_huf,
                item_count=group.item_count,
            )
        )
    return gaps


def _build_cost_type_caveats(audits: List[CategoryAudit]) -> List[str]:
    caveats: List[str] = []
    for audit in audits:
        if audit.cost_tracking == "combined":
            caveats.append(
                f"`{audit.scope}`: the real line items in this category carry "
                "only a combined `total_cost_huf` (the `labor_cost_huf` / "
                "`material_cost_huf` split is null). The deviation above is "
                "left as-is; the missing split is a data-quality flag, not "
                "something this report normalizes away."
            )
        elif audit.cost_tracking == "mixed":
            caveats.append(
                f"`{audit.scope}`: some real line items in this category track "
                "labor and material separately while others carry only a "
                "combined total. The deviation above is left as-is."
            )
    return caveats


async def run_audit(
    database_url: str,
    price_index: PriceIndex,
    *,
    quote_id: Optional[str] = None,
    workdir: Optional[Path] = None,
) -> AuditResult:
    """Run the single-apartment audit against a leaked-guarded DB copy."""
    url = make_url(database_url)
    if not url.database:
        raise ValueError(f"database_url has no file path: {database_url!r}")
    source_db_path = Path(url.database)
    if not source_db_path.exists():
        raise FileNotFoundError(source_db_path)

    quotes = await _load_quotes(database_url)
    if quote_id is not None:
        quote = _find_quote(quotes, quote_id)
        if quote is None:
            raise ValueError(f"quote_id not found in corpus: {quote_id!r}")
    else:
        quote = select_default_apartment(quotes)
        if quote is None:
            raise ValueError(
                "No usable `komplett` quote found: every komplett quote is "
                "missing area/building-type/era or a quote_date."
            )

    if quote.quote_date is None:
        raise ValueError(f"quote {quote.id} has no quote_date")
    apt_input = build_apartment_input(quote, ARM1)
    if apt_input is None:
        raise ValueError(
            f"quote {quote.id} is missing area_sqm/building_type/building_era"
        )

    target_date = date(quote.quote_date.year, 1, 1)
    warnings, year_min, year_max = check_year_coverage(
        [quote.quote_date.year], price_index
    )

    owns_workdir = workdir is None
    workdir = (
        Path(tempfile.mkdtemp(prefix="renovai_audit_"))
        if workdir is None
        else Path(workdir)
    )
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        arm0_input = build_apartment_input(quote, "category_key")
        _, v2_result, training_ids = await _execute_fold(
            source_db_path,
            quote.id,
            price_index,
            target_date,
            arm0_input if arm0_input is not None else apt_input,
            apt_input,
            workdir,
        )
    finally:
        if owns_workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    if v2_result is None:
        raise ValueError(f"estimator returned no result for quote {quote.id}")

    # Ground truth is read only now, after the estimate has run.
    truth = ground_truth_by_scope(quote)
    groups = group_quote_categories(quote)
    tracking = scopes_cost_tracking(quote)
    audits = _build_category_audits(v2_result, truth, tracking)

    debug = v2_result.get("debug") or {}
    result = AuditResult(
        quote_id=str(quote.id),
        file_name=quote.file_name,
        year=quote.quote_date.year,
        completeness=quote.renovation_completeness,
        area_sqm=float(quote.area_sqm),
        building_type=str(getattr(quote.building_type, "value", quote.building_type)),
        building_era=(
            int(quote.building_era) if quote.building_era is not None else None
        ),
        district=quote.district,
        target_date=target_date.isoformat(),
        estimate_low_huf=int(v2_result["estimate_low_huf"]),
        estimate_mid_huf=int(v2_result["estimate_mid_huf"]),
        estimate_high_huf=int(v2_result["estimate_high_huf"]),
        contingency_huf=int(debug.get("contingency_huf", 0)),
        selected_scopes=[
            scope for scope in SCOPE_NAMES_ORDERED if getattr(apt_input, scope)
        ],
        scope_estimates=_build_scope_estimates(v2_result, apt_input),
        categories=audits,
        coverage_gaps=_build_coverage_gaps(groups, v2_result, apt_input),
        training_quote_ids=training_ids,
        price_index_min=year_min,
        price_index_max=year_max,
        coverage_warnings=warnings,
        cost_type_caveats=_build_cost_type_caveats(audits),
        apartment_input=apt_input.model_dump(),
    )
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _format_huf(value: Optional[int]) -> str:
    if value is None:
        return "—"
    return f"{value:,}".replace(",", " ")


def _format_pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:+.1f}%"


def _flag_display(audit: CategoryAudit) -> str:
    if not audit.flag:
        base = NO_FLAG_DISPLAY
    else:
        base = audit.flag
    return f"{base} {audit.flag_note}".strip()


def _full_estimate_table(result: AuditResult) -> List[str]:
    lines = [
        "| scope | in this apartment's needs? | estimate (mid) | corpus quality |",
        "|---|---|---:|---|",
    ]
    for estimate in result.scope_estimates:
        selected = "yes" if estimate.selected else "no"
        lines.append(
            f"| {estimate.scope} | {selected} | "
            f"{_format_huf(estimate.estimate_mid_huf)} | {estimate.data_quality} |"
        )
    lines.append("")
    return lines


def _category_table(result: AuditResult) -> List[str]:
    lines = [
        "| category | ground truth | estimate (mid) | deviation | flag | "
        "cost tracking |",
        "|---|---:|---:|---:|---|---|",
    ]
    for audit in result.categories:
        lines.append(
            f"| {audit.category} | {_format_huf(audit.ground_truth_huf)} | "
            f"{_format_huf(audit.estimate_huf)} | "
            f"{_format_pct(audit.deviation_pct)} | {_flag_display(audit)} | "
            f"{audit.cost_tracking} |"
        )
    lines.append("")
    return lines


def _coverage_table(result: AuditResult) -> List[str]:
    if not result.coverage_gaps:
        return [
            "None. Every category this apartment's real quote has cost in is "
            "represented by a corpus-backed estimate.",
            "",
        ]
    lines = [
        "| category | reason | real total | line items |",
        "|---|---|---:|---:|",
    ]
    for gap in result.coverage_gaps:
        lines.append(
            f"| {gap.category} | {gap.reason} | {_format_huf(gap.total_huf)} | "
            f"{gap.item_count} |"
        )
    lines.append("")
    return lines


def render_report(result: AuditResult) -> str:
    """Render the standalone Markdown audit report."""
    selected = ", ".join(result.selected_scopes) if result.selected_scopes else "none"
    lines: List[str] = [
        f"# Estimate audit — {result.file_name}",
        "",
        "**Generated by `scripts/estimate_audit.py`.** Deterministic and "
        "rule-based: no ML model, no LLM call, no network access. It reuses "
        "`scope_matched_estimate()` and the leave-one-out leak guard rather "
        "than reimplementing either.",
        "",
        "## What this report checks",
        "",
        "- **The full-renovation estimate** for one demonstration apartment, "
        "summed by the estimator across the scope categories the apartment "
        "needs, with each scope's contribution shown explicitly.",
        "- **Per-category accuracy** — the estimator's mid estimate against the "
        "apartment's own real line-item totals, with a signed deviation and a "
        "flag when the miss is large.",
        "- **Category coverage** — every taxonomy category the apartment's real "
        "quote spends money on is listed with a reason if the estimate does not "
        "cover it, so a gap is never silently ignored.",
        "",
        "## Demonstration apartment",
        "",
        f"- **File:** {result.file_name}",
        f"- **Quote id:** `{result.quote_id}`",
        f"- **Year / target date:** {result.year} / {result.target_date}",
        f"- **Area:** {result.area_sqm:.0f} m²",
        f"- **Building type / era:** {result.building_type} / {result.building_era}",
        f"- **Completeness:** {result.completeness}",
        f"- **District:** {result.district}",
        f"- **Scopes in this apartment's estimate:** {selected}",
        "",
        "**Selection rule.** The quote is the first one in stable `quote_id` "
        "order whose completeness is `komplett` and which has the "
        "area/building-type/era/date data the estimator needs to run. A "
        "`komplett` quote's own real category coverage is used as the reference "
        "for what a full renovation means **for this one example**. It is not a "
        "universal renovation checklist and must not be read as one: another "
        "apartment may need a different set of categories, and this report makes "
        "no claim about a canonical renovation scope.",
        "",
        "## Methodology",
        "",
        "- **No leakage.** The quote is deleted from a throwaway copy of the "
        "database before the estimator runs (the same leak guard as "
        "`scripts/loocv_validation.py`). Its real totals are read from the "
        "source database only afterwards, and only as ground truth.",
        "- **Canonical categorization.** The estimate uses "
        '`category_column="category_key_v2"`, the canonical ASCII taxonomy.',
        "- **Ground truth** is the quote's own real `total_cost_huf` summed per "
        "scope under the canonical column (legacy fallback for `NULL` rows).",
        "- **Deviation** is `(estimate - ground_truth) / ground_truth * 100`, "
        "so a negative value means the estimate is below reality.",
        "- **Flags** are deliberately asymmetric. `ALULBECSÜLT` fires below "
        "-5%; `TÚLBECSÜLT` fires above +10%; anything in between is unflagged. "
        "Underestimating cost is the more damaging error for user trust "
        "(a buyer is misled about affordability), so it gets the tighter bound.",
        f"- **Informational fallback flags.** When a category's estimate is a "
        f"floor fallback with no comparable corpus data, the flag is shown with "
        f"the visible note `{FALLBACK_NOTE_FLOOR}` and is *not* a confident "
        "accuracy claim: a floor-versus-reality deviation says the corpus has "
        "nothing to compare against, not that the corpus is wrong.",
        "- **Cost-type caveats.** Whether each category's real items split "
        "labor/material or carry only a combined total is reported as a "
        "data-quality flag. Nothing is normalized, reallocated, or corrected.",
        "",
    ]

    if result.coverage_warnings:
        lines += ["## Price-index coverage warnings", ""]
        lines += [f"- {warning}" for warning in result.coverage_warnings]
        lines.append("")

    lines += [
        "## Full-renovation estimate",
        "",
        f"- **Mid estimate:** {_format_huf(result.estimate_mid_huf)} HUF",
        f"- **Low / high band:** {_format_huf(result.estimate_low_huf)} / "
        f"{_format_huf(result.estimate_high_huf)} HUF",
        f"- **Fixed contingency included:** {_format_huf(result.contingency_huf)} "
        "HUF (permits, scaffolding, etc.)",
        f"- **Scopes summed into the headline:** {selected}",
        "",
        "The estimator returns a single mid figure computed as the fixed "
        "contingency plus the per-scope average cost-per-m² times this "
        "apartment's area, summed over the scopes marked `yes` below. Scopes "
        "marked `no` are categories this apartment's own quote had no work in; "
        "their standalone per-category figures are still shown for transparency "
        "but are deliberately **not** added, because a full renovation here "
        "means this apartment's renovation, not a hypothetical one.",
        "",
    ]
    lines += _full_estimate_table(result)

    lines += [
        "## Per-category estimate vs. ground truth",
        "",
        "Each row is one scope category this apartment's real quote has cost "
        "in. `ground truth` is the real total, `estimate` is the estimator's "
        "mid figure for the same scope, and the flag reflects the deviation "
        "thresholds defined above.",
        "",
    ]
    lines += _category_table(result)

    # Explain every flag that actually appears, in prose, next to the table.
    under = [a for a in result.categories if a.flag == FLAG_UNDER]
    over = [a for a in result.categories if a.flag == FLAG_OVER]
    note_categories = [a for a in result.categories if a.flag_note]
    if under or over or note_categories:
        lines += ["### Reading the flags in this report", ""]
        for audit in result.categories:
            if not audit.flag and not audit.flag_note:
                continue
            if audit.flag == FLAG_UNDER:
                claim = (
                    f"`{audit.category}` is **ALULBECSÜLT** "
                    f"({_format_pct(audit.deviation_pct)}): the estimate is "
                    "more than 5% below this apartment's real cost."
                )
            elif audit.flag == FLAG_OVER:
                claim = (
                    f"`{audit.category}` is **TÚLBECSÜLT** "
                    f"({_format_pct(audit.deviation_pct)}): the estimate is "
                    "more than 10% above this apartment's real cost."
                )
            else:
                claim = (
                    f"`{audit.category}` is inside the unflagged band "
                    f"({_format_pct(audit.deviation_pct)})."
                )
            if audit.flag_note:
                claim += (
                    f" The flag is informational only — {audit.flag_note}. "
                    "This means the estimator had nothing solid to compare "
                    "against, so the deviation is not evidence the corpus "
                    "estimate is wrong; treat it as a data-coverage gap."
                )
            lines.append(f"- {claim}")
        lines.append("")

    lines += [
        "## Category coverage — why isn't this in the estimate?",
        "",
        "The rows below are taxonomy categories this apartment's real quote "
        "spends money on but which are **not** represented by a corpus-backed "
        "estimate. A category that is in estimator scope *and* received a "
        "corpus-backed estimate is not missing, so it is left out of this "
        "section. Each row is classified with exactly one reason:",
        "",
        f"- **{REASON_NOT_IN_SCOPE}** — the category is not one of the seven "
        "categories wired into `SCOPE_CATEGORY_MAP`, so the estimator has no "
        "scope bucket to place it in. These gaps are structural and out of "
        "scope for this task; wiring them in is a separate change.",
        f"- **{REASON_IN_SCOPE_NO_DATA}** — the category does belong to a scope, "
        "but the estimator found no comparable corpus quotes for this "
        "apartment's profile (after the held-out quote was removed), so it fell "
        "back to the scope's minimum-premium floor. The category is effectively "
        "absent from the corpus-backed estimate.",
        "",
    ]
    lines += _coverage_table(result)

    lines += ["## Labor / material cost-type caveats", ""]
    if result.cost_type_caveats:
        lines += [f"- {caveat}" for caveat in result.cost_type_caveats]
    else:
        lines += [
            "None of the scored categories used combined (total-only) cost "
            "tracking: every real line item in those categories carries at "
            "least one of `labor_cost_huf` / `material_cost_huf`, so no "
            "labor/material split caveat applies to this apartment.",
        ]
    lines.append("")

    lines += [
        "## Notes and limitations",
        "",
        "- This audit is one apartment, not a corpus statistic. For corpus-wide "
        "accuracy see `docs/loocv_validation_report.md`.",
        f"- `num_rooms` is the documented placeholder constant `0`; the "
        "estimator does not read it.",
        "- Ground truth sums all cost-bearing line items, matching the "
        "estimator's own corpus-aggregation convention.",
        "- The 'not in estimator scope' list describes this apartment's real "
        "coverage only; it is not a claim that those categories are never "
        "estimable in future versions.",
        "- This harness makes no LLM calls and performs no price arithmetic of "
        "its own beyond signed deviation percentages.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Machine-readable output
# ---------------------------------------------------------------------------


def result_to_records(result: AuditResult) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for audit in result.categories:
        record = {
            "quote_id": result.quote_id,
            "file_name": result.file_name,
        }
        record.update(asdict(audit))
        records.append(record)
    return records


def write_machine_readable(
    result: AuditResult, csv_path: Path, json_path: Path
) -> None:
    records = result_to_records(result)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        if records:
            writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rule-based estimate audit report for one apartment"
    )
    parser.add_argument(
        "--database-url",
        default="sqlite+aiosqlite:///data/renovai.db",
        help="SQLite database URL (the held-out copy is made from its file)",
    )
    parser.add_argument(
        "--materials-cpi", default="data/raw/inflation/materials_cpi.csv"
    )
    parser.add_argument("--labor-cpi", default="data/raw/inflation/labor_cpi.csv")
    parser.add_argument(
        "--quote-id",
        default=None,
        help="audit this specific quote id instead of the default pick",
    )
    parser.add_argument("--out-dir", default="docs")
    parser.add_argument(
        "--report", default="estimate_audit_report.md", help="report filename"
    )
    parser.add_argument("--csv", default="estimate_audit_results.csv")
    parser.add_argument("--json", default="estimate_audit_results.json")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    price_index = load_price_index(Path(args.materials_cpi), Path(args.labor_cpi))
    result = asyncio.run(
        run_audit(args.database_url, price_index, quote_id=args.quote_id)
    )

    out_dir = Path(args.out_dir)
    report_path = out_dir / args.report
    csv_path = out_dir / args.csv
    json_path = out_dir / args.json
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(result), encoding="utf-8")
    write_machine_readable(result, csv_path, json_path)

    print(f"Audited quote: {result.file_name} ({result.quote_id})")
    print(f"Full-renovation mid estimate: {_format_huf(result.estimate_mid_huf)} HUF")
    for audit in result.categories:
        print(
            f"  {audit.category}: truth {_format_huf(audit.ground_truth_huf)}, "
            f"estimate {_format_huf(audit.estimate_huf)}, "
            f"deviation {_format_pct(audit.deviation_pct)} "
            f"{audit.flag} {audit.flag_note}".rstrip()
        )
    print(f"Report: {report_path}")
    print(f"CSV:    {csv_path}")
    print(f"JSON:   {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
