"""Tests for the building-taxonomy backfill extraction + DB application."""

import pytest

from renovai.db.models import BuildingType, Quote
from renovai.db.session import get_engine
from scripts.backfill_building_taxonomy import (
    apply_backfill,
    extract_building_era,
    extract_building_type,
    extract_elevator_type,
    extract_floor_number,
    extract_renovation_completeness,
    extract_taxonomy,
)


class TestBuildingEra:
    def test_panel_1970s_decade_midpoint(self):
        assert (
            extract_building_era(
                "1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm"
            )
            == 1975
        )

    def test_pre_1970_1930s(self):
        assert (
            extract_building_era(
                "1015 Csalogány utca 12. fsz majdnem komplett, 1930-as évek, van salak, 32nm"
            )
            == 1935
        )

    def test_exact_year(self):
        assert (
            extract_building_era(
                "Bolgár kertész utca 3, komplett, 1958, van salak, 53nm"
            )
            == 1958
        )

    def test_epites_eve(self):
        assert (
            extract_building_era("komplett, 1958 építés éve, van salak, 51nm") == 1958
        )

    def test_kb_epites_eve(self):
        assert (
            extract_building_era("komplett, kb 1900 építés éve, van salak, 80nm")
            == 1900
        )

    def test_kb_approx_year(self):
        assert (
            extract_building_era(
                "1011 Pongrác lakótelep, részleges, kb. 2005, nincs salak, fürdő-előtér"
            )
            == 2005
        )

    def test_year_range_underscore(self):
        assert (
            extract_building_era(
                "1148 Zugló -Németh László, komplett, 1950_60-as évek, van salak, 53nm"
            )
            == 1955
        )

    def test_postal_code_not_era(self):
        # 1035 is a Budapest postal code (1000-1299), not a construction year.
        assert (
            extract_building_era(
                "1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm"
            )
            == 1975
        )

    def test_modern_year(self):
        assert (
            extract_building_era("részleges, 2004, nincs salak, családi ház, 82nm")
            == 2004
        )

    def test_no_era(self):
        assert extract_building_era("lakásfelújítás megjegyzések") is None


class TestBuildingType:
    def test_panel(self):
        assert (
            extract_building_type(
                "1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm"
            )
            == BuildingType.PANEL
        )

    def test_panel_parenthesized(self):
        assert (
            extract_building_type(
                "1157 Páskom park fszt 3. részleges, 1970-es évek, nincs salak (panel), 57-65nm"
            )
            == BuildingType.PANEL
        )

    def test_csaladi_haz(self):
        assert (
            extract_building_type("részleges, 2004, nincs salak, családi ház, 82nm")
            == BuildingType.TEGLA_CSALADI_HAZ
        )

    def test_ambiguous_defaults_to_tegla(self):
        # No explicit type token, but the filename matches the corpus grammar
        # (era clause present) -> TEGLA default (Task 2.5 rule).
        assert (
            extract_building_type(
                "1092 Ráday utca 5. 2 emelet, komplett, 1900-as évek, van salak, 83nm"
            )
            == BuildingType.TEGLA
        )

    def test_product_term_not_type(self):
        # "fűtéspanel" is a heating panel product, not a building type; and the
        # text has no corpus grammar markers, so it stays None (not TEGLA).
        assert extract_building_type("nappaliban elektromos fűtéspanel") is None

    def test_teglasi_street_defaults_to_tegla(self):
        # "Téglási" is a street-name element, NOT the tégla type token; the
        # filename still matches the corpus grammar, so it gets the TEGLA
        # default via the era clause, not via a téglási match.
        assert (
            extract_building_type(
                "Budakalász Téglási András utca, részleges, 2013, nincs salak"
            )
            == BuildingType.TEGLA
        )

    def test_plain_arbitrary_text_none(self):
        # No corpus grammar at all -> leave NULL rather than force a default.
        assert extract_building_type("lakásfelújítás megjegyzések") is None

    def test_example_csalogany(self):
        # User Task 2.5 example filename (1 of 2).
        assert (
            extract_building_type(
                "1015 Csalogány utca 12. fsz majdnem komplett, 1930-as évek, van salak, 32nm"
            )
            == BuildingType.TEGLA
        )

    def test_example_kek_golyo(self):
        # User Task 2.5 example filename (2 of 2).
        assert (
            extract_building_type(
                "1123 Kék Golyó utca 30. 4 emelet, komplett, 1930-as évek, van salak, 42nm"
            )
            == BuildingType.TEGLA
        )


