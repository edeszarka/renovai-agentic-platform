"""Tests for the rule-based estimate audit report.

Fully synthetic and deterministic: no network, no LLM, no local corpus. They
cover the required areas:

* category classification (in-scope-no-data vs. not-in-scope),
* deviation-flag thresholds just inside/outside the -5%/+10% boundaries,
* the leak guard (held-out quote id/price must not reach the estimator),
* report rendering and machine-readable output.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

import pytest

from renovai.db.models import Base, BuildingType, LineItemORM, Quote
from renovai.db.session import get_engine, get_session_maker
from renovai.ingestion.inflation_models import CPIRecord, PriceIndex
from renovai.predictor.feature_extractor import ApartmentInput
from scripts.estimate_audit import (
    FALLBACK_NOTE_FLOOR,
    FALLBACK_NOTE_SINGLE,
    FLAG_OVER,
    FLAG_UNDER,
    NO_FLAG,
    REASON_IN_SCOPE_NO_DATA,
    REASON_NOT_IN_SCOPE,
    _build_coverage_gaps,
    ascii_key,
    classify_deviation,
    fallback_flag_note,
    group_quote_categories,
    render_report,
    run_audit,
    select_default_apartment,
    write_machine_readable,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _price_index() -> PriceIndex:
    records = [
        CPIRecord(year=year, quarter=quarter, index_value=100.0 + year * 10 + quarter)
        for year in range(2022, 2027)
        for quarter in range(1, 5)
    ]
    return PriceIndex(
        materials=list(records),
        labor=list(records),
        generated_at=datetime(2026, 1, 1),
    )


def _item(
    legacy: Optional[str],
    v2: Optional[str],
    cost: int,
    labor: Optional[int] = None,
    material: Optional[int] = None,
) -> LineItemORM:
    return LineItemORM(
        name_hu=f"item-{legacy}-{v2}",
        category_key=legacy,
        category_key_v2=v2,
        total_cost_huf=cost,
        labor_cost_huf=labor,
        material_cost_huf=material,
        section="main",
        version=1,
    )


def _quote(
    items: List[LineItemORM],
    *,
    file_name: str = "x.xlsx",
    completeness: Optional[str] = "komplett",
    area: Optional[float] = 50.0,
    building_type: Optional[BuildingType] = BuildingType.TEGLA,
    era: Optional[int] = 1980,
    quote_date: Optional[date] = date(2024, 1, 1),
    qid: Optional[UUID] = None,
) -> Quote:
    quote = Quote(
        file_name=file_name,
        district=5,
        quote_date=quote_date,
        total_labor_huf=0,
        total_material_huf=0,
        grand_total_huf=1_000_000,
        grand_total_adjusted_huf=1_000_000,
        building_type=building_type,
        building_era=era,
        area_sqm=area,
        renovation_completeness=completeness,
        num_line_items=len(items),
    )
    if qid is not None:
        quote.id = qid
    quote.line_items = items
    return quote


# ---------------------------------------------------------------------------
# Deviation thresholds
# ---------------------------------------------------------------------------


class TestClassifyDeviation:
    @pytest.mark.parametrize(
        ("deviation", "expected"),
        [
            (-100.0, FLAG_UNDER),
            (-5.001, FLAG_UNDER),
            (-5.0, NO_FLAG),
            (-4.999, NO_FLAG),
            (0.0, NO_FLAG),
            (9.999, NO_FLAG),
            (10.0, NO_FLAG),
            (10.001, FLAG_OVER),
            (150.0, FLAG_OVER),
        ],
    )
    def test_boundaries(self, deviation, expected):
        assert classify_deviation(deviation) == expected

    def test_flag_values_are_the_documented_hungarian_tokens(self):
        assert FLAG_UNDER == "ALULBECSÜLT"
        assert FLAG_OVER == "TÚLBECSÜLT"


class TestFallbackNotes:
    def test_no_corpus_data_gets_floor_note(self):
        assert fallback_flag_note("no_corpus_data", True) == FALLBACK_NOTE_FLOOR

    def test_single_quote_gets_weak_corpus_note(self):
        assert fallback_flag_note("single_quote", True) == FALLBACK_NOTE_SINGLE

    def test_sufficient_is_silent(self):
        assert fallback_flag_note("sufficient", False) == ""


# ---------------------------------------------------------------------------
# Canonical grouping
# ---------------------------------------------------------------------------


class TestCanonicalGrouping:
    def test_ascii_key_folds_legacy_accents(self):
        assert ascii_key("víz_fűtés") == "viz_futes"
        assert ascii_key("egyéb_köműves") == "egyeb_komuves"
        assert ascii_key("glettelés_festés") == "gletteles_festes"

    def test_groups_by_v2_and_legacy_fallback(self):
        quote = _quote(
            [
                _item("víz_fűtés", "viz_futes", 1000, labor=600, material=400),
                _item("víz_fűtés", "viz_futes", 500, labor=500),
                _item("vakolás", "vakolas", 300),  # combined, no split fields
                _item("egyéb", None, 0),  # zero cost is ignored
            ]
        )
        groups = group_quote_categories(quote)
        assert groups["viz_futes"].total_huf == 1500
        assert groups["viz_futes"].item_count == 2
        assert groups["viz_futes"].cost_tracking == "split"
        assert groups["vakolas"].cost_tracking == "combined"
        assert "egyeb" not in groups

    def test_null_v2_falls_back_to_legacy_for_grouping(self):
        quote = _quote([_item("szigetelés", None, 700)])
        groups = group_quote_categories(quote)
        assert groups["szigeteles"].total_huf == 700

    def test_mixed_tracking(self):
        quote = _quote(
            [
                _item("burkolás", "burkolas", 100, labor=100),
                _item("burkolás", "burkolas", 200),
            ]
        )
        assert group_quote_categories(quote)["burkolas"].cost_tracking == "mixed"


# ---------------------------------------------------------------------------
# Category coverage classification
# ---------------------------------------------------------------------------


class TestCoverageClassification:
    def _groups(self):
        quote = _quote(
            [
                _item("víz_fűtés", "viz_futes", 1000),
                _item("klíma", "klima", 500),
                _item("vakolás", "vakolas", 300),
            ]
        )
        return group_quote_categories(quote)

    def test_in_scope_without_corpus_data_and_not_in_scope(self):
        groups = self._groups()
        apt = ApartmentInput(
            district=5,
            total_area_sqm=50.0,
            num_rooms=0,
            building_type="tegla",
            building_era=1980,
            needs_plumbing=True,
            needs_ac=True,
        )
        estimate = {
            "categories": {
                "needs_plumbing": {"data_quality": "sufficient"},
                "needs_ac": {"data_quality": "no_corpus_data"},
            }
        }
        gaps = _build_coverage_gaps(groups, estimate, apt)
        reasons = {gap.category: gap.reason for gap in gaps}

        # Unscoped category -> structural gap.
        assert reasons["vakolas"] == REASON_NOT_IN_SCOPE
        # Scoped, selected, but no comparable corpus data -> coverage gap.
        assert reasons["klima"] == REASON_IN_SCOPE_NO_DATA
        # Scoped and corpus-backed -> not missing, omitted from this section.
        assert "viz_futes" not in reasons
        # Exactly the two documented reasons are ever produced.
        assert set(reasons.values()) <= {
            REASON_NOT_IN_SCOPE,
            REASON_IN_SCOPE_NO_DATA,
        }

    def test_unselected_scope_is_treated_as_missing(self):
        groups = self._groups()
        apt = ApartmentInput(
            district=5,
            total_area_sqm=50.0,
            num_rooms=0,
            building_type="tegla",
            building_era=1980,
            needs_plumbing=True,
            needs_ac=False,  # not selected despite real kliml items
        )
        estimate = {"categories": {"needs_ac": {"data_quality": "sufficient"}}}
        gaps = _build_coverage_gaps(groups, estimate, apt)
        assert {g.category: g.reason for g in gaps}["klima"] == (
            REASON_IN_SCOPE_NO_DATA
        )


# ---------------------------------------------------------------------------
# Default apartment selection
# ---------------------------------------------------------------------------


class TestDefaultSelection:
    def test_picks_first_usable_komplett_quote_by_id(self):
        unusable_id = UUID("00000000-0000-0000-0000-000000000001")
        partial_id = UUID("00000000-0000-0000-0000-000000000002")
        usable_id = UUID("00000000-0000-0000-0000-000000000003")
        unusable = _quote(
            [_item("víz_fűtés", "viz_futes", 1)],
            completeness="komplett",
            area=None,
            qid=unusable_id,
        )
        partial = _quote(
            [_item("víz_fűtés", "viz_futes", 1)],
            completeness="reszleges",
            qid=partial_id,
        )
        usable = _quote(
            [_item("víz_fűtés", "viz_futes", 1)],
            completeness="komplett",
            qid=usable_id,
        )
        picked = select_default_apartment([partial, usable, unusable])
        assert picked is usable

    def test_returns_none_when_no_usable_komplett_quote(self):
        partial = _quote([_item("víz_fűtés", "viz_futes", 1)], completeness="reszleges")
        assert select_default_apartment([partial]) is None


# ---------------------------------------------------------------------------
# Full audit run against a synthetic file DB (leak guard)
# ---------------------------------------------------------------------------

A_ID = UUID("00000000-0000-0000-0000-000000000001")
B_ID = UUID("00000000-0000-0000-0000-000000000002")
C_ID = UUID("00000000-0000-0000-0000-000000000003")

QUOTES: List[Dict[str, Any]] = [
    {
        "name": "a.xlsx",
        "id": A_ID,
        "year": 2024,
        "area": 50.0,
        "type": BuildingType.TEGLA,
        "era": 1980,
        "completeness": "komplett",
        "items": [
            ("víz_fűtés", "viz_futes", 1_000_000, 700_000, 300_000),
            ("klíma", "klima", 150_000, 150_000, None),
            ("vakolás", "vakolas", 200_000, 120_000, 80_000),
        ],
    },
    {
        "name": "b.xlsx",
        "id": B_ID,
        "year": 2024,
        "area": 60.0,
        "type": BuildingType.PANEL,
        "era": 1975,
        "completeness": "reszleges",
        "items": [
            ("víz_fűtés", "viz_futes", 1_200_000, None, None),
            ("bontás", "bontas", 300_000, None, None),
        ],
    },
    {
        "name": "c.xlsx",
        "id": C_ID,
        "year": 2025,
        "area": 55.0,
        "type": BuildingType.TEGLA,
        "era": 1970,
        "completeness": "komplett",
        "items": [
            ("villany", "villany", 700_000, None, None),
            ("parketta", "parketta", 500_000, None, None),
        ],
    },
]

TOTAL_QUOTES = len(QUOTES)


async def _seed_file_db(database_url: str) -> None:
    engine = get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = get_session_maker(engine)
    async with session_maker() as session:
        for spec in QUOTES:
            quote = Quote(
                id=spec["id"],
                file_name=spec["name"],
                district=5,
                quote_date=date(spec["year"], 1, 1),
                total_labor_huf=0,
                total_material_huf=0,
                grand_total_huf=1_000_000,
                grand_total_adjusted_huf=1_000_000,
                building_type=spec["type"],
                building_era=spec["era"],
                area_sqm=spec["area"],
                renovation_completeness=spec["completeness"],
                num_line_items=len(spec["items"]),
            )
            quote.line_items = [
                _item(legacy, v2, cost, labor, material)
                for legacy, v2, cost, labor, material in spec["items"]
            ]
            session.add(quote)
        await session.commit()
    await engine.dispose()


@pytest.fixture
def file_db(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'corpus.db').as_posix()}"


class TestRunAudit:
    @pytest.mark.asyncio
    async def test_leak_guard_and_coverage(self, file_db):
        await _seed_file_db(file_db)
        result = await run_audit(file_db, _price_index(), quote_id=str(A_ID))

        assert result.quote_id == str(A_ID)
        # The held-out quote is physically absent from the training pool...
        assert str(A_ID) not in result.training_quote_ids
        # ...while every other quote remains available.
        assert len(result.training_quote_ids) == TOTAL_QUOTES - 1

        # No price/id can reach the estimator through the descriptive input.
        dumped = result.apartment_input
        assert "id" not in dumped
        assert not any(
            "price" in key or "cost" in key or "huf" in key for key in dumped
        )

        # Only one non-held-out quote has plumbing -> the held-out price did
        # not leak in to prop the count up.
        plumbing = next(
            s for s in result.scope_estimates if s.scope == "needs_plumbing"
        )
        assert plumbing.data_quality == "single_quote"

        reasons = {gap.category: gap.reason for gap in result.coverage_gaps}
        assert reasons["vakolas"] == REASON_NOT_IN_SCOPE
        assert reasons["klima"] == REASON_IN_SCOPE_NO_DATA
        assert "viz_futes" not in reasons

        ac = next(c for c in result.categories if c.category == "needs_ac")
        assert ac.estimate_huf is not None
        assert ac.flag_note == FALLBACK_NOTE_FLOOR

    @pytest.mark.asyncio
    async def test_default_pick_is_first_komplett_by_id(self, file_db):
        await _seed_file_db(file_db)
        result = await run_audit(file_db, _price_index())
        assert result.quote_id == str(A_ID)

    @pytest.mark.asyncio
    async def test_explicit_quote_selection(self, file_db):
        await _seed_file_db(file_db)
        result = await run_audit(file_db, _price_index(), quote_id=str(C_ID))
        assert result.quote_id == str(C_ID)
        assert result.file_name == "c.xlsx"

    @pytest.mark.asyncio
    async def test_unknown_quote_id_raises(self, file_db):
        await _seed_file_db(file_db)
        with pytest.raises(ValueError):
            await run_audit(
                file_db,
                _price_index(),
                quote_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
            )

    @pytest.mark.asyncio
    async def test_render_and_machine_readable(self, file_db, tmp_path):
        await _seed_file_db(file_db)
        result = await run_audit(file_db, _price_index(), quote_id=str(A_ID))

        report = render_report(result)
        assert report.startswith("# Estimate audit")
        assert "Demo" in report or "Demonstration apartment" in report
        assert REASON_NOT_IN_SCOPE in report
        assert REASON_IN_SCOPE_NO_DATA in report
        assert "TÚLBECSÜLT" in report
        assert FALLBACK_NOTE_FLOOR in report
        assert "Category coverage" in report

        csv_path = tmp_path / "audit.csv"
        json_path = tmp_path / "audit.json"
        write_machine_readable(result, csv_path, json_path)

        assert csv_path.exists() and csv_path.read_text(encoding="utf-8")
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["quote_id"] == str(A_ID)
        assert payload["coverage_gaps"]
        assert any(row["category"] == "needs_ac" for row in payload["categories"])
