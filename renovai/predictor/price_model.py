import logging
import numpy as np
from datetime import date
from pathlib import Path
from typing import List, Optional, Dict, Any, Set
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .feature_extractor import QuoteFeatures, ApartmentInput, extract_features
from ..ingestion.inflation_models import PriceIndex
from ..ingestion.inflation_calc import get_factor
from ..db.models import Quote

logger = logging.getLogger(__name__)

SCOPE_CATEGORY_MAP: Dict[str, Dict[str, Any]] = {
    "needs_plumbing": {
        "keys": {"víz_fűtés"},
        "label_hu": "Víz és fűtés",
        "label_en": "Plumbing & Heating",
        "min_premium": 300_000,
    },
    "needs_electrical": {
        "keys": {"villany"},
        "label_hu": "Villanyszerelés",
        "label_en": "Electrical",
        "min_premium": 250_000,
    },
    "needs_flooring": {
        "keys": {"burkolás", "parketta"},
        "label_hu": "Burkolás/parketta",
        "label_en": "Flooring/Tiling",
        "min_premium": 350_000,
    },
    "needs_full_demolition": {
        "keys": {"bontás"},
        "label_hu": "Bontás",
        "label_en": "Demolition",
        "min_premium": 200_000,
    },
    "needs_windows_doors": {
        "keys": {"nyílászáró"},
        "label_hu": "Nyílászáró csere",
        "label_en": "Windows & Doors",
        "min_premium": 400_000,
    },
    "needs_insulation": {
        "keys": {"szigetelés"},
        "label_hu": "Szigetelés",
        "label_en": "Insulation",
        "min_premium": 350_000,
    },
    "needs_ac": {
        "keys": {"klíma"},
        "label_hu": "Klíma szerelés",
        "label_en": "AC Installation",
        "min_premium": 300_000,
    },
}

SCOPE_NAMES_ORDERED = [
    "needs_plumbing",
    "needs_electrical",
    "needs_flooring",
    "needs_full_demolition",
    "needs_windows_doors",
    "needs_insulation",
    "needs_ac",
]

REFERENCE_AREA_SQM = 55.0


def _get_quote_scopes(quote: Quote) -> Set[str]:
    cats = set()
    for li in quote.line_items:
        if li.category_key:
            cats.add(li.category_key)
    result = set()
    for scope_name, info in SCOPE_CATEGORY_MAP.items():
        if cats & info["keys"]:
            result.add(scope_name)
    return result


def _get_user_scopes(apt: ApartmentInput) -> Set[str]:
    result: Set[str] = set()
    if apt.needs_plumbing:
        result.add("needs_plumbing")
    if apt.needs_electrical:
        result.add("needs_electrical")
    if apt.needs_flooring:
        result.add("needs_flooring")
    if apt.needs_full_demolition:
        result.add("needs_full_demolition")
    if apt.needs_windows_doors:
        result.add("needs_windows_doors")
    if apt.needs_insulation:
        result.add("needs_insulation")
    if apt.needs_ac:
        result.add("needs_ac")
    return result


def _category_breakdown(quotes: List[Quote]) -> List[Dict[str, Any]]:
    """Aggregate per-category costs from DB quotes (same logic as load_cost_breakdown)."""
    from .feature_extractor import CATEGORY_KEYWORDS
    from ..predictor.cost_breakdown import CATEGORY_LABELS_HU, CATEGORY_LABELS_EN

    categories: Dict[str, List[int]] = {k: [] for k in CATEGORY_KEYWORDS}

    for q in quotes:
        for li in q.line_items:
            name_lower = (li.name_hu or "").lower()
            cost = li.total_cost_huf
            if not cost:
                continue
            for cat_key, keywords in CATEGORY_KEYWORDS.items():
                if any(k in name_lower for k in keywords):
                    categories[cat_key].append(cost)
                    break

    rows = []
    for key, values in categories.items():
        if not values:
            continue
        arr = np.array(values)
        rows.append({
            "category_key": key,
            "category_hu": CATEGORY_LABELS_HU.get(key, key),
            "category_en": CATEGORY_LABELS_EN.get(key, key),
            "count": len(values),
            "avg_huf": int(round(arr.mean())),
            "median_huf": int(round(np.median(arr))),
            "min_huf": int(arr.min()),
            "max_huf": int(arr.max()),
        })
    rows.sort(key=lambda r: r["avg_huf"], reverse=True)
    return rows


# Cache for monotonicity guard (scope combo -> list of (area_sqm, estimate, warning))
_monotonicity_cache: Dict[str, list] = {}


def _cache_key(apt: ApartmentInput) -> str:
    return (
        f"p{apt.needs_plumbing}_e{apt.needs_electrical}_f{apt.needs_flooring}"
        f"_d{apt.needs_full_demolition}_w{apt.needs_windows_doors}"
        f"_i{apt.needs_insulation}_a{apt.needs_ac}"
    )


