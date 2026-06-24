import json
import logging
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Any

from .feature_extractor import CATEGORY_KEYWORDS
from ..ingestion.inflation_models import AdjustedQuote

logger = logging.getLogger(__name__)

CATEGORY_LABELS_HU = {
    "demolition": "Bontás",
    "plumbing_heating": "Víz és fűtés",
    "electrical": "Villanyszerelés",
    "plastering": "Vakolás",
    "tiling": "Burkolás",
    "painting_plastering": "Glettelés/festés",
    "flooring": "Parketta",
    "misc_masonry": "Egyéb kőműves",
    "logistics": "Szállítás/segédmunka",
}

CATEGORY_LABELS_EN = {
    "demolition": "Demolition",
    "plumbing_heating": "Plumbing & Heating",
    "electrical": "Electrical",
    "plastering": "Plastering",
    "tiling": "Tiling",
    "painting_plastering": "Painting & Plastering",
    "flooring": "Flooring",
    "misc_masonry": "Misc. Masonry",
    "logistics": "Logistics",
}


def load_cost_breakdown(quotes_dir: Path) -> List[Dict[str, Any]]:
    """Loads all adjusted quote files and aggregates costs by work category.

    Returns a list of dicts sorted by descending average cost:
      - category_key: internal key
      - category_hu: Hungarian label
      - category_en: English label
      - count: number of quotes containing this work type
      - avg_huf: average total cost in HUF (inflation-adjusted)
      - median_huf: median total cost
      - min_huf: minimum observed cost
      - max_huf: maximum observed cost
      - avg_per_sqm_huf: average cost per m² (across quotes that have this work)
    """
    categories: Dict[str, List[int]] = {k: [] for k in CATEGORY_KEYWORDS}

    for file in sorted(quotes_dir.glob("*_adjusted.json")):
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
            quote = AdjustedQuote.model_validate(data)
        except Exception as e:
            logger.warning("Skipping %s: %s", file.name, e)
            continue

        area = quote.original_metadata.grand_total or 1
        for adj_item in quote.line_items_adjusted:
            name_lower = adj_item.original.name.lower()
            cost = adj_item.total_cost_adjusted
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
