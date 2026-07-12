import json
import math
import os
import pytest
import asyncio
from datetime import date
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from renovai.db.models import Base
from renovai.ingestion.inflation_calc import load_price_index
from renovai.predictor.price_model import scope_matched_estimate
from renovai.predictor.feature_extractor import ApartmentInput

DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
GOLDEN_PATH = Path(__file__).resolve().parent / "golden_outputs.json"
TARGET_DATE = date(2026, 7, 12)
Tolerance = 0.10


def _load_golden():
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        return json.load(f)


def _make_session_maker():
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")
    engine = create_async_engine(db_url)
    return async_sessionmaker(engine)


def _make_price_index():
    mat = DATA_ROOT / "raw" / "inflation" / "materials_cpi.csv"
    lab = DATA_ROOT / "raw" / "inflation" / "labor_cpi.csv"
    return load_price_index(mat, lab)


FROZEN_CASES = [
    ("d1_40sqm_full",
     dict(district=1, total_area_sqm=40, num_rooms=1, building_era=1960,
          needs_plumbing=True, needs_electrical=True, needs_flooring=True, needs_full_demolition=True)),
    ("d1_60sqm_full",
     dict(district=1, total_area_sqm=60, num_rooms=2, building_era=1960,
          needs_plumbing=True, needs_electrical=True, needs_flooring=True, needs_full_demolition=True)),
    ("d1_80sqm_full",
     dict(district=1, total_area_sqm=80, num_rooms=3, building_era=1960,
          needs_plumbing=True, needs_electrical=True, needs_flooring=True, needs_full_demolition=True)),
    ("d1_100sqm_full",
     dict(district=1, total_area_sqm=100, num_rooms=4, building_era=1960,
          needs_plumbing=True, needs_electrical=True, needs_flooring=True, needs_full_demolition=True)),
    ("d1_55sqm_plumbing",
     dict(district=1, total_area_sqm=55, num_rooms=2, building_era=1985,
          needs_plumbing=True, needs_electrical=False, needs_flooring=False, needs_full_demolition=False)),
    ("d1_55sqm_flooring",
     dict(district=1, total_area_sqm=55, num_rooms=2, building_era=1985,
          needs_plumbing=False, needs_electrical=False, needs_flooring=True, needs_full_demolition=False)),
    ("d1_55sqm_electrical",
     dict(district=1, total_area_sqm=55, num_rooms=2, building_era=1985,
          needs_plumbing=False, needs_electrical=True, needs_flooring=False, needs_full_demolition=False)),
    ("d1_35sqm_full_tall",
     dict(district=1, total_area_sqm=35, num_rooms=1, building_era=1950,
          needs_plumbing=True, needs_electrical=True, needs_flooring=True, needs_full_demolition=True)),
    ("d11_70sqm_full",
     dict(district=11, total_area_sqm=70, num_rooms=3, building_era=1975,
          needs_plumbing=True, needs_electrical=True, needs_flooring=True, needs_full_demolition=True)),
    ("d9_90sqm_full_noelev",
     dict(district=9, total_area_sqm=90, num_rooms=3, building_era=1960,
          needs_plumbing=True, needs_electrical=True, needs_flooring=True, needs_full_demolition=True)),
]


@pytest.fixture(scope="module")
def session_maker():
    return _make_session_maker()


@pytest.fixture(scope="module")
def price_index():
    return _make_price_index()


@pytest.fixture(scope="module")
def golden():
    return _load_golden()


class TestFrozenCases:
    @pytest.mark.parametrize("case_id,kwargs", FROZEN_CASES, ids=[c[0] for c in FROZEN_CASES])
    @pytest.mark.asyncio
    async def test_frozen_output_within_bounds(self, case_id, kwargs, session_maker, price_index, golden):
        apt = ApartmentInput(**kwargs)
        result = await scope_matched_estimate(apt, session_maker, price_index, TARGET_DATE)
        assert result is not None, f"scope_matched_estimate returned None for {case_id}"

        expected = golden["price_model"][case_id]
        for key in ("low", "mid", "high"):
            actual = result[f"estimate_{key}_huf"]
            exp = expected[key]
            lo = exp * (1 - Tolerance)
            hi = exp * (1 + Tolerance)
            assert lo <= actual <= hi, (
                f"{case_id} {key}: {actual:,} outside [{lo:,.0f}, {hi:,.0f}] "
                f"(golden={exp:,})"
            )

    @pytest.mark.asyncio
    async def test_geometric_mean_property(self, session_maker, price_index, golden):
        case = FROZEN_CASES[0]
        apt = ApartmentInput(**case[1])
        result = await scope_matched_estimate(apt, session_maker, price_index, TARGET_DATE)
        low = result["estimate_low_huf"]
        mid = result["estimate_mid_huf"]
        high = result["estimate_high_huf"]
        geo_mid = math.sqrt(low * high)
        ratio = mid / geo_mid
        assert 0.90 < ratio < 1.10, (
            f"geometric mean violated: mid={mid:,} vs sqrt(low*high)={geo_mid:,.0f} "
            f"(ratio={ratio:.4f})"
        )


