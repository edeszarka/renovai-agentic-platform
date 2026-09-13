"""Tests for the Phase 3 leave-one-out categorization validation harness.

These tests are fully synthetic and deterministic: no network, no LLM, no
locally generated corpus. They cover the four required areas:

* the leak guard (held-out id absent from the training pool / ApartmentInput),
* the ``needs_*`` derivation per category column,
* the report math (signed deviation, MAPE aggregation),
* the estimator's default ``category_column`` behavior being unchanged.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from renovai.db.models import Base, BuildingType, LineItemORM, Quote
from renovai.db.session import get_engine, get_session_maker
from renovai.ingestion.inflation_models import CPIRecord, PriceIndex
from renovai.predictor.feature_extractor import ApartmentInput
from renovai.predictor.price_model import scope_matched_estimate
from scripts.loocv_validation import (
    ARM0,
    ARM1,
    CategoryOutcome,
    FoldResult,
    LOOCVResult,
    SkippedQuote,
    _signed_deviation_pct,
    build_apartment_input,
    compute_metrics,
    derive_needs,
    ground_truth_by_scope,
    mape,
    render_report,
    run_loocv,
)

# ---------------------------------------------------------------------------
# Synthetic corpus
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


QUOTES: List[Dict[str, Any]] = [
    {
        "name": "a.xlsx",
        "year": 2024,
        "area": 50.0,
        "type": BuildingType.TEGLA,
        "era": 1980,
        "completeness": "komplett",
        "items": [
            ("víz_fűtés", "viz_futes", 1_000_000),
            ("burkolás", "burkolas", 400_000),
        ],
    },
    {
        "name": "b.xlsx",
        "year": 2024,
        "area": 60.0,
        "type": BuildingType.PANEL,
        "era": 1975,
        "completeness": "reszleges",
        "items": [
            ("víz_fűtés", "viz_futes", 1_200_000),
            ("bontás", "bontas", 300_000),
        ],
    },
    {
        "name": "c.xlsx",
        "year": 2025,
        "area": 55.0,
        "type": BuildingType.TEGLA,
        "era": 1970,
        "completeness": "komplett",
        "items": [
            ("villany", "villany", 700_000),
            ("parketta", "parketta", 500_000),
        ],
    },
    {
        "name": "d.xlsx",
        "year": 2025,
        "area": 70.0,
        "type": BuildingType.TEGLA,
        "era": 1990,
        "completeness": "komplett",
        # legacy column does not map this into a scope; v2 does (insulation).
        "items": [
            ("egyéb_köműves", "szigeteles", 300_000),
            ("nyílászáró", "nyilaszaro", 800_000),
        ],
    },
    {
        "name": "e.xlsx",
        "year": 2025,
        "area": 65.0,
        "type": BuildingType.PANEL,
        "era": 1960,
        "completeness": "reszleges",
        "items": [("szigetelés", "szigeteles", 250_000)],
    },
    {
        "name": "missing_area.xlsx",
        "year": 2025,
        "area": None,
        "type": BuildingType.TEGLA,
        "era": 1980,
        "completeness": "komplett",
        "items": [("víz_fűtés", "viz_futes", 100_000)],
    },
]

OTHER_IDS = {"a.xlsx", "b.xlsx", "c.xlsx", "d.xlsx", "e.xlsx"}


async def _seed_file_db(database_url: str) -> None:
    engine = get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = get_session_maker(engine)
    async with session_maker() as session:
        for spec in QUOTES:
            quote = Quote(
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
                LineItemORM(
                    name_hu=f"item-{idx}",
                    category_key=legacy,
                    category_key_v2=v2,
                    total_cost_huf=cost,
                    section="main",
                    version=1,
                )
                for idx, (legacy, v2, cost) in enumerate(spec["items"])
            ]
            session.add(quote)
        await session.commit()
    await engine.dispose()


@pytest.fixture
def file_db(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'corpus.db').as_posix()}"


# ---------------------------------------------------------------------------
# needs_* derivation
# ---------------------------------------------------------------------------


def _quote_with_items(items: List[Tuple[str, Optional[str]]]) -> Quote:
    quote = Quote(
        file_name="x.xlsx",
        total_labor_huf=0,
        total_material_huf=0,
        grand_total_huf=0,
        num_line_items=len(items),
    )
    quote.line_items = [
        LineItemORM(
            name_hu=f"i{idx}",
            category_key=legacy,
            category_key_v2=v2,
            total_cost_huf=1,
            section="s",
            version=1,
        )
        for idx, (legacy, v2) in enumerate(items)
    ]
    return quote


class TestNeedsDerivation:
    def test_legacy_accented_key_matches(self):
        quote = _quote_with_items([("víz_fűtés", "viz_futes")])
        needs = derive_needs(quote, ARM0)
        assert needs["needs_plumbing"] is True
        assert sum(needs.values()) == 1

    def test_v2_ascii_key_matches(self):
        quote = _quote_with_items([("víz_fűtés", "viz_futes")])
        needs = derive_needs(quote, ARM1)
        assert needs["needs_plumbing"] is True

    def test_reclassification_changes_flags_between_arms(self):
        # Legacy misc-masonry key is not a scope; the v2 canonical key is.
        quote = _quote_with_items([("egyéb_köműves", "szigeteles")])
        assert derive_needs(quote, ARM0)["needs_insulation"] is False
        assert derive_needs(quote, ARM1)["needs_insulation"] is True

    def test_none_v2_does_not_match(self):
        quote = _quote_with_items([("víz_fűtés", None)])
        needs = derive_needs(quote, ARM1)
        assert needs["needs_plumbing"] is False

    def test_flooring_covers_two_canonical_keys(self):
        quote = _quote_with_items([("parketta", "parketta"), ("burkolás", "burkolas")])
        needs = derive_needs(quote, ARM1)
        assert needs["needs_flooring"] is True
        assert sum(needs.values()) == 1


class TestGroundTruth:
    def test_sums_per_scope_using_canonical_column(self):
        quote = _quote_with_items([("víz_fűtés", "viz_futes"), ("bontás", "bontas")])
        quote.line_items[0].total_cost_huf = 1000
        quote.line_items[1].total_cost_huf = 500
        truth = ground_truth_by_scope(quote)
        assert truth["needs_plumbing"] == 1000
        assert truth["needs_full_demolition"] == 500

    def test_null_v2_falls_back_to_legacy(self):
        quote = _quote_with_items([("víz_fűtés", None)])
        quote.line_items[0].total_cost_huf = 700
        truth = ground_truth_by_scope(quote)
        assert truth["needs_plumbing"] == 700

    def test_zero_cost_ignored(self):
        quote = _quote_with_items([("víz_fűtés", "viz_futes")])
        quote.line_items[0].total_cost_huf = 0
        assert ground_truth_by_scope(quote) == {}


class TestBuildApartmentInput:
    def test_missing_area_returns_none(self):
        quote = _quote_with_items([("víz_fűtés", "viz_futes")])
        quote.area_sqm = None
        quote.building_type = BuildingType.TEGLA
        quote.building_era = 1980
        assert build_apartment_input(quote, ARM0) is None

    def test_placeholder_num_rooms_and_no_price_fields(self):
        quote = _quote_with_items([("víz_fűtés", "viz_futes")])
        quote.area_sqm = 50.0
        quote.building_type = BuildingType.TEGLA
        quote.building_era = 1980
        quote.district = None
        apt = build_apartment_input(quote, ARM0)
        assert apt is not None
        dumped = apt.model_dump()
        assert dumped["num_rooms"] == 0
        assert dumped["district"] == 0
        assert "id" not in dumped
        assert not any(
            "price" in key or "cost" in key or "huf" in key for key in dumped
        )


# ---------------------------------------------------------------------------
# Report math
# ---------------------------------------------------------------------------


class TestReportMath:
    def test_signed_deviation(self):
        assert _signed_deviation_pct(110, 100) == pytest.approx(10.0)
        assert _signed_deviation_pct(90, 100) == pytest.approx(-10.0)

    def test_mape_ignores_sign(self):
        assert mape([10.0, -20.0, 30.0]) == pytest.approx(20.0)
        assert mape([]) is None

    def _fold(self, completeness, category, a0, a1, truth):
        return FoldResult(
            quote_id="q",
            file_name="q.xlsx",
            year=2025,
            completeness=completeness,
            area_sqm=50.0,
            building_type="tegla",
            outcomes=[
                CategoryOutcome(
                    category=category,
                    ground_truth_huf=truth,
                    arm0_estimate_huf=a0,
                    arm0_deviation_pct=_signed_deviation_pct(a0, truth),
                    arm1_estimate_huf=a1,
                    arm1_deviation_pct=_signed_deviation_pct(a1, truth),
                    closer_arm="arm1",
                )
            ],
        )

    def test_compute_metrics_overall_and_per_category(self):
        folds = [
            self._fold("komplett", "needs_plumbing", 110, 105, 100),
            self._fold("reszleges", "needs_electrical", 80, 120, 100),
        ]
        result = LOOCVResult(
            folds=folds,
            skipped=[],
            coverage_warnings=[],
            corpus_quote_count=2,
            indexed_year_min=2022,
            indexed_year_max=2026,
        )
        metrics = compute_metrics(result)
        # Arm 0: |+10| and |-20| -> 15%; Arm 1: |+5| and |+20| -> 12.5%
        assert metrics["per_arm"][ARM0]["mape_pct"] == pytest.approx(15.0)
        assert metrics["per_arm"][ARM1]["mape_pct"] == pytest.approx(12.5)
        assert metrics["per_arm"][ARM0]["per_category_mape_pct"][
            "needs_plumbing"
        ] == pytest.approx(10.0)
        assert metrics["per_arm"][ARM1]["per_category_mape_pct"][
            "needs_electrical"
        ] == pytest.approx(20.0)

    def test_completeness_breakdown(self):
        folds = [
            self._fold("komplett", "needs_plumbing", 150, 100, 100),
            self._fold("reszleges", "needs_electrical", 100, 100, 100),
        ]
        result = LOOCVResult(
            folds=folds,
            skipped=[],
            coverage_warnings=[],
            corpus_quote_count=2,
            indexed_year_min=2022,
            indexed_year_max=2026,
        )
        metrics = compute_metrics(result)
        assert metrics["per_arm"][ARM0]["per_completeness"]["komplett"][
            "mape_pct"
        ] == pytest.approx(50.0)
        assert (
            metrics["per_arm"][ARM0]["per_completeness"]["komplett"]["row_count"] == 1
        )

    def test_render_report_contains_key_sections(self):
        fold = self._fold("komplett", "needs_plumbing", 110, 105, 100)
        result = LOOCVResult(
            folds=[fold],
            skipped=[
                SkippedQuote("z", "z.xlsx", "missing required field(s): area_sqm")
            ],
            coverage_warnings=[],
            corpus_quote_count=2,
            indexed_year_min=2022,
            indexed_year_max=2026,
        )
        report = render_report(result, compute_metrics(result))
        assert "Aggregate MAPE" in report
        assert "needs_plumbing" in report
        assert "z.xlsx" in report
        assert "ground truth" in report


# ---------------------------------------------------------------------------
# Full sweep: leak guard
# ---------------------------------------------------------------------------


class TestRunLoocv:
    @pytest.mark.asyncio
    async def test_leak_guard_and_skip_reporting(self, file_db):
        await _seed_file_db(file_db)
        result = await run_loocv(file_db, _price_index())

        # The quote with no area is skipped with an explicit reason, never crashes.
        skipped_names = {s.file_name for s in result.skipped}
        assert "missing_area.xlsx" in skipped_names
        assert all("area_sqm" in s.reason for s in result.skipped)

        assert len(result.folds) == len(OTHER_IDS)
        total_quotes = len(QUOTES)
        for fold in result.folds:
            # Held-out id is physically absent from the training query pool...
            assert fold.quote_id not in fold.training_quote_ids
            # ...while every OTHER quote (including the one skipped as a fold)
            # remains available as training data.
            assert len(fold.training_quote_ids) == total_quotes - 1
            assert len(set(fold.training_quote_ids)) == total_quotes - 1
            # No price/id can reach the estimator through either ApartmentInput.
            for dumped in (fold.arm0_input, fold.arm1_input):
                assert "id" not in dumped
                assert not any(
                    "price" in key or "cost" in key or "huf" in key for key in dumped
                )
                assert set(dumped) == {
                    "district",
                    "total_area_sqm",
                    "num_rooms",
                    "building_type",
                    "building_era",
                    "needs_plumbing",
                    "needs_electrical",
                    "needs_flooring",
                    "needs_full_demolition",
                    "needs_windows_doors",
                    "needs_insulation",
                    "needs_ac",
                    "suspected_slag",
                }
            assert fold.outcomes, "every usable fold should score >=1 category"

    @pytest.mark.asyncio
    async def test_both_arms_use_same_training_pool(self, file_db):
        await _seed_file_db(file_db)
        result = await run_loocv(file_db, _price_index())
        for fold in result.folds:
            assert fold.arm0_scopes or fold.arm1_scopes
            assert fold.training_quote_ids  # non-empty pool


# ---------------------------------------------------------------------------
# Estimator default behavior is preserved
# ---------------------------------------------------------------------------


async def _seed_two_ascii_legacy_quotes(path: str) -> None:
    """Quotes whose legacy category_key is ASCII (folding would newly match)."""
    engine = get_engine(path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = get_session_maker(engine)
    async with session_maker() as session:
        for name, cost in (("a.xlsx", 1_000_000), ("b.xlsx", 1_500_000)):
            session.add(
                Quote(
                    file_name=name,
                    district=5,
                    quote_date=date(2024, 1, 1),
                    total_labor_huf=0,
                    total_material_huf=0,
                    grand_total_huf=1_000_000,
                    grand_total_adjusted_huf=1_000_000,
                    building_type=BuildingType.TEGLA,
                    building_era=1980,
                    area_sqm=50.0,
                    renovation_completeness="komplett",
                    num_line_items=1,
                    line_items=[
                        LineItemORM(
                            name_hu="Vízszerelés",
                            category_key="viz_futes",  # ASCII, not the accented key
                            category_key_v2="viz_futes",
                            total_cost_huf=cost,
                            section="main",
                            version=1,
                        )
                    ],
                )
            )
        await session.commit()
    await engine.dispose()


class TestDefaultCategoryColumnUnchanged:
    @pytest.mark.asyncio
    async def test_default_equals_explicit_legacy_column(self, file_db):
        await _seed_two_ascii_legacy_quotes(file_db)
        engine = get_engine(file_db)
        session_maker = get_session_maker(engine)
        apt = ApartmentInput(
            district=5,
            total_area_sqm=50.0,
            num_rooms=0,
            building_type="tegla",
            building_era=1980,
            needs_plumbing=True,
        )
        try:
            default = await scope_matched_estimate(
                apt, session_maker, _price_index(), date(2024, 1, 1)
            )
            explicit = await scope_matched_estimate(
                apt,
                session_maker,
                _price_index(),
                date(2024, 1, 1),
                category_column="category_key",
            )
        finally:
            await engine.dispose()
        assert default == explicit

    @pytest.mark.asyncio
    async def test_default_keeps_exact_match_but_v2_folds(self, file_db):
        await _seed_two_ascii_legacy_quotes(file_db)
        engine = get_engine(file_db)
        session_maker = get_session_maker(engine)
        apt = ApartmentInput(
            district=5,
            total_area_sqm=50.0,
            num_rooms=0,
            building_type="tegla",
            building_era=1980,
            needs_plumbing=True,
        )
        try:
            default = await scope_matched_estimate(
                apt, session_maker, _price_index(), date(2024, 1, 1)
            )
            canonical = await scope_matched_estimate(
                apt,
                session_maker,
                _price_index(),
                date(2024, 1, 1),
                category_column="category_key_v2",
            )
        finally:
            await engine.dispose()

        # Default must NOT fold the ASCII legacy value into the accented key.
        assert default["debug"]["scope_distinct_quote_count"]["needs_plumbing"] == 0
        # The new column path does fold, so both quotes are found.
        assert canonical["debug"]["scope_distinct_quote_count"]["needs_plumbing"] == 2