class TestBuildingFloor:
    def test_ground_floor_fsz(self):
        # Ground floor = 1 (codebase convention, matches elevator_surcharge).
        assert (
            extract_floor_number(
                "1015 Csalogány utca 12. fsz majdnem komplett, 1930-as évek, van salak, 32nm"
            )
            == 1
        )

    def test_floor_number_kek_golyo(self):
        # "4 emelet" -> 4 (matches the user's Task 2.5 example 2).
        assert (
            extract_floor_number(
                "1123 Kék Golyó utca 30. 4 emelet, komplett, 1930-as évek, van salak, 42nm"
            )
            == 4
        )

    def test_no_floor_clause(self):
        assert (
            extract_floor_number(
                "1092 Ráday utca 5. komplett, 1900-as évek, van salak, 83nm"
            )
            is None
        )

    def test_task2c_floor_regression_three_filenames(self):
        # Task 2C regression: the 3 user-quoted filenames must yield
        # floor_numbers 2, 2, 3 respectively, under BOTH "2 emelet" and
        # "3. emelet" spellings and despite the preceding comma in case 3.
        cases = [
            ("1092 Ráday utca 5. 2 emelet, komplett, 1900-as évek, van salak, 83nm", 2),
            (
                "1126 Hollósy Simon utca 30. 2 emelet, komplett, 1930-as évek, van salak, 76nm",
                2,
            ),
            (
                "1065 Bajcsy-Zsilinszky út 19, 3. emelet, részleges, 1930-as évek, van salak, 80nm, fürdő felújítás",
                3,
            ),
        ]
        for stem, expected in cases:
            assert extract_floor_number(stem) == expected


class TestRenovationCompleteness:
    def test_komplett(self):
        assert (
            extract_renovation_completeness(
                "1092 Ráday utca 5. 2 emelet, komplett, 1900-as évek, van salak, 83nm"
            )
            == "komplett"
        )

    def test_reszleges(self):
        assert (
            extract_renovation_completeness(
                "1065 Bajcsy-Zsilinszky út 19, 3. emelet, részleges, 1930-as évek, van salak, 80nm"
            )
            == "reszleges"
        )

    def test_reszletes_es_komplett_counts_as_komplett(self):
        assert (
            extract_renovation_completeness(
                "részletes és komplett, 1900-as évek, van salak, 47nm"
            )
            == "komplett"
        )

    def test_majdnem_komplett_is_not_force_fit(self):
        # Blend -> None: reported as "neither/unclear", not forced binary.
        assert (
            extract_renovation_completeness(
                "1015 Csalogány utca 12. fsz majdnem komplett, 1930-as évek, van salak, 32nm"
            )
            is None
        )

    def test_tobbnyire_komplett_is_not_force_fit(self):
        assert (
            extract_renovation_completeness(
                "többnyire komplett, 1930-as évek, van salak, 48nm"
            )
            is None
        )

    def test_no_clause(self):
        assert extract_renovation_completeness("lakásfelújítás megjegyzések") is None

    def test_case_insensitive_accented(self):
        assert (
            extract_renovation_completeness(
                "RÉSZLEGES, 1970-es évek, nincs salak, 49nm"
            )
            == "reszleges"
        )


class TestElevatorExtraction:
    def test_conditional_lift_use_present(self):
        # "ha lehet használni a liftet" presupposes a lift exists -> present.
        body = "Ár: 1 200 000 Ft. Minden ár csak akkor érvényes, ha lehet használni a liftet az anyagszállításhoz."
        assert extract_elevator_type(body) == "present"

    def test_lifthasználat_present(self):
        body = "Ha van lifthasználat, felvitel díja 0 Ft."
        assert extract_elevator_type(body) == "present"

    def test_interposed_words_present(self):
        # Task 2C: the 1958-58nm file worded it "lehet anyagmozgatásra
        # használni a liftet" — the generic lift-word rule catches it.
        body = "Kb. 16 tonna ... Ez az ár akkor érvényes, ha végig lehet anyagmozgatásra használni a liftet."
        assert extract_elevator_type(body) == "present"

    def test_explicit_no_lift_none(self):
        body = "A házban nincs lift, a felvitelt gyalog végezzük."
        assert extract_elevator_type(body) == "none"

    def test_no_mention_is_none(self):
        body = "Komplett fürdőszoba felújítás, 80 nm."
        assert extract_elevator_type(body) is None

    def test_empty_body_none(self):
        assert extract_elevator_type("") is None
        assert extract_elevator_type(None) is None


