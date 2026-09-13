"""
Verification follow-up tests for the building-type/era similarity weighting.

Coverage added by the Task 2 self-verification pass:

* CHECK 3 — ``_weighted_mean`` is self-normalizing: a uniform down-weighting
  of ALL candidate weights must cancel out (no distortion).
* CHECK 4 — canonical token matching must NOT conflate distinct doc-01
  taxonomy categories that share a substring (``tegla`` vs
  ``teglacsaladihaz``).
"""

import numpy as np

from renovai.db.models import BuildingType as BT
from renovai.predictor.price_model import (
    _weighted_mean,
    building_type_similarity,
    era_similarity,
)


class TestEraSimilarity:
    def test_close_era_full_weight(self):
        assert era_similarity(1960, 1965) == 1.0

    def test_within_band_full_weight(self):
        assert era_similarity(1960, 1970) == 1.0

    def test_distant_era_downweighted(self):
        w = era_similarity(1960, 2010)
        assert 0.3 <= w < 1.0

    def test_very_distant_era_floored_never_zero(self):
        w = era_similarity(1900, 2010)
        assert w == 0.3

    def test_missing_era_neutral(self):
        assert era_similarity(None, 1970) == 1.0
        assert era_similarity(1970, None) == 1.0


class TestBuildingTypeSimilarity:
    def test_exact_match_full_weight(self):
        assert building_type_similarity("tégla", BT.TEGLA) == 1.0
        assert building_type_similarity("panel", BT.PANEL) == 1.0

    def test_accent_folded_match(self):
        assert building_type_similarity("tégla", BT.TEGLA) == 1.0
        assert building_type_similarity("csúszózsalus", BT.CSUSZOZSALUS) == 1.0

    def test_free_text_contains_canonical_token(self):
        assert building_type_similarity("1980 előtti tégla", BT.TEGLA) == 1.0

    def test_distinct_categories_mismatch(self):
        # CHECK 4 regression: "tegla" is a SUBSTRING of "teglacsaladihaz",
        # but they are distinct doc-01 categories (apartment brick vs
        # family-house brick) and must NOT be conflated.
        assert building_type_similarity("tégla", BT.TEGLA_CSALADI_HAZ) == 0.5
        assert building_type_similarity("panel", BT.TEGLA_CSALADI_HAZ) == 0.5

    def test_mismatch_downweighted_not_excluded(self):
        assert building_type_similarity("panel", BT.TEGLA) == 0.5

    def test_missing_side_neutral(self):
        assert building_type_similarity(None, BT.PANEL) == 1.0
        assert building_type_similarity("panel", None) == 1.0
        assert building_type_similarity("ismeretlen", BT.PANEL) == 1.0


class TestWeightedMeanScaleInvariance:
    def test_uniform_downweight_cancels(self):
        """CHECK 3: the weighted mean is self-normalizing. Multiplying every
        candidate weight by the same constant must not change the result,
        so no explicit renormalization pass is needed."""
        values = np.array([10.0, 20.0, 30.0, 40.0])
        weights = np.array([1.0, 4.0, 2.0, 3.0])
        scaled = 0.3 * weights

        base = _weighted_mean(values, weights)
        uniform_scaled = _weighted_mean(values, scaled)

        assert base == np.sum(weights * values) / np.sum(weights)
        assert np.isclose(base, uniform_scaled)
        # A purely uniform weight vector reduces to the plain arithmetic mean
        ones = np.ones_like(values)
        assert np.isclose(_weighted_mean(values, ones), values.mean())

    def test_differential_weights_still_bite(self):
        """Sanity: the mean DOES respond to non-uniform weight differences,
        so the self-normalization is not masking the similarity factor."""
        values = np.array([10.0, 40.0])
        assert _weighted_mean(values, np.array([1.0, 0.0])) == 10.0
        assert _weighted_mean(values, np.array([0.0, 1.0])) == 40.0
        assert _weighted_mean(values, np.array([1.0, 1.0])) == 25.0
