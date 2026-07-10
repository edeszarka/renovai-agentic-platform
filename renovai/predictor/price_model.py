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
}

SCOPE_NAMES_ORDERED = [
    "needs_plumbing",
    "needs_electrical",
    "needs_flooring",
    "needs_full_demolition",
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


async def scope_matched_estimate(
    apt_input: ApartmentInput,
    session_maker,
    price_index: PriceIndex,
    target_date: date,
) -> Optional[dict]:
    """Estimates renovation cost by summing average per-category costs for selected scopes.

    Uses line-item-level averages from the DB, grouped by scope category_key.
    Returns same dict shape as predict() — estimate_low/mid/high_huf + debug.
    Guarantees: more selected scopes → higher estimate.
    """
    if not session_maker:
        logger.error("scope_matched_estimate: no session_maker provided")
        return None

    # 1 — Load all quotes with line items
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

    # 2 — Aggregate line-item costs per scope category across all usable quotes
    scope_costs: Dict[str, List[int]] = {}
    for scope_name in SCOPE_NAMES_ORDERED:
        scope_costs[scope_name] = []

    for q in quotes:
        total = q.grand_total_adjusted_huf or q.grand_total_huf
        if not total:
            continue  # skip quotes with zero total
        # Classify each line item into a scope
        for li in q.line_items:
            if not li.total_cost_huf:
                continue
            for scope_name, info in SCOPE_CATEGORY_MAP.items():
                if li.category_key in info["keys"]:
                    scope_costs[scope_name].append(li.total_cost_huf)
                    break

    # 3 — Average cost per scope, plus a "base" (min expected cost / contingency)
    scope_avg: Dict[str, int] = {}
    scope_count: Dict[str, int] = {}
    for scope_name in SCOPE_NAMES_ORDERED:
        vals = scope_costs[scope_name]
        scope_count[scope_name] = len(vals)
        if len(vals) >= 2:
            scope_avg[scope_name] = int(round(np.mean(vals)))
        else:
            scope_avg[scope_name] = SCOPE_CATEGORY_MAP[scope_name]["min_premium"]

    # 4 — Total estimate = base contingency + sum of selected scope averages
    CONTINGENCY = 200_000  # base cost (scaffolding, permits, etc.)
    scope_total = sum(scope_avg[s] for s in user_scopes)
    estimate_mid = CONTINGENCY + scope_total

    # 5 — Area adjustment (linear scaling)
    area_factor = apt_input.total_area_sqm / REFERENCE_AREA_SQM
    estimate_mid = int(estimate_mid * area_factor)
    estimate_mid = max(estimate_mid, 500_000)

    # 6 — Inflation adjustment
    baseline_date = date(2024, 2, 15)
    f_labor = get_factor(price_index, "labor", baseline_date, target_date)
    f_material = get_factor(price_index, "materials", baseline_date, target_date)

    labor_share = 0.55
    m_base = estimate_mid * (1.0 - labor_share)
    l_base = estimate_mid * labor_share
    estimate_adj = int((l_base * f_labor) + (m_base * f_material))

    # 7 — Return predict()-compatible dict
    return {
        "estimate_low_huf": int(estimate_adj * 0.85),
        "estimate_mid_huf": estimate_adj,
        "estimate_high_huf": int(estimate_adj * 1.20),
        "inflation_adjusted_to": target_date.isoformat(),
        "inflation_factor_labor": round(f_labor, 3),
        "inflation_factor_materials": round(f_material, 3),
        "model_used": "scope_matched",
        "warning": None,
        "debug": {
            "num_queries_in_db": len(quotes),
            "num_user_scopes": num_user_scopes,
            "contingency_huf": CONTINGENCY,
            "area_factor": round(area_factor, 2),
            "scope_avg_cost_huf": scope_avg,
            "scope_line_item_count": scope_count,
            "scope_total_avg_huf": scope_total,
        },
    }


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