def _check_monotonicity(
    apt_input: ApartmentInput,
    estimate_mid: int,
    price_per_sqm: float,
) -> Optional[str]:
    """Defense-in-depth: same-scope estimate must not decrease as area increases.

    Returns a warning string if monotonicity is violated, else None.
    """
    key = _cache_key(apt_input)
    if key not in _monotonicity_cache:
        _monotonicity_cache[key] = []
    history = _monotonicity_cache[key]

    # Check all cached entries for this scope combo
    warning = None
    for cached_area, cached_est, _ in history:
        if apt_input.total_area_sqm > cached_area and estimate_mid < cached_est:
            warning = (
                "Elégtelen összehasonlítható adat a terület alapú skálázáshoz — "
                "a nagyobb alapterületű becslés alacsonyabb, mint egy kisebbé. "
                "Az eredmény fenntartással kezelendő."
            )
            break
        if apt_input.total_area_sqm < cached_area and estimate_mid > cached_est:
            warning = (
                "Elégtelen összehasonlítható adat a terület alapú skálázáshoz — "
                "a kisebb alapterületű becslés magasabb, mint egy nagyobbé. "
                "Az eredmény fenntartással kezelendő."
            )
            break

    _monotonicity_cache[key].append((apt_input.total_area_sqm, estimate_mid, warning))
    return warning


async def scope_matched_estimate(
    apt_input: ApartmentInput,
    session_maker,
    price_index: PriceIndex,
    target_date: date,
) -> Optional[dict]:
    """Estimates renovation cost using price-per-m² normalization.

    For every historical quote, computes scope-level cost per square meter,
    averages in per-sqm space, then multiplies by the query area.
    This makes area the dominant cost driver instead of a post-hoc scaling factor.

    Returns same dict shape as predict() — estimate_low/mid/high_huf + debug.
    Guarantees: more selected scopes → higher estimate.
    """
    if not session_maker:
        logger.error("scope_matched_estimate: no session_maker provided")
        return None

    # 1 — Load all quotes with line items (include area_sqm)
    async with session_maker() as session:
        stmt = select(Quote).options(selectinload(Quote.line_items))
        res = await session.execute(stmt)
        quotes = res.scalars().all()

    if not quotes:
        logger.warning("No quotes in database")
        return None

    user_scopes = _get_user_scopes(apt_input)
    num_user_scopes = len(user_scopes)
    if num_user_scopes == 0:
        return None

    # 2 — Aggregate per-quote scope costs, normalized by area
    # scope_per_sqm[scope_name] = list of per-sqm costs across quotes
    scope_per_sqm: Dict[str, List[float]] = {}
    for scope_name in SCOPE_NAMES_ORDERED:
        scope_per_sqm[scope_name] = []

    for q in quotes:
        total = q.grand_total_adjusted_huf or q.grand_total_huf
        if not total:
            continue
        area = q.area_sqm or REFERENCE_AREA_SQM
        if area <= 0:
            continue
        # Group line items by scope for this quote
        scope_line_total: Dict[str, int] = {}
        for li in q.line_items:
            if not li.total_cost_huf:
                continue
            for scope_name, info in SCOPE_CATEGORY_MAP.items():
                if li.category_key in info["keys"]:
                    scope_line_total[scope_name] = (
                        scope_line_total.get(scope_name, 0) + li.total_cost_huf
                    )
                    break
        # Normalize each scope cost by this quote's area
        for scope_name, cost_total in scope_line_total.items():
            scope_per_sqm[scope_name].append(cost_total / area)

    # 3 — Average per-sqm cost per scope using inverse-variance weighting.
    # Each quote's weight = 1 / ((x_i - mean)^2 + eps), so quotes far from
    # the category mean (outliers, partial-scope quotes) are downweighted
    # automatically without a full Bayesian model.
    _IVW_EPS = 1.0  # prevent division by zero when a quote lands on the mean
    scope_avg_per_sqm: Dict[str, float] = {}
    scope_count: Dict[str, int] = {}
    for scope_name in SCOPE_NAMES_ORDERED:
        vals = scope_per_sqm[scope_name]
        scope_count[scope_name] = len(vals)
        if len(vals) >= 2:
            arr = np.array(vals, dtype=float)
            mean = arr.mean()
            variances = (arr - mean) ** 2 + _IVW_EPS
            weights = 1.0 / variances
            scope_avg_per_sqm[scope_name] = float(np.sum(weights * arr) / np.sum(weights))
        else:
            # Insufficient corpus data for this scope — use min_premium as floor
            scope_avg_per_sqm[scope_name] = (
                SCOPE_CATEGORY_MAP[scope_name]["min_premium"] / REFERENCE_AREA_SQM
            )
            if scope_count[scope_name] == 0:
                logger.warning(
                    "Scope '%s': no historical quotes in corpus; "
                    "using minimum premium floor (%d HUF)",
                    scope_name,
                    SCOPE_CATEGORY_MAP[scope_name]["min_premium"],
                )

    # 4 — Estimate = fixed contingency + sum(scope_per_sqm * query_area)
    CONTINGENCY = 200_000  # fixed costs (permits, scaffolding, etc.)
    query_area = apt_input.total_area_sqm
    scope_total_per_sqm = sum(scope_avg_per_sqm[s] for s in user_scopes)
    estimate_mid = int(CONTINGENCY + scope_total_per_sqm * query_area)
    estimate_mid = max(estimate_mid, 500_000)

    # 4b — Monotonicity guard
    monotonicity_warning = _check_monotonicity(apt_input, estimate_mid, scope_total_per_sqm)

    # 5 — Compute inflation factors (NOT applied here — caller applies inflation LAST
    # after all structural add-ons are summed, so every cost component is inflated
    # consistently to the target date).
    baseline_date = date(2024, 2, 15)
    f_labor = get_factor(price_index, "labor", baseline_date, target_date)
    f_material = get_factor(price_index, "materials", baseline_date, target_date)

    # 6 — Return predict()-compatible dict (pre-inflation estimates)
    result = {
        "estimate_low_huf": int(estimate_mid * 0.85),
        "estimate_mid_huf": estimate_mid,
        "estimate_high_huf": int(estimate_mid * 1.20),
        "inflation_adjusted_to": target_date.isoformat(),
        "inflation_factor_labor": round(f_labor, 3),
        "inflation_factor_materials": round(f_material, 3),
        "model_used": "scope_matched_per_sqm",
        "warning": monotonicity_warning,
        "debug": {
            "num_queries_in_db": len(quotes),
            "num_user_scopes": num_user_scopes,
            "contingency_huf": CONTINGENCY,
            "query_area_sqm": query_area,
            "scope_avg_per_sqm_huf": {k: round(v, 0) for k, v in scope_avg_per_sqm.items()},
            "scope_line_item_count": scope_count,
            "scope_data_warnings": {
                s: ("no_corpus_data" if scope_count[s] == 0 else "single_quote")
                for s in user_scopes if scope_count[s] < 2
            },
            "scope_total_per_sqm_huf": round(scope_total_per_sqm, 0),
            "price_per_sqm_huf": round((CONTINGENCY + scope_total_per_sqm * query_area) / query_area, 0) if query_area > 0 else 0,
        },
    }
    return result


