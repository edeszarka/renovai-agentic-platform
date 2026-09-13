import json
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from renovai.ingestion.inflation_models import CPIRecord, PriceIndex
from renovai.predictor.feature_extractor import (
    ApartmentInput,
    QuoteFeatures,
    apartment_input_to_features,
    build_feature_matrix,
    extract_features,
)
from renovai.predictor.price_model import find_similar_quotes, predict, train_model


@pytest.fixture
def mock_adjusted_json(tmp_path):
    data = {
        "original_metadata": {
            "file_name": "test.xlsx",
            "address_raw": "Bp 12",
            "district": 12,
            "total_labor": 100,
            "total_material": 100,
            "grand_total": 200,
        },
        "target_date": "2024-06-01",
        "line_items_adjusted": [
            {
                "original": {
                    "name": "Bontás",
                    "labor_cost": 50,
                    "material_cost": 0,
                    "total_cost": 50,
                    "section": "Main",
                    "version": 1,
                    "notes": "Kohósalak van itt",
                },
                "total_cost_adjusted": 60,
                "labor_cost_adjusted": 60,
                "material_cost_adjusted": 0,
                "adjustment_factor_labor": 1.2,
                "adjustment_factor_materials": 1.1,
                "target_date": "2024-06-01",
            },
            {
                "original": {
                    "name": "Burkolás",
                    "labor_cost": 50,
                    "material_cost": 50,
                    "total_cost": 100,
                    "section": "Main",
                    "version": 1,
                },
                "total_cost_adjusted": 115,
                "labor_cost_adjusted": 60,
                "material_cost_adjusted": 55,
                "adjustment_factor_labor": 1.2,
                "adjustment_factor_materials": 1.1,
                "target_date": "2024-06-01",
            },
        ],
        "grand_total_original": 150,
        "grand_total_adjusted": 175,
        "inflation_delta_pct": 16.67,
    }
    path = tmp_path / "test_adjusted.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def test_extract_features_detection(mock_adjusted_json):
    feat = extract_features(mock_adjusted_json)
    assert feat.district == 12
    assert feat.has_demolition is True
    assert feat.has_slag_complication is True
    assert feat.has_flooring_work is True  # Burkolás
    assert feat.labor_to_material_ratio == (60 + 60) / 55
    assert feat.grand_total_adjusted == 175


def test_apartment_input_mapping():
    inp = ApartmentInput(
        district=5,
        total_area_sqm=50,
        num_rooms=2,
        needs_plumbing=True,
        needs_full_demolition=False,
    )
    feat = apartment_input_to_features(inp)
    assert feat.district == 5
    assert feat.total_area_sqm == 50
    assert feat.has_plumbing_work is True
    assert feat.has_demolition is False
    assert feat.plumbing_cost_share > 0


def test_build_feature_matrix(tmp_path, mock_adjusted_json):
    # Copy it to have two files
    path2 = tmp_path / "test2_adjusted.json"
    path2.write_text(mock_adjusted_json.read_text())

    X, y = build_feature_matrix(tmp_path)
    assert len(X) == 2
    assert "district" in X.columns
    assert y.iloc[0] == 175


def test_train_model_runs(tmp_path):
    X = pd.DataFrame(
        [
            {
                "district": 1,
                "total_area_sqm": 50,
                "num_rooms": 1,
                "has_plumbing_work": True,
                "has_electrical_work": True,
                "has_flooring_work": True,
                "has_demolition": True,
                "has_slag_complication": False,
                "num_line_items": 10,
                "labor_to_material_ratio": 1.1,
                "demolition_cost_share": 0.1,
                "plumbing_cost_share": 0.2,
            },
            {
                "district": 2,
                "total_area_sqm": 60,
                "num_rooms": 2,
                "has_plumbing_work": True,
                "has_electrical_work": False,
                "has_flooring_work": True,
                "has_demolition": False,
                "has_slag_complication": True,
                "num_line_items": 12,
                "labor_to_material_ratio": 1.2,
                "demolition_cost_share": 0.0,
                "plumbing_cost_share": 0.3,
            },
            {
                "district": 3,
                "total_area_sqm": 70,
                "num_rooms": 3,
                "has_plumbing_work": False,
                "has_electrical_work": True,
                "has_flooring_work": False,
                "has_demolition": True,
                "has_slag_complication": False,
                "num_line_items": 8,
                "labor_to_material_ratio": 1.0,
                "demolition_cost_share": 0.2,
                "plumbing_cost_share": 0.0,
            },
            {
                "district": 4,
                "total_area_sqm": 80,
                "num_rooms": 3,
                "has_plumbing_work": True,
                "has_electrical_work": True,
                "has_flooring_work": True,
                "has_demolition": True,
                "has_slag_complication": False,
                "num_line_items": 15,
                "labor_to_material_ratio": 1.3,
                "demolition_cost_share": 0.15,
                "plumbing_cost_share": 0.25,
            },
            {
                "district": 5,
                "total_area_sqm": 40,
                "num_rooms": 1,
                "has_plumbing_work": False,
                "has_electrical_work": False,
                "has_flooring_work": True,
                "has_demolition": False,
                "has_slag_complication": False,
                "num_line_items": 5,
                "labor_to_material_ratio": 0.9,
                "demolition_cost_share": 0.0,
                "plumbing_cost_share": 0.0,
            },
        ]
    )
    y = pd.Series([1000, 1200, 1100, 1500, 800])

    results = train_model(X, y, tmp_path)
    assert results["n_samples"] == 5
    assert (tmp_path / "price_model.joblib").exists()


@patch("joblib.load")
def test_predict_with_inflation(mock_load, tmp_path):
    # Create dummy file to satisfy .exists() check
    model_path = tmp_path / "price_model.joblib"
    model_path.write_text("dummy")

    mock_pipe = MagicMock()
    mock_pipe.predict.return_value = [1000000]
    mock_load.return_value = mock_pipe

    feat = QuoteFeatures(
        district=1,
        has_plumbing_work=True,
        has_electrical_work=True,
        has_flooring_work=True,
        has_demolition=True,
        has_slag_complication=False,
        num_line_items=10,
        num_versions=1,
        labor_to_material_ratio=1.0,
        demolition_cost_share=0.1,
        plumbing_cost_share=0.2,
    )

    index = PriceIndex(
        materials=[
            CPIRecord(year=2024, quarter=1, index_value=1.0),
            CPIRecord(year=2025, quarter=1, index_value=1.2),
        ],
        labor=[
            CPIRecord(year=2024, quarter=1, index_value=1.0),
            CPIRecord(year=2025, quarter=1, index_value=1.1),
        ],
        generated_at="2024-01-01",
    )

    res = predict(feat, index, date(2025, 2, 15), tmp_path)

    # 1.0 ratio means 500k labor, 500k material
    # labor factor = 1.1, material factor = 1.2
    # predicted = 500k*1.1 + 500k*1.2 = 550k + 600k = 1,150,000
    assert res["estimate_mid_huf"] == pytest.approx(1150000, abs=1000)
    assert res["estimate_low_huf"] == pytest.approx(int(1150000 * 0.85), abs=1000)


def test_find_similar_quotes(tmp_path, mock_adjusted_json):
    feat = extract_features(mock_adjusted_json)
    # distance should be 0 to itself
    similar = find_similar_quotes(feat, tmp_path)
    assert len(similar) == 1
    assert similar[0]["distance"] == 0
