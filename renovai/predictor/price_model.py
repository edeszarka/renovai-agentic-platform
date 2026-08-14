import logging
import numpy as np
from datetime import date
from pathlib import Path
from typing import List, Optional, Dict, Any, Set, Tuple
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .feature_extractor import QuoteFeatures, ApartmentInput, extract_features
from ..ingestion.inflation_models import PriceIndex
from ..ingestion.inflation_calc import inflate_quote_value, compound_inflation_factor
from ..db.models import Quote, BuildingType

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


# ---------------------------------------------------------------------------
# Building-type / era similarity weights (third multiplicative factor)
# ---------------------------------------------------------------------------
# Multiplicatively combined with the existing IVW × recency weights in
# scope_matched_estimate(): same-type-and-close-era quotes get full weight;
# distant matches are down-weighted but NEVER excluded — the corpus is only
# ~47 quotes, so exclusion would starve scopes to zero matches.

_HU_ACCENT_MAP = str.maketrans("áéíóöőúüűÁÉÍÓÖŐÚÜŰ", "aeiooouuüAEIOOOUUU")
_TYPE_MISMATCH_WEIGHT = 0.5  # different type: down-weighted, not excluded
_ERA_MATCH_BAND_YEARS = 10  # |Δ| <= this → full era weight
_ERA_FLOOR_WEIGHT = 0.3  # floor for very distant eras (never 0)

# Canonical doc-01 taxonomy tokens (BuildingType enum values, already
# ASCII). Used for type matching so that e.g. "tegla" (apartment brick)
# does NOT substring-match "teglacsaladihaz" (family-house brick) — those
# are distinct doc-01 categories that merely share a prefix.
_CANONICAL_TYPE_TOKENS = {bt.value for bt in BuildingType}


def _fold_type(value: Optional[str]) -> str:
    return (value or "").strip().lower().translate(_HU_ACCENT_MAP)


def _quote_type_token(building_type: Any) -> str:
    """Normalized token for a Quote.building_type (BuildingType enum member)."""
    if building_type is None:
        return ""
    try:
        return _fold_type(building_type.value)
    except AttributeError:
        return _fold_type(str(building_type))


def _canonical_types_in_text(text: str) -> Set[str]:
    """Which doc-01 taxonomy tokens appear in a (folded) free-text type.

    Returns the canonical tokens that are substrings of ``text``. This keeps
    free-text user input ("1980 előtti tégla") matchable while never letting
    a canonical token match a different canonical token (substring-matching
    is applied only against the raw user text, not against other tokens).
    """
    found: Set[str] = set()
    for token in _CANONICAL_TYPE_TOKENS:
        if token in text:
            found.add(token)
    return found


def building_type_similarity(user_type: Optional[str], quote_type: Any) -> float:
    """Weight by building-type match (1.0 match, 0.5 mismatch, 1.0 if unknown).

    User free text is decomposed into canonical doc-01 tokens; a quote's
    canonical type matches if it is one of those tokens. Because canonical
    tokens are never substring-matched against *each other*, "tegla" cannot
    match "teglacsaladihaz" (distinct doc-01 categories).
    """
    u = _fold_type(user_type)
    q = _quote_type_token(quote_type)
    if not u or not q:
        return 1.0  # unknown on either side → neutral, don't starve the corpus
    user_canonical = _canonical_types_in_text(u)
    if not user_canonical:
        return 1.0  # free text had no recognizable type → neutral
    if q in user_canonical:
        return 1.0
    return _TYPE_MISMATCH_WEIGHT


def era_similarity(user_era: Optional[int], quote_era: Optional[int]) -> float:
    """Weight by era closeness (1.0 close, linear decay to floor, never 0)."""
    if user_era is None or quote_era is None:
        return 1.0
    try:
        delta = abs(int(user_era) - int(quote_era))
    except (TypeError, ValueError):
        return 1.0
    if delta <= _ERA_MATCH_BAND_YEARS:
        return 1.0
    return max(_ERA_FLOOR_WEIGHT, 1.0 - (delta - _ERA_MATCH_BAND_YEARS) / 100.0)


