"""Tests for the building-taxonomy backfill extraction + DB application."""
import asyncio
import pytest
from pathlib import Path

from scripts.backfill_building_taxonomy import (
    extract_building_era,
    extract_building_type,
    extract_taxonomy,
    apply_backfill,
    run,
)
from renovai.db.models import BuildingType, Quote
from renovai.db.session import get_engine


class TestBuildingEra:
    def test_panel_1970s_decade_midpoint(self):
        assert extract_building_era("1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm") == 1975

    def test_pre_1970_1930s(self):
        assert extract_building_era("1015 Csalogány utca 12. fsz majdnem komplett, 1930-as évek, van salak, 32nm") == 1935

    def test_exact_year(self):
        assert extract_building_era("Bolgár kertész utca 3, komplett, 1958, van salak, 53nm") == 1958

    def test_epites_eve(self):
        assert extract_building_era("komplett, 1958 építés éve, van salak, 51nm") == 1958

    def test_kb_epites_eve(self):
        assert extract_building_era("komplett, kb 1900 építés éve, van salak, 80nm") == 1900

    def test_kb_approx_year(self):
        assert extract_building_era("1011 Pongrác lakótelep, részleges, kb. 2005, nincs salak, fürdő-előtér") == 2005

    def test_year_range_underscore(self):
        assert extract_building_era("1148 Zugló -Németh László, komplett, 1950_60-as évek, van salak, 53nm") == 1955

    def test_postal_code_not_era(self):
        # 1035 is a Budapest postal code (1000-1299), not a construction year.
        assert extract_building_era("1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm") == 1975

    def test_modern_year(self):
        assert extract_building_era("részleges, 2004, nincs salak, családi ház, 82nm") == 2004

    def test_no_era(self):
        assert extract_building_era("lakásfelújítás megjegyzések") is None


class TestBuildingType:
    def test_panel(self):
        assert extract_building_type("1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm") == BuildingType.PANEL

    def test_panel_parenthesized(self):
        assert extract_building_type("1157 Páskom park fszt 3. részleges, 1970-es évek, nincs salak (panel), 57-65nm") == BuildingType.PANEL

    def test_csaladi_haz(self):
        assert extract_building_type("részleges, 2004, nincs salak, családi ház, 82nm") == BuildingType.TEGLA_CSALADI_HAZ

    def test_ambiguous_returns_none(self):
        assert extract_building_type("1092 Ráday utca 5. 2 emelet, komplett, 1900-as évek, van salak, 83nm") is None

    def test_product_term_not_type(self):
        # "fűtéspanel" is a heating panel product, not a building type.
        assert extract_building_type("nappaliban elektromos fűtéspanel") is None

    def test_teglasi_street_not_type(self):
        # "Téglási" is a street-name element, not the tégla type.
        assert extract_building_type("Budakalász Téglási András utca, részleges, 2013, nincs salak") is None

    def test_valyog(self):
        assert extract_building_type("vályog épület, 1950 előtt") == BuildingType.VALYOG_VEGYES


class TestExtractTaxonomy:
    def test_full_extraction(self):
        tax = extract_taxonomy(
            "1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm"
        )
        assert tax["building_type"] == BuildingType.PANEL
        assert tax["building_era"] == 1975

    def test_nulls_for_ambiguous(self):
        tax = extract_taxonomy("1092 Ráday utca 5. 2 emelet, komplett, 1900-as évek, van salak, 83nm")
        assert tax["building_type"] is None
        assert tax["building_era"] == 1905


@pytest.fixture
async def backfill_engine():
    engine = get_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from renovai.db.models import Base
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


class TestApplyBackfill:
    @pytest.mark.asyncio
    async def test_applies_taxonomy_to_rows(self, backfill_engine):
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker
        maker = async_sessionmaker(backfill_engine, expire_on_commit=False)
        async with maker() as s:
            s.add(Quote(file_name="1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm.xlsx",
                        total_labor_huf=1, total_material_huf=1, grand_total_huf=2, num_line_items=0))
            await s.commit()

        rows = [{
            "file_name": "1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm.xlsx",
            "building_type": "panel",
            "building_era": 1975,
        }]
        applied, unresolved = await apply_backfill(rows, backfill_engine)
        assert (applied, unresolved) == (1, 0)

        async with maker() as s:
            q = (await s.execute(select(Quote))).scalars().one()
            assert q.building_type == BuildingType.PANEL
            assert q.building_era == 1975

    @pytest.mark.asyncio
    async def test_null_kept_for_ambiguous(self, backfill_engine):
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker
        maker = async_sessionmaker(backfill_engine, expire_on_commit=False)
        async with maker() as s:
            s.add(Quote(file_name="1092 Ráday utca 5. 2 emelet, komplett, 1900-as évek, van salak, 83nm.xlsx",
                        total_labor_huf=1, total_material_huf=1, grand_total_huf=2, num_line_items=0))
            await s.commit()

        rows = [{
            "file_name": "1092 Ráday utca 5. 2 emelet, komplett, 1900-as évek, van salak, 83nm.xlsx",
            "building_type": None,
            "building_era": 1905,
        }]
        applied, unresolved = await apply_backfill(rows, backfill_engine)
        assert (applied, unresolved) == (0, 1)

        async with maker() as s:
            q = (await s.execute(select(Quote))).scalars().one()
            assert q.building_type is None
            assert q.building_era == 1905

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write(self, backfill_engine):
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker
        maker = async_sessionmaker(backfill_engine, expire_on_commit=False)
        async with maker() as s:
            s.add(Quote(file_name="q.xlsx", total_labor_huf=1, total_material_huf=1, grand_total_huf=2, num_line_items=0))
            await s.commit()

        rows = [{"file_name": "q.xlsx", "building_type": "panel", "building_era": 1980}]
        await apply_backfill(rows, backfill_engine, dry_run=True)

        async with maker() as s:
            q = (await s.execute(select(Quote))).scalars().one()
            assert q.building_type is None
            assert q.building_era is None