class TestGoldenRegression:
    def test_golden_file_loads(self):
        g = _load_golden()
        assert "price_model" in g
        assert len(g["price_model"]) == 10

    @pytest.mark.asyncio
    async def test_no_value_drifts_beyond_threshold(self, session_maker, price_index, golden):
        threshold = golden["_meta"]["regression_threshold_pct"] / 100.0
        regressions = []
        for case_id, kwargs in FROZEN_CASES:
            apt = ApartmentInput(**kwargs)
            result = await scope_matched_estimate(apt, session_maker, price_index, TARGET_DATE)
            expected = golden["price_model"][case_id]
            for key in ("low", "mid", "high"):
                actual = result[f"estimate_{key}_huf"]
                exp = expected[key]
                drift = abs(actual - exp) / exp
                if drift > threshold:
                    regressions.append(
                        f"{case_id}.{key}: actual={actual:,} golden={exp:,} "
                        f"drift={drift*100:.2f}% > {threshold*100:.0f}%"
                    )
        assert not regressions, "Regressions detected:\n" + "\n".join(regressions)

    @pytest.mark.asyncio
    async def test_low_less_than_mid_less_than_high(self, session_maker, price_index):
        for case_id, kwargs in FROZEN_CASES:
            apt = ApartmentInput(**kwargs)
            result = await scope_matched_estimate(apt, session_maker, price_index, TARGET_DATE)
            low = result["estimate_low_huf"]
            mid = result["estimate_mid_huf"]
            high = result["estimate_high_huf"]
            assert low < mid < high, (
                f"{case_id}: order violated low={low:,} mid={mid:,} high={high:,}"
            )

    @pytest.mark.asyncio
    async def test_all_values_positive(self, session_maker, price_index):
        for case_id, kwargs in FROZEN_CASES:
            apt = ApartmentInput(**kwargs)
            result = await scope_matched_estimate(apt, session_maker, price_index, TARGET_DATE)
            for key in ("low", "mid", "high"):
                val = result[f"estimate_{key}_huf"]
                assert val > 0, f"{case_id} {key}={val} is not positive"

    @pytest.mark.asyncio
    async def test_inflation_factors_applied(self, session_maker, price_index):
        apt = ApartmentInput(**FROZEN_CASES[0][1])
        result = await scope_matched_estimate(apt, session_maker, price_index, TARGET_DATE)
        f_labor = result.get("inflation_factor_labor")
        f_mat = result.get("inflation_factor_materials")
        assert f_labor is not None and f_labor > 0, f"bad labor factor: {f_labor}"
        assert f_mat is not None and f_mat > 0, f"bad material factor: {f_mat}"