def building_similarity_weight(apt: ApartmentInput, quote: Quote) -> float:
    """Combined type × era similarity weight for a quote (multiplicative)."""
    return building_type_similarity(apt.building_type, quote.building_type) * era_similarity(
        apt.building_era, quote.building_era
    )


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    """Weighted arithmetic mean. Self-normalizing: a uniform scaling of
    ``weights`` cancels (Σ(c·w·x)/Σ(c·w) == Σ(w·x)/Σ(w)), so combined
    weights never need an explicit renormalization pass."""
    return float(np.sum(weights * values) / np.sum(weights))


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
    include_breakdown: bool = True,
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

    # 2 — Aggregate per-quote scope costs, normalized by area and inflated to target_date.
    # Store (per_sqm_value, quote_year) pairs per scope; track raw line-item counts.
    scope_entries: Dict[str, List[Tuple[float, int]]] = {}
    scope_line_items: Dict[str, int] = {}
    infl_factors: List[float] = []
    for scope_name in SCOPE_NAMES_ORDERED:
        scope_entries[scope_name] = []
        scope_line_items[scope_name] = 0

    for q in quotes:
        total = q.grand_total_adjusted_huf or q.grand_total_huf
        if not total:
            continue
        area = q.area_sqm or REFERENCE_AREA_SQM
        if area <= 0:
            continue
        quote_year = q.quote_date.year if q.quote_date else 2024
        sim_weight = building_similarity_weight(apt_input, q)

        scope_line_total: Dict[str, int] = {}
        for li in q.line_items:
            if not li.total_cost_huf:
                continue
            for scope_name, info in SCOPE_CATEGORY_MAP.items():
                if li.category_key in info["keys"]:
                    scope_line_total[scope_name] = (
                        scope_line_total.get(scope_name, 0) + li.total_cost_huf
                    )
                    scope_line_items[scope_name] += 1
                    break

        f_blended = inflate_quote_value(1.0, quote_year, target_date, price_index)
        infl_factors.append(f_blended)
        for scope_name, cost_total in scope_line_total.items():
            scope_entries[scope_name].append((
                inflate_quote_value(cost_total / area, quote_year, target_date, price_index),
                quote_year,
                sim_weight,
            ))

    # 3 — Average per-sqm cost per scope using IVW × recency × building-similarity weighting.
    # IVW: weight_i = 1 / ((x_i - mean)^2 + eps)  — downweights outliers
    # Recency: boost_year = most recent year in this scope's data;
    #   quotes from boost_year get 2× weight, others 1×
    # Similarity: type/era match vs the request → same-type-and-close-era quotes
    #   get full weight, distant matches down-weighted (never excluded).
    # Combined: combined_i = IVW_i × recency_i × similarity_i
    # Weighted mean: Σ(combined_i · x_i) / Σ(combined_i)
    # Edge case: if all quotes same year, recency is uniform (all 2×) →
    #   mathematically identical to pure IVW (common factor cancels).
    _IVW_EPS = 1.0
    scope_avg_per_sqm: Dict[str, float] = {}
    scope_distinct_quotes: Dict[str, int] = {}
    scope_boost_year: Dict[str, Optional[int]] = {}
    scope_combined_weights: Dict[str, List[float]] = {}
    scope_similarity_weights: Dict[str, List[float]] = {}
    scope_fallback: Dict[str, bool] = {}
    for scope_name in SCOPE_NAMES_ORDERED:
        entries = scope_entries[scope_name]
        scope_distinct_quotes[scope_name] = len(entries)
        vals = np.array([e[0] for e in entries], dtype=float)
        years = [e[1] for e in entries]
        sims = [e[2] for e in entries]
        scope_boost_year[scope_name] = None

        if len(vals) >= 2:
            mean = vals.mean()
            variances = (vals - mean) ** 2 + _IVW_EPS
            ivw_weights = 1.0 / variances

            boost_year = max(years)
            scope_boost_year[scope_name] = boost_year
            recency_weights = np.array(
                [2.0 if y == boost_year else 1.0 for y in years], dtype=float
            )
            similarity_weights = np.array(sims, dtype=float)
            combined = ivw_weights * recency_weights * similarity_weights
            scope_combined_weights[scope_name] = list(float(w) for w in combined)
            scope_similarity_weights[scope_name] = list(float(w) for w in similarity_weights)
            scope_avg_per_sqm[scope_name] = _weighted_mean(vals, combined)
            scope_fallback[scope_name] = False
        elif len(vals) == 1:
            scope_boost_year[scope_name] = entries[0][1]
            scope_combined_weights[scope_name] = [1.0]
            scope_similarity_weights[scope_name] = [float(entries[0][2])]
            scope_avg_per_sqm[scope_name] = float(vals[0])
            scope_fallback[scope_name] = True
        else:
            scope_avg_per_sqm[scope_name] = (
                SCOPE_CATEGORY_MAP[scope_name]["min_premium"] / REFERENCE_AREA_SQM
            )
            scope_combined_weights[scope_name] = []
            scope_similarity_weights[scope_name] = []
            scope_fallback[scope_name] = True
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

    # 5 — Compute inflation factor range from per-quote blended factors
    # (inflation is already resolved in per-sqm values above; these stats are
    # for EstimationTrace/debugging visibility only).
    infl_factor_stats: Dict[str, float] = {}
    if infl_factors:
        arr = np.array(infl_factors, dtype=float)
        infl_factor_stats = {
            "min": round(float(arr.min()), 4),
            "max": round(float(arr.max()), 4),
            "mean": round(float(arr.mean()), 4),
            "count": len(infl_factors),
        }

    # 6 — Build per-category breakdown (Item D) when requested
    categories: Dict[str, Any] = {}
    if include_breakdown:
        for scope_name in SCOPE_NAMES_ORDERED:
            _entries = scope_entries[scope_name]
            raw_vals = [float(e[0]) for e in _entries]
            raw_years = [e[1] for e in _entries]
            dq = scope_distinct_quotes[scope_name]
            if dq >= 2:
                dq_label = "sufficient"
            elif dq == 1:
                dq_label = "single_quote"
            else:
                dq_label = "no_corpus_data"

            scope_est_mid = int(scope_avg_per_sqm[scope_name] * query_area)
            categories[scope_name] = {
                "scope": scope_name,
                "label_hu": SCOPE_CATEGORY_MAP[scope_name]["label_hu"],
                "label_en": SCOPE_CATEGORY_MAP[scope_name]["label_en"],
                "distinct_quote_count": dq,
                "line_item_count": scope_line_items[scope_name],
                "area_sqm": query_area,
                "avg_per_sqm_huf": round(scope_avg_per_sqm[scope_name], 0),
                "raw_per_sqm_huf": raw_vals,
                "combined_weights": scope_combined_weights[scope_name],
                "similarity_weights": scope_similarity_weights[scope_name],
                "min_per_sqm_huf": round(float(min(raw_vals)), 0) if raw_vals else 0,
                "max_per_sqm_huf": round(float(max(raw_vals)), 0) if raw_vals else 0,
                "data_quality": dq_label,
                "fallback_used": scope_fallback[scope_name],
                "boost_year": scope_boost_year[scope_name],
                "estimate_huf": {
                    "low": int(scope_est_mid * 0.85),
                    "mid": scope_est_mid,
                    "high": int(scope_est_mid * 1.20),
                },
            }

    # 7 — Return predict()-compatible dict (estimates already in target-date HUF)
    result = {
        "estimate_low_huf": int(estimate_mid * 0.85),
        "estimate_mid_huf": estimate_mid,
        "estimate_high_huf": int(estimate_mid * 1.20),
        "inflation_adjusted_to": target_date.isoformat(),
        "inflation_factor_range": infl_factor_stats,
        "model_used": "scope_matched_per_sqm",
        "warning": monotonicity_warning,
        "debug": {
            "num_queries_in_db": len(quotes),
            "num_user_scopes": num_user_scopes,
            "contingency_huf": CONTINGENCY,
            "query_area_sqm": query_area,
            "scope_avg_per_sqm_huf": {k: round(v, 0) for k, v in scope_avg_per_sqm.items()},
            "scope_distinct_quote_count": scope_distinct_quotes,
            "scope_line_item_count": scope_line_items,
            "scope_similarity_weight_min": round(min(
                (w for ws in scope_similarity_weights.values() for w in ws), default=1.0
            ), 3),
            "scope_total_per_sqm_huf": round(scope_total_per_sqm, 0),
            "price_per_sqm_huf": round((CONTINGENCY + scope_total_per_sqm * query_area) / query_area, 0) if query_area > 0 else 0,
        },
    }
    if include_breakdown:
        result["categories"] = categories
    return result


