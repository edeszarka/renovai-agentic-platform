"""
Thin wrapper around renovai.predictor.price_model.predict for the cost_estimate skill.

Called by the orchestrator when the buyer asks for a renovation budget estimate.
Reuses the same underlying implementation as mcp_server/server.py: estimate_renovation_cost.
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from dotenv import load_dotenv

load_dotenv()

from renovai.ingestion.inflation_calc import load_price_index
from renovai.predictor.feature_extractor import (
    ApartmentInput,
    apartment_input_to_features,
)
from renovai.predictor.price_model import find_similar_quotes, predict

# Map Hungarian scope keywords to apartment input flags
SCOPE_MAP = {
    "villany": {"needs_electrical": True},
    "viz": {"needs_plumbing": True},
    "viz_futes": {"needs_plumbing": True},
    "futes": {"needs_plumbing": True},
    "burkolas": {"needs_flooring": True},
    "bontas": {"needs_full_demolition": True},
    "furdo": {},
    "konyha": {},
    "nyilaszaro": {},
    "festes": {},
    "futes_rendszer": {"needs_plumbing": True},
    "szigeteles": {},
    "teljes": {
        "needs_plumbing": True,
        "needs_electrical": True,
        "needs_flooring": True,
        "needs_full_demolition": True,
    },
}


def run(district: int, area_sqm: float, num_rooms: int, scope: list[str]) -> dict:
    needs_plumbing = any(SCOPE_MAP.get(s, {}).get("needs_plumbing") for s in scope)
    needs_electrical = any(SCOPE_MAP.get(s, {}).get("needs_electrical") for s in scope)
    needs_flooring = any(SCOPE_MAP.get(s, {}).get("needs_flooring") for s in scope)
    needs_demo = any(SCOPE_MAP.get(s, {}).get("needs_full_demolition") for s in scope)

    inp = ApartmentInput(
        district=district,
        total_area_sqm=area_sqm,
        num_rooms=num_rooms,
        needs_plumbing=needs_plumbing,
        needs_electrical=needs_electrical,
        needs_flooring=needs_flooring,
        needs_full_demolition=needs_demo,
    )

    features = apartment_input_to_features(inp)
    target_date = date.today()

    price_index = load_price_index(
        Path("data/raw/inflation/materials_cpi.csv"),
        Path("data/raw/inflation/labor_cpi.csv"),
    )

    result = predict(
        features=features,
        price_index=price_index,
        target_date=target_date,
        model_dir=Path("data/models"),
    )

    similar = find_similar_quotes(features, Path("data/processed/quotes_json"), top_k=3)

    return {
        "low_huf": result["estimate_low_huf"],
        "mid_huf": result["estimate_mid_huf"],
        "high_huf": result["estimate_high_huf"],
        "inflation_adjusted_to": str(target_date),
        "similar_cases": similar,
        "num_similar_cases": len(similar),
        "warning": result.get("warning"),
    }


if __name__ == "__main__":
    data = json.loads(sys.stdin.read())
    result = run(
        district=data["district"],
        area_sqm=data["area_sqm"],
        num_rooms=data.get("num_rooms", 1),
        scope=data.get("scope", []),
    )
    print(json.dumps(result, ensure_ascii=False, default=str))