class TestExtractTaxonomy:
    def test_full_extraction(self):
        tax = extract_taxonomy(
            "1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm"
        )
        assert tax["building_type"] == BuildingType.PANEL
        assert tax["building_era"] == 1975

    def test_tegla_default_for_ambiguous(self):
        tax = extract_taxonomy(
            "1092 Ráday utca 5. 2 emelet, komplett, 1900-as évek, van salak, 83nm"
        )
        assert tax["building_type"] == BuildingType.TEGLA
        assert tax["building_era"] == 1905

    def test_example_filenames_floor_and_era(self):
        # Task 2.5 examples: type TEGLA, era ~1930, floors 1 (ground) and 4.
        for stem, floor in [
            (
                "1015 Csalogány utca 12. fsz majdnem komplett, 1930-as évek, van salak, 32nm",
                1,
            ),
            (
                "1123 Kék Golyó utca 30. 4 emelet, komplett, 1930-as évek, van salak, 42nm",
                4,
            ),
        ]:
            tax = extract_taxonomy(stem)
            assert tax["building_type"] == BuildingType.TEGLA
            assert tax["building_era"] == 1935
            assert extract_floor_number(stem) == floor


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
            s.add(
                Quote(
                    file_name="1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm.xlsx",
                    total_labor_huf=1,
                    total_material_huf=1,
                    grand_total_huf=2,
                    num_line_items=0,
                )
            )
            await s.commit()

        rows = [
            {
                "file_name": "1035 Szellő utca 10. 6. emelet, komplett, 1970-es évek, nincs salak, panel, 49nm.xlsx",
                "building_type": "panel",
                "building_era": 1975,
            }
        ]
        applied, unresolved = await apply_backfill(rows, backfill_engine)
        assert (applied, unresolved) == (1, 0)

        async with maker() as s:
            q = (await s.execute(select(Quote))).scalars().one()
            assert q.building_type == BuildingType.PANEL
            assert q.building_era == 1975

    @pytest.mark.asyncio
    async def test_tegla_default_applied(self, backfill_engine):
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(backfill_engine, expire_on_commit=False)
        async with maker() as s:
            s.add(
                Quote(
                    file_name="1092 Ráday utca 5. 2 emelet, komplett, 1900-as évek, van salak, 83nm.xlsx",
                    total_labor_huf=1,
                    total_material_huf=1,
                    grand_total_huf=2,
                    num_line_items=0,
                )
            )
            await s.commit()

        rows = [
            {
                "file_name": "1092 Ráday utca 5. 2 emelet, komplett, 1900-as évek, van salak, 83nm.xlsx",
                "building_type": "tegla",
                "building_era": 1905,
            }
        ]
        applied, unresolved = await apply_backfill(rows, backfill_engine)
        assert (applied, unresolved) == (1, 0)

        async with maker() as s:
            q = (await s.execute(select(Quote))).scalars().one()
            assert q.building_type == BuildingType.TEGLA
            assert q.building_era == 1905

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write(self, backfill_engine):
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(backfill_engine, expire_on_commit=False)
        async with maker() as s:
            s.add(
                Quote(
                    file_name="q.xlsx",
                    total_labor_huf=1,
                    total_material_huf=1,
                    grand_total_huf=2,
                    num_line_items=0,
                )
            )
            await s.commit()

        rows = [{"file_name": "q.xlsx", "building_type": "panel", "building_era": 1980}]
        await apply_backfill(rows, backfill_engine, dry_run=True)

        async with maker() as s:
            q = (await s.execute(select(Quote))).scalars().one()
            assert q.building_type is None
            assert q.building_era is None
