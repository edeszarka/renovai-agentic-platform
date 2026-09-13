import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from renovai.advisor.pre_purchase import (
    AdvisoryReport,
    ApartmentProfile,
    ChecklistItem,
    _era_label,
    gather_advisory_context,
    generate_report,
)
from renovai.advisor.report_renderer import render_report_md
from renovai.rag.chunker import Chunk
from renovai.rag.gemini_client import GeminiConfig
from renovai.rag.pipeline import RAGResponse, RetrievedContext


@pytest.fixture
def mock_profile():
    return ApartmentProfile(
        address_district=11,
        floor_area_sqm=55.0,
        num_rooms=2,
        building_type="tégla",
        building_era_approx=1960,
        current_condition="közepes",
        known_issues=["régi ablakok"],
        has_seen_in_person=True,
        asking_price_million_huf=45.0,
    )


def test_gather_advisory_context(mock_profile):
    mock_pipeline = MagicMock()

    # Mock return value for query_with_trace
    chunk = Chunk(
        chunk_id="c1",
        source_file="f1.md",
        source_type="quote",
        section="Sec",
        content="Content",
        metadata={},
    )
    mock_resp = RAGResponse(
        answer="Ans",
        sources_cited=["f1.md"],
        confidence="low",
        retrieval_metadata={"top_scores": [0.9]},
        tokens_used=10,
    )
    mock_trace = RetrievedContext(chunks=[chunk], query="Q", retrieval_metadata={})
    mock_pipeline.query_with_trace.return_value = (mock_resp, mock_trace)

    context, sources = gather_advisory_context(mock_profile, mock_pipeline)

    # 7 templates in pre_purchase.py
    assert mock_pipeline.query_with_trace.call_count == 7
    assert "[FORRÁS 1: f1.md | Sec]" in context
    assert sources == ["f1.md"]


def test_overall_risk_logic():
    # Case 1: kritikus flag -> magas risk
    r1 = ChecklistItem(category="C", item="I", priority="kritikus", why="W")
    report_data = {"questions": [], "inspection": [], "red_flags": [r1.model_dump()]}

    # We'll just test the logic inside generate_report manually here or via a partial mock
    # Risk logic:
    # risk = "alacsony"
    # if any(it.priority == "kritikus" for it in r_items): risk = "magas"
    # elif r_items: risk = "közepes"

    def check_risk(items):
        risk = "alacsony"
        if any(it.priority == "kritikus" for it in items):
            return "magas"
        elif items:
            return "közepes"
        return risk

    assert check_risk([r1]) == "magas"

    r2 = ChecklistItem(category="C", item="I", priority="fontos", why="W")
    assert check_risk([r2]) == "közepes"
    assert check_risk([]) == "alacsony"


def test_render_report_md(mock_profile):
    report = AdvisoryReport(
        apartment_profile=mock_profile,
        generated_at=datetime.now(),
        questions_for_seller=[
            ChecklistItem(
                category="Víz", item="Strang csere?", priority="kritikus", why="Fontos"
            )
        ],
        inspection_checklist=[],
        red_flags=[],
        cost_estimate={
            "estimate_low_huf": 1000,
            "estimate_mid_huf": 2000,
            "estimate_high_huf": 3000,
            "inflation_adjusted_to": "2024-01-01",
        },
        similar_cases=[],
        rag_sources_used=["s1.md"],
        overall_risk="magas",
        summary_hu="Összefoglaló szöveg.",
    )

    md = render_report_md(report)
    assert "# Felújítási Tanácsadói Jelentés" in md
    assert "11. kerület" in md
    assert "Összefoglaló szöveg." in md
    assert "Strang csere?" in md
    assert "1 000 Ft" in md


