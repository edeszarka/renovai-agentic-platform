import logging
import time
from typing import Any, Dict

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from .models import LineItemORM, Quote, WorkCategory

logger = logging.getLogger(__name__)

_stats_cache: Dict[str, Any] = {"timestamp": 0.0, "data": None}
CACHE_TTL = 300


async def compute_per_sqm_stats(session_maker: async_sessionmaker) -> dict:
    now = time.time()
    if _stats_cache["data"] and (now - _stats_cache["timestamp"]) < CACHE_TTL:
        return _stats_cache["data"]

    async with session_maker() as session:
        stmt = (
            select(Quote)
            .options(selectinload(Quote.line_items))
        )
        res = await session.execute(stmt)
        quotes = list(res.scalars().all())

        cat_res = await session.execute(select(WorkCategory))
        categories: Dict[str, dict] = {
            c.key: {"label_hu": c.label_hu, "label_en": c.label_en}
            for c in cat_res.scalars().all()
        }

    overall_vals: list[float] = []
    category_vals: dict[str, list[float]] = {}

    for q in quotes:
        area = q.area_sqm
        if not area or area <= 0:
            continue

        total = q.grand_total_adjusted_huf or q.grand_total_huf
        if total:
            overall_vals.append(total / area)

        seen_cats: set[str] = set()
        for li in q.line_items:
            if not li.total_cost_huf or not li.category_key:
                continue
            key = li.category_key
            if key not in seen_cats:
                category_vals.setdefault(key, []).append(li.total_cost_huf / area)
                seen_cats.add(key)

    overall = {
        "count": len(overall_vals),
        "avg": int(round(np.mean(overall_vals))) if overall_vals else 0,
        "median": int(round(np.median(overall_vals))) if overall_vals else 0,
        "min": int(round(min(overall_vals))) if overall_vals else 0,
        "max": int(round(max(overall_vals))) if overall_vals else 0,
    }

    by_category: dict[str, dict] = {}
    for key, vals in sorted(category_vals.items()):
        info = categories.get(key, {"label_hu": key, "label_en": key})
        by_category[key] = {
            "label_hu": info["label_hu"],
            "label_en": info["label_en"],
            "count": len(vals),
            "avg_per_sqm": int(round(np.mean(vals))),
            "median_per_sqm": int(round(np.median(vals))),
            "min_per_sqm": int(round(min(vals))),
            "max_per_sqm": int(round(max(vals))),
        }

    result = {
        "num_quotes": len(quotes),
        "overall_per_sqm": overall,
        "by_category": by_category,
    }

    _stats_cache["timestamp"] = now
    _stats_cache["data"] = result
    return result
