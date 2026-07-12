"""One-shot script: run a real estimate and dump the trace entry to stdout.

Usage:
    python scripts/run_trace_demo.py
    python scripts/run_trace_demo.py --no-trace   # disable tracing
"""

import asyncio
import json
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")

from pathlib import Path
from datetime import date

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from renovai.db.models import Base
from renovai.ingestion.inflation_calc import load_price_index
from renovai.predictor.price_model import scope_matched_estimate
from renovai.predictor.feature_extractor import ApartmentInput
from renovai.predictor.estimation_trace import _LOG_FILE

DATA_ROOT = Path(__file__).resolve().parent.parent / "data"


async def main():
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")
    engine = create_async_engine(db_url)
    session_maker = async_sessionmaker(engine)

    mat = DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv"
    lab = DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv"
    price_index = load_price_index(mat, lab)

    target = date(2026, 7, 12)

    # Run 1: Klíma only (has real corpus data — 20 items)
    print("=" * 70)
    print("RUN 1: Klíma only (D1, 60sqm, era 1970)")
    print("=" * 70)
    apt1 = ApartmentInput(
        district=1, total_area_sqm=60, num_rooms=2, building_era=1970,
        needs_plumbing=False, needs_electrical=False, needs_flooring=False,
        needs_full_demolition=False, needs_ac=True,
    )
    r1 = await scope_matched_estimate(apt1, session_maker, price_index, target)
    print(f"  estimate_mid_huf = {r1['estimate_mid_huf']:,}")
    print(f"  trace written to: {_LOG_FILE}")

    # Run 2: Nyílászáró + Szigetelés (no corpus data — fallback)
    print()
    print("=" * 70)
    print("RUN 2: Nyílászáró + Szigetelés (D1, 60sqm, era 1970) — fallback")
    print("=" * 70)
    apt2 = ApartmentInput(
        district=1, total_area_sqm=60, num_rooms=2, building_era=1970,
        needs_plumbing=False, needs_electrical=False, needs_flooring=False,
        needs_full_demolition=False, needs_windows_doors=True, needs_insulation=True,
    )
    r2 = await scope_matched_estimate(apt2, session_maker, price_index, target)
    print(f"  estimate_mid_huf = {r2['estimate_mid_huf']:,}")
    print(f"  trace written to: {_LOG_FILE}")

    # Run 3: Full scope (D1, 80sqm, era 1960, high ceiling)
    print()
    print("=" * 70)
    print("RUN 3: Full scope (D1, 80sqm, era 1960, ceiling=3.2m)")
    print("=" * 70)
    apt3 = ApartmentInput(
        district=1, total_area_sqm=80, num_rooms=3, building_era=1960,
        needs_plumbing=True, needs_electrical=True, needs_flooring=True,
        needs_full_demolition=True, needs_ac=True,
    )
    r3 = await scope_matched_estimate(apt3, session_maker, price_index, target)
    print(f"  estimate_mid_huf = {r3['estimate_mid_huf']:,}")
    print(f"  trace written to: {_LOG_FILE}")

    # Dump the last trace entry
    print()
    print("=" * 70)
    print("LAST TRACE ENTRY (pretty-printed):")
    print("=" * 70)
    with open(_LOG_FILE, encoding="utf-8") as f:
        lines = f.readlines()
    last = json.loads(lines[-1])
    print(json.dumps(last, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