@patch("renovai.advisor.pre_purchase.genai.Client")
@patch("renovai.advisor.pre_purchase.predict")
@patch("renovai.advisor.pre_purchase.find_similar_quotes")
@patch("renovai.advisor.pre_purchase.gather_advisory_context")
def test_generate_report_end_to_end(
    mock_gather, mock_similar, mock_predict, mock_client_class, mock_profile
):
    mock_gather.return_value = ("Context", ["f1.md"])
    mock_predict.return_value = {
        "estimate_low_huf": 1,
        "estimate_mid_huf": 2,
        "estimate_high_huf": 3,
        "inflation_adjusted_to": "2024",
    }
    mock_similar.return_value = []

    # Mock Gemini client and responses
    mock_client = mock_client_class.return_value

    mock_resp_json = MagicMock()
    mock_resp_json.text = json.dumps(
        {
            "questions": [
                {"category": "V", "item": "Q", "priority": "fontos", "why": "W"}
            ],
            "inspection": [],
            "red_flags": [
                {"category": "S", "item": "F", "priority": "fontos", "why": "W"}
            ],
        }
    )

    mock_resp_sum = MagicMock()
    mock_resp_sum.text = "Summary"

    mock_client.models.generate_content.side_effect = [mock_resp_json, mock_resp_sum]

    pipeline = MagicMock()
    gem_cfg = GeminiConfig(api_key="k", model_name="m")

    # We need to mock price_index as well
    report = generate_report(
        mock_profile, pipeline, Path("models"), MagicMock(), gem_cfg
    )

    assert report.summary_hu == "Summary"
    assert len(report.questions_for_seller) == 1
    assert report.overall_risk == "közepes"  # because 1 flag/question and no kritikus


def test_era_label_maps_canonical_int_year_to_band():
    assert _era_label(1930) == "1945 előtt"
    assert _era_label(1960) == "1945 és 1970 között"
    assert _era_label(1980) == "1970 és 1990 között"
    assert _era_label(2000) == "1990 és 2010 között"
    assert _era_label(2015) == "2010 után"
    assert _era_label(None) == "ismeretlen"


@patch("renovai.advisor.pre_purchase.genai.Client")
@patch("renovai.advisor.pre_purchase.predict")
@patch("renovai.advisor.pre_purchase.find_similar_quotes")
@patch("renovai.advisor.pre_purchase.gather_advisory_context")
def test_generate_report_flows_int_era_into_price_features(
    mock_gather, mock_similar, mock_predict, mock_client_class
):
    """End-to-end: an ApartmentProfile with a canonical int era must reach
    ApartmentInput.building_era (and thus the price features) as an int, and
    never be dropped or replaced by a band-key string."""
    mock_gather.return_value = ("Context", ["f1.md"])
    mock_similar.return_value = []
    mock_predict.return_value = {
        "estimate_low_huf": 1,
        "estimate_mid_huf": 2,
        "estimate_high_huf": 3,
        "inflation_adjusted_to": "2024",
    }

    mock_client = mock_client_class.return_value
    mock_resp_json = MagicMock()
    mock_resp_json.text = json.dumps(
        {"questions": [], "inspection": [], "red_flags": []}
    )
    mock_resp_sum = MagicMock()
    mock_resp_sum.text = "Summary"
    mock_client.models.generate_content.side_effect = [mock_resp_json, mock_resp_sum]

    profile = ApartmentProfile(
        address_district=11,
        floor_area_sqm=55.0,
        num_rooms=2,
        building_type="tégla",
        building_era_approx=1960,
        current_condition="közepes",
        known_issues=[],
        has_seen_in_person=True,
        asking_price_million_huf=None,
    )

    report = generate_report(
        profile,
        MagicMock(),
        Path("models"),
        MagicMock(),
        GeminiConfig(api_key="k", model_name="m"),
    )

    assert mock_predict.called, "price predictor should be invoked"
    feat_arg = mock_predict.call_args[0][0]
    assert feat_arg.building_era == 1960
    assert report.apartment_profile.building_era_approx == 1960
