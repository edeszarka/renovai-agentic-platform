"""
Thin wrapper around renovai.advisor.pre_purchase.generate_report for the due_diligence skill.

Called by the orchestrator when the buyer asks for pre-purchase advisory.
Reuses the same underlying implementation as mcp_server/server.py: get_due_diligence_advice.
"""

import sys
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from dotenv import load_dotenv

load_dotenv()

from renovai.advisor.pre_purchase import generate_report, ApartmentProfile
from renovai.rag.vector_store import VectorStoreConfig, RenovAIVectorStore
from renovai.rag.embedder import EmbedderConfig
from renovai.rag.retriever import RetrievalConfig
from renovai.rag.gemini_client import GeminiConfig
from renovai.rag.pipeline import RAGPipeline
from renovai.ingestion.inflation_calc import load_price_index


def run(
    district: int,
    area_sqm: float,
    building_type: str,
    condition: str,
    known_issues: list[str],
) -> dict:
    api_key = os.getenv("GOOGLE_API_KEY", "")

    # Build RAG pipeline
    vs_config = VectorStoreConfig(persist_dir=os.getenv("CHROMA_DB_PATH", "data/chroma_db"))
    vector_store = RenovAIVectorStore(vs_config)
    emb_config = EmbedderConfig(api_key=api_key)
    ret_config = RetrievalConfig()
    gemini_config = GeminiConfig(api_key=api_key)
    rag = RAGPipeline(vector_store, emb_config, ret_config, gemini_config)

    price_index = load_price_index(
        Path("data/raw/inflation/materials_cpi.csv"),
        Path("data/raw/inflation/labor_cpi.csv"),
    )

    profile = ApartmentProfile(
        address_district=district,
        floor_area_sqm=area_sqm,
        num_rooms=0,
        building_type=building_type,
        current_condition=condition,
        known_issues=known_issues,
        has_seen_in_person=False,
        asking_price_million_huf=None,
    )

    report = generate_report(
        profile=profile,
        rag_pipeline=rag,
        price_predictor_model_dir=Path("data/models"),
        price_index=price_index,
        gemini_config=gemini_config,
    )

    return {
        "questions_for_seller": [it.model_dump() for it in report.questions_for_seller],
        "inspection_checklist": [it.model_dump() for it in report.inspection_checklist],
        "red_flags": [it.model_dump() for it in report.red_flags],
        "cost_estimate": report.cost_estimate,
        "overall_risk": report.overall_risk,
        "summary_hu": report.summary_hu,
        "sources_cited": report.rag_sources_used,
    }


if __name__ == "__main__":
    data = json.loads(sys.stdin.read())
    result = run(
        district=data["district"],
        area_sqm=data["area_sqm"],
        building_type=data["building_type"],
        condition=data["condition"],
        known_issues=data.get("known_issues", []),
    )
    print(json.dumps(result, ensure_ascii=False, default=str))