def find_similar_quotes(
    features: QuoteFeatures,
    quotes_dir: Path,
    top_k: int = 3,
) -> List[dict]:
    """Finds top_k similar historical quotes using Euclidean distance on numeric features."""
    all_feats = []

    num_cols = [
        "district",
        "num_line_items",
        "labor_to_material_ratio",
        "demolition_cost_share",
        "plumbing_cost_share",
    ]

    target_vec = np.array([getattr(features, col) for col in num_cols])

    for file in quotes_dir.glob("*_adjusted.json"):
        try:
            f = extract_features(file)
            vec = np.array([getattr(f, col) for col in num_cols])
            dist = np.linalg.norm(target_vec - vec)
            all_feats.append({
                "file": file.name,
                "address": f.district,
                "grand_total_adjusted": f.grand_total_adjusted,
                "distance": dist,
            })
        except Exception:
            continue

    all_feats = [f for f in all_feats if f.get("grand_total_adjusted")]
    all_feats.sort(key=lambda x: x["distance"])
    return all_feats[:top_k]


def predict(
    features: QuoteFeatures,
    price_index: PriceIndex,
    target_date: date,
    model_dir: Optional[Path] = None,
) -> dict:
    """Backward-compatible predict() that delegates to cost-breakdown averages.

    Uses the per-category averages from load_cost_breakdown() instead of
    the old ML model.  The `model_dir` argument is accepted but ignored.
    """
    from .cost_breakdown import load_cost_breakdown
    from ..api.config import AppConfig

    cfg = AppConfig()
    cost_data = load_cost_breakdown(Path(cfg.quotes_json_dir))

    SCOPE_FEATURE_MAP: Dict[str, str] = {
        "plumbing_heating": "has_plumbing_work",
        "electrical": "has_electrical_work",
        "tiling": "has_flooring_work",
        "flooring": "has_flooring_work",
        "demolition": "has_demolition",
    }

    total = 200_000  # base contingency
    for row in cost_data:
        key = row["category_key"]
        feature = SCOPE_FEATURE_MAP.get(key)
        if feature and getattr(features, feature, False):
            total += row["avg_huf"]

    # Area scaling
    user_area = features.total_area_sqm or 55.0
    total = int(total * user_area / REFERENCE_AREA_SQM)
    total = max(total, 500_000)

    # Inflation
    baseline_date = date(2024, 2, 15)
    f_labor = get_factor(price_index, "labor", baseline_date, target_date)
    f_material = get_factor(price_index, "materials", baseline_date, target_date)
    labor_share = 0.55
    total_adj = int(total * (labor_share * f_labor + (1.0 - labor_share) * f_material))

    return {
        "estimate_low_huf": int(total_adj * 0.85),
        "estimate_mid_huf": total_adj,
        "estimate_high_huf": int(total_adj * 1.20),
        "inflation_adjusted_to": target_date.isoformat(),
        "inflation_factor_labor": round(f_labor, 3),
        "inflation_factor_materials": round(f_material, 3),
        "model_used": "cost_breakdown",
        "warning": "predict() is deprecated; use scope_matched_estimate() for DB-backed estimates.",
    }