class TestCrossPathConsistency:
    @pytest.mark.asyncio
    async def test_full_scope_beats_plumbing_only(self, session_maker, price_index):
        full = await scope_matched_estimate(
            ApartmentInput(district=1, total_area_sqm=55, num_rooms=2, building_era=1985,
                           needs_plumbing=True, needs_electrical=True, needs_flooring=True,
                           needs_full_demolition=True),
            session_maker, price_index, TARGET_DATE,
        )
        plumb = await scope_matched_estimate(
            ApartmentInput(district=1, total_area_sqm=55, num_rooms=2, building_era=1985,
                           needs_plumbing=True, needs_electrical=False, needs_flooring=False,
                           needs_full_demolition=False),
            session_maker, price_index, TARGET_DATE,
        )
        assert full["estimate_mid_huf"] > plumb["estimate_mid_huf"]

    @pytest.mark.asyncio
    async def test_more_area_means_higher_cost(self, session_maker, price_index):
        areas = [40, 60, 80, 100]
        mids = []
        for area in areas:
            r = await scope_matched_estimate(
                ApartmentInput(district=1, total_area_sqm=area, num_rooms=2, building_era=1960,
                               needs_plumbing=True, needs_electrical=True, needs_flooring=True,
                               needs_full_demolition=True),
                session_maker, price_index, TARGET_DATE,
            )
            mids.append(r["estimate_mid_huf"])
        for i in range(len(areas) - 1):
            assert mids[i] < mids[i + 1], (
                f"area {areas[i]}sqm ({mids[i]:,}) should be < {areas[i+1]}sqm ({mids[i+1]:,})"
            )

    @pytest.mark.asyncio
    async def test_orchestrator_at_least_raw_estimate(self, session_maker, price_index):
        from renovai.predictor.structural_cost import (
            apply_height_surcharge, detect_active_chains, chain_total_cost,
            apply_infrastructure_minimums, compute_logistics_surcharge,
            elevator_surcharge,
        )

        apt = ApartmentInput(district=1, total_area_sqm=60, num_rooms=2, building_era=1960,
                             needs_plumbing=True, needs_electrical=True, needs_flooring=True,
                             needs_full_demolition=True)
        raw = await scope_matched_estimate(apt, session_maker, price_index, TARGET_DATE)
        b_low = raw["estimate_low_huf"]
        b_mid = raw["estimate_mid_huf"]
        b_high = raw["estimate_high_huf"]

        painting_share = int(b_mid * 0.15)
        painting_labor = int(painting_share * 0.70)
        _, hsc = apply_height_surcharge(2.75, 60, painting_labor)
        b_low += int(hsc * 0.85)
        b_mid += hsc
        b_high += int(hsc * 1.20)

        scope = {"plumbing": True, "electrical": True, "flooring": True, "demolition": True}
        ac = detect_active_chains(scope, "1960", 5, False)
        cc = chain_total_cost(ac, area_sqm=60)
        cd = cc["point"]
        if cd:
            b_low += int(cc["low"] * 0.85)
            b_mid += cd
            b_high += int(cc["high"] * 1.20)

        is_full = all(scope.get(k, False) for k in ("demolition", "electrical", "plumbing"))
        ml, mm, mh, _ = apply_infrastructure_minimums(b_low, b_mid, b_high, is_full, False)
        b_low, b_mid, b_high = ml, mm, mh

        tl = int(b_mid * 0.55)
        ls = int(compute_logistics_surcharge(tl))
        if ls:
            b_low += int(ls * 0.85)
            b_mid += ls
            b_high += int(ls * 1.20)

        f_labor = raw.get("inflation_factor_labor", 1.0)
        f_mat = raw.get("inflation_factor_materials", 1.0)
        combined = 0.55 * f_labor + 0.45 * f_mat
        b_low = int(b_low * combined)
        b_mid = int(b_mid * combined)
        b_high = int(b_high * combined)

        assert b_low >= raw["estimate_low_huf"] * combined
        assert b_mid >= raw["estimate_mid_huf"] * combined
        assert b_high >= raw["estimate_high_huf"] * combined

    @pytest.mark.asyncio
    async def test_single_scope_vs_multi_scope_ordering(self, session_maker, price_index):
        plumbing_only = await scope_matched_estimate(
            ApartmentInput(district=1, total_area_sqm=55, num_rooms=2, building_era=1985,
                           needs_plumbing=True, needs_electrical=False, needs_flooring=False,
                           needs_full_demolition=False),
            session_maker, price_index, TARGET_DATE,
        )
        electrical_only = await scope_matched_estimate(
            ApartmentInput(district=1, total_area_sqm=55, num_rooms=2, building_era=1985,
                           needs_plumbing=False, needs_electrical=True, needs_flooring=False,
                           needs_full_demolition=False),
            session_maker, price_index, TARGET_DATE,
        )
        both = await scope_matched_estimate(
            ApartmentInput(district=1, total_area_sqm=55, num_rooms=2, building_era=1985,
                           needs_plumbing=True, needs_electrical=True, needs_flooring=False,
                           needs_full_demolition=False),
            session_maker, price_index, TARGET_DATE,
        )
        assert both["estimate_mid_huf"] >= plumbing_only["estimate_mid_huf"]
        assert both["estimate_mid_huf"] >= electrical_only["estimate_mid_huf"]

    @pytest.mark.asyncio
    async def test_higher_district_multiplier(self, session_maker, price_index):
        d1 = await scope_matched_estimate(
            ApartmentInput(district=1, total_area_sqm=60, num_rooms=2, building_era=1960,
                           needs_plumbing=True, needs_electrical=True, needs_flooring=True,
                           needs_full_demolition=True),
            session_maker, price_index, TARGET_DATE,
        )
        d11 = await scope_matched_estimate(
            ApartmentInput(district=11, total_area_sqm=60, num_rooms=2, building_era=1960,
                           needs_plumbing=True, needs_electrical=True, needs_flooring=True,
                           needs_full_demolition=True),
            session_maker, price_index, TARGET_DATE,
        )
        assert d1["estimate_mid_huf"] >= d11["estimate_mid_huf"]
