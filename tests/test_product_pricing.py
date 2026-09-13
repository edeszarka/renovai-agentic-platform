"""Tests for the owner-purchased product pricing catalog (doc 04)."""

import pytest

from renovai.predictor.product_pricing import (
    APPLIANCE_NAMES,
    APPLIANCE_PRICES,
    SANITARY_PRICES,
    TIERS,
    door_install,
    lookup,
    normalize_tier,
    xps_underlay,
)


class TestTierNormalization:
    def test_ascii_keys(self):
        assert TIERS == [
            "also",
            "also_kozep",
            "kozep_kozep",
            "felso_kozep",
            "premium",
            "luxus",
        ]

    def test_accented_hungarian_labels(self):
        assert normalize_tier("Alsó") == "also"
        assert normalize_tier("Felső közép") == "felso_kozep"
        assert normalize_tier("Prémium") == "premium"
        assert normalize_tier("luxus") == "luxus"

    def test_spaces_and_casing(self):
        assert normalize_tier("  ALSO   KOZEP ") == "also_kozep"
        assert normalize_tier("közép_közép") == "kozep_kozep"

    def test_unknown_tier_raises(self):
        with pytest.raises(KeyError):
            normalize_tier("megafancy")


class TestTileLookup:
    def test_premium_60x120(self):
        # doc 04 §1: prémium 60x120 = 15-20 e Ft/nm
        assert lookup("tile", "premium", size="60x120") == (15_000, 20_000)

    def test_also_60x30(self):
        # doc 04 §1: alsó 60x30 = 4-6 e Ft/nm
        assert lookup("tile", "also", size="60x30") == (4_000, 6_000)

    def test_luxus_multisize_from(self):
        # doc 04 §1: luxus 60x120 = 20-25 e+ Ft/nm
        assert lookup("tile", "luxus", size="60x120") == (20_000, 25_000)

    def test_missing_tier_is_none(self):
        assert lookup("tile", "also", size="80x80") == (None, None)

    def test_invalid_size_raises(self):
        with pytest.raises(KeyError):
            lookup("tile", "premium", size="33x33")


class TestApplianceLookup:
    def test_dishwasher_kozep_kozep(self):
        # doc 04 §8: mosogatógép közép közép = 120-140 e Ft
        assert lookup(
            "appliance", "kozep_kozep", appliance="dishwasher_mosogatogep"
        ) == (120_000, 140_000)

    def test_range_hood_felso_kozep(self):
        assert lookup(
            "appliance", "felso_kozep", appliance="range_hood_szagelszivo"
        ) == (40_000, 55_000)

    def test_all_appliances_have_all_tiers(self):
        for app in APPLIANCE_NAMES:
            assert set(APPLIANCE_PRICES[app]) == set(TIERS), app

    def test_unknown_appliance_raises(self):
        with pytest.raises(KeyError):
            lookup("appliance", "also", appliance="hoverboard")


class TestSanitaryLookup:
    def test_premium(self):
        # doc 04 §3: prémium szaniter = 700 e - 1 M Ft
        assert lookup("sanitary", "premium") == (700_000, 1_000_000)

    def test_luxus_open_ended(self):
        assert lookup("sanitary", "luxus") == (1_000_000, None)


class TestDoorLookup:
    def test_kulso_zsaneros_with_glass(self):
        assert lookup(
            "door", "also", door_type="kulso_zsaneros_1m", glass="with_glass"
        ) == (130_000, 180_000)

    def test_install(self):
        assert door_install("kulso_zsaneros_1m", "with_glass") == (25_000, 30_000)


class TestOtherCategories:
    def test_kitchen_felso_kozep(self):
        # doc 04 §7: felső közép konyhabútor = 1.5-2.5 M Ft
        assert lookup("kitchen", "felso_kozep") == (1_500_000, 2_500_000)

    def test_lamp_premium(self):
        # doc 04 §6: prémium lámpa = 250-500 e Ft
        assert lookup("lamp", "premium") == (250_000, 500_000)

    def test_laminate_12mm(self):
        # doc 04 §2: 12 mm laminált = 6 500-9 500 Ft/nm
        assert lookup("laminate", "also", thickness="12mm") == (6_500, 9_500)

    def test_xps_underlay(self):
        assert xps_underlay("3mm") == (550, 700)
        assert xps_underlay("5mm") == (750, 1_000)


class TestCrossSectionConsistency:
    def test_tiers_monotone_for_sanitary(self):
        # Higher tiers should not be cheaper than lower tiers (lower bound).
        for i in range(1, len(TIERS)):
            prev_low = SANITARY_PRICES[TIERS[i - 1]][0]
            cur_low = SANITARY_PRICES[TIERS[i]][0]
            assert cur_low is None or prev_low is None or cur_low >= prev_low
