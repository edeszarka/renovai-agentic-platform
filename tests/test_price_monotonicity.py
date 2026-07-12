"""
Regression test for price-per-m² monotonicity.

Tests that for a fixed scope, estimates are strictly non-decreasing
as area increases, and that price-per-m² stays within a plausible band.
This catches the class of bug where sparse data lets a poorly-representative
small-flat quote outweigh area entirely (same root cause as the earlier
"adding scope could decrease cost" bug — structural constraint missing).
"""
import os
import sys
from datetime import date
from pathlib import Path

import pytest

from renovai.predictor.feature_extractor import ApartmentInput
from renovai.predictor.price_model import scope_matched_estimate
from renovai.ingestion.inflation_calc import load_price_index
from renovai.db.session import get_engine, get_session_maker

DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")
DATA_ROOT = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def session_maker():
    engine = get_engine(DB_URL)
    return get_session_maker(engine)


@pytest.fixture(scope="module")
def price_index():
    mat_path = DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv"
    lab_path = DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv"
    return load_price_index(mat_path, lab_path)


TARGET_DATE = date(2026, 7, 10)

FULL_SCOPE = dict(
    district=5,
    needs_plumbing=True,
    needs_electrical=True,
    needs_flooring=True,
    needs_full_demolition=True,
)

AREA_CASES = [(40, 1), (49, 2), (60, 2), (75, 3), (99, 4), (120, 4)]


@pytest.mark.asyncio
async def test_price_monotonicity(session_maker, price_index):
    """For a fixed scope (full renovation), price must be non-decreasing
    as area increases."""
    prev_estimate = 0
    prev_area = 0
    results = []

    for area, rooms in AREA_CASES:
        apt = ApartmentInput(
            total_area_sqm=float(area),
            num_rooms=rooms,
            **FULL_SCOPE,
        )
        est = await scope_matched_estimate(apt, session_maker, price_index, TARGET_DATE)
        assert est is not None
        mid = est["estimate_mid_huf"]
        ppsqm = est["debug"]["price_per_sqm_huf"]
        results.append((area, mid, ppsqm, est.get("warning")))

    areas = [r[0] for r in results]
    estimates = [r[1] for r in results]

    # 1. Strictly non-decreasing as area increases
    for i in range(1, len(estimates)):
        assert estimates[i] >= estimates[i - 1], (
            f"Monotonicity VIOLATION: {areas[i]}m2 ({estimates[i]:,}) < "
            f"{areas[i-1]}m2 ({estimates[i-1]:,})"
        )

    # 2. Price-per-m2 within plausible band (10k-600k HUF/m2)
    ppsqms = [r[2] for r in results]
    for area, ppsqm in zip(areas, ppsqms):
        assert 10_000 <= ppsqm <= 600_000, (
            f"Price-per-m2 {ppsqm:,} for {area}m2 outside band [10k, 600k]"
        )

    # 3. Price-per-m2 should be roughly stable (+-50% of median)
    median_ppsqm = sorted(ppsqms)[len(ppsqms) // 2]
    for area, ppsqm in zip(areas, ppsqms):
        ratio = ppsqm / median_ppsqm
        assert 0.5 <= ratio <= 2.0, (
            f"Price-per-m2 for {area}m2 ({ppsqm:,}) deviates too much from "
            f"median {median_ppsqm:,} (ratio={ratio:.2f})"
        )


@pytest.mark.asyncio
async def test_49_vs_99_comparison(session_maker, price_index):
    """Specifically compare the 49m2/2-room vs 99m2/4-room case.
    The old bug produced a ratio near 1.0x (only ~1M difference).
    With per-m2 normalization, cost should scale with area."""
    apt_49 = ApartmentInput(total_area_sqm=49, num_rooms=2, **FULL_SCOPE)
    apt_99 = ApartmentInput(total_area_sqm=99, num_rooms=4, **FULL_SCOPE)

    est_49 = await scope_matched_estimate(apt_49, session_maker, price_index, TARGET_DATE)
    est_99 = await scope_matched_estimate(apt_99, session_maker, price_index, TARGET_DATE)

    assert est_49 is not None
    assert est_99 is not None

    m49 = est_49["estimate_mid_huf"]
    m99 = est_99["estimate_mid_huf"]

    ratio = m99 / m49
    area_ratio = 99 / 49

    # Monotonic: larger area must cost more
    assert m99 > m49, f"99m2 ({m99:,}) should cost more than 49m2 ({m49:,})"

    # Cost ratio must be significant (>1.3x)
    assert ratio > 1.3, (
        f"Cost ratio {ratio:.2f}x is too close to 1.0 -- "
        f"area is not driving cost correctly"
    )