def find_similar_quotes(
    features: QuoteFeatures,
    quotes_dir: Path,
    top_k: int = 3,
) -> List[dict]:
    """Finds top_k similar historical quotes using Euclidean distance on numeric features."""
    all_feats = []

    num_cols = [
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

    # Inflation — deprecated code path has no per-quote year; use today as baseline
    # (effectively no-op: this function is deprecated in favour of scope_matched_estimate())
    baseline_date = date.today()
    f_labor = compound_inflation_factor(baseline_date.year, target_date, price_index, "labor")
    f_material = compound_inflation_factor(baseline_date.year, target_date, price_index, "materials")
    labor_share = 0.55
    total_adj = int(total * (labor_share * f_labor + (1.0 - labor_share) * f_material))

    return {
        "estimate_low_huf": int(total_adj * 0.85),
        "estimate_mid_huf": total_adj,
        "estimate_high_huf": int(total_adj * 1.20),
        "inflation_adjusted_to": target_date.isoformat(),
        "inflation_factor_range": {
            "min": round(min(f_labor, f_material), 4),
            "max": round(max(f_labor, f_material), 4),
            "mean": round((f_labor + f_material) / 2, 4),
        },
        "model_used": "cost_breakdown",
        "warning": "predict() is deprecated; use scope_matched_estimate() for DB-backed estimates.",
    }
