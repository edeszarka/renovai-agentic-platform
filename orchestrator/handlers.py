"""
Specialized Sub-Agent Handlers for RenovAI 2.0.

Each handler is an async function that:
  1. Receives a structured input dict matching the spec contract
  2. Calls PolicyService.check_structural() before execution
  3. Calls PolicyService.check_semantic() on tool arguments
  4. Loads the relevant skill via SkillRegistry (progressive disclosure)
  5. Returns a structured output dict

Zero Ambient Authority: handlers do not inherit permissions from the
gateway or from each other. Every tool call must pass through policy.
"""

import os
import json
import logging
import subprocess
from pathlib import Path
from typing import Any
from datetime import date

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handler contracts (mirroring specs/cost_estimation.feature)
#
# Every handler returns a dict with at minimum:
#   { "status": "ok" | "error", "data": {...}, "confidence": {...} }
# ---------------------------------------------------------------------------


async def handle_cost_estimation(
    params: dict[str, Any],
    policy_service: Any,
    skill_registry: Any,
    trace_id: str,
) -> dict[str, Any]:
    """
    Cost Estimator handler.

    Input contract:
      { "district": int, "area_sqm": float, "num_rooms": int,
        "scope_flags": {...}, "building_era": str|None,
        "target_date": str|None }

    Output contract:
      { "estimate_low_huf": int, "estimate_mid_huf": int,
        "estimate_high_huf": int, "inflation_adjusted_to": str,
        "similar_quotes": [...], "warnings": [...] }
    """
    # Structural gate
    sr = policy_service.check_structural("cost_estimator", "produce_estimate", trace_id)
    if not sr.passed:
        return {"status": "error", "error": sr.reason, "trace_id": trace_id}

    # Semantic gate on input params
    sem = await policy_service.check_semantic(params, trace_id)
    if not sem.passed:
        return {"status": "error", "error": sem.reason, "trace_id": trace_id}

    # Load skill metadata + instructions (progressive disclosure Layer 0+1)
    skill = skill_registry.get("cost_estimate")
    instructions = ""
    if skill:
        instructions = skill_registry.load_instructions("cost_estimate")

    # Load reference pricing (Layer 3) if skill has it
    pricing_ref = None
    if skill and skill.has_references:
        pricing_path = skill_registry.get_reference_path("cost_estimate", "work_categories.md")
        if pricing_path:
            pricing_ref = pricing_path.read_text(encoding="utf-8")

    # --- Core estimation logic ---
    try:
        from renovai.predictor.price_model import scope_matched_estimate, find_similar_quotes
        from renovai.ingestion.inflation_calc import load_price_index
        from renovai.predictor.feature_extractor import ApartmentInput
    except ImportError:
        return {
            "status": "error",
            "error": "Price model modules not available. Run data pipeline first.",
            "trace_id": trace_id,
        }

    try:
        # Build apartment input
        apt = ApartmentInput(
            district=params.get("district", 1),
            total_area_sqm=params.get("area_sqm", 55.0),
            num_rooms=params.get("num_rooms", 2),
            building_era=params.get("building_era"),
            needs_plumbing=params.get("scope_flags", {}).get("plumbing", False),
            needs_electrical=params.get("scope_flags", {}).get("electrical", False),
            needs_flooring=params.get("scope_flags", {}).get("flooring", False),
            needs_full_demolition=params.get("scope_flags", {}).get("demolition", False),
            suspected_slag=params.get("scope_flags", {}).get("slag", False),
        )

        # Load price index
        data_root = Path(__file__).resolve().parent.parent / "data"
        mat_path = data_root / "raw" / "inflation" / "materials_cpi.csv"
        lab_path = data_root / "raw" / "inflation" / "labor_cpi.csv"
        price_index = load_price_index(mat_path, lab_path)

        target_date = params.get("target_date") or date.today().isoformat()

        # Use scope-matched estimation (DB-backed, preferred over legacy predict)
        from renovai.db.session import get_engine, get_session_maker
        from renovai.db.models import Base

        db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")
        engine = get_engine(db_url)
        session_maker = get_session_maker(engine)

        estimate = await scope_matched_estimate(
            apt, session_maker, price_index, date.fromisoformat(target_date),
        )

        # Find similar quotes
        similar = find_similar_quotes(
            apt, data_root / "processed" / "quotes_json", top_k=3,
        )

        warnings = []
        if len(similar) < 3:
            warnings.append(
                "Figyelem: az adatbázis mindössze 30 idézetet tartalmaz, "
                "ebből {} hasonló található.".format(len(similar))
            )

        return {
            "status": "ok",
            "data": {
                "estimate_low_huf": estimate.get("estimate_low_huf", 0),
                "estimate_mid_huf": estimate.get("estimate_mid_huf", 0),
                "estimate_high_huf": estimate.get("estimate_high_huf", 0),
                "inflation_adjusted_to": target_date,
                "similar_quotes": similar,
                "warnings": warnings,
            },
            "trace_id": trace_id,
        }

    except Exception as exc:
        logger.exception("[%s] Cost estimation failed", trace_id)
        return {
            "status": "error",
            "error": f"Cost estimation failed: {exc}",
            "trace_id": trace_id,
        }


async def handle_ingestion(
    params: dict[str, Any],
    policy_service: Any,
    skill_registry: Any,
    trace_id: str,
) -> dict[str, Any]:
    """
    Ingestion Specialist handler.

    Input contract:
      { "xlsx_path": str, "auto_merge": bool }

    Output contract:
      { "file_name": str, "grand_total_huf": int, "line_items": int,
        "errors": [...] }
    """
    sr = policy_service.check_structural("ingestion", "spawn_sandbox", trace_id)
    if not sr.passed:
        return {"status": "error", "error": sr.reason, "trace_id": trace_id}

    sem = await policy_service.check_semantic(params, trace_id)
    if not sem.passed:
        return {"status": "error", "error": sem.reason, "trace_id": trace_id}

    # Load instructions (progressive disclosure Layer 1)
    instructions = skill_registry.load_instructions("masonry-specialist")

    xlsx_path = Path(params.get("xlsx_path", ""))
    if not xlsx_path.exists():
        return {
            "status": "error",
            "error": f"File not found: {xlsx_path}",
            "trace_id": trace_id,
        }

    try:
        from sandbox.ingest_sandboxed import run_sandboxed, merge_into_db
        from renovai.ingestion.models import RenovationQuote
    except ImportError:
        return {
            "status": "error",
            "error": "Sandbox or ingestion modules not available.",
            "trace_id": trace_id,
        }

    try:
        result = run_sandboxed(xlsx_path)
        if "error" in result:
            return {"status": "error", "error": result["error"], "trace_id": trace_id}

        auto_merge = params.get("auto_merge", False)
        if auto_merge:
            quote = RenovationQuote(**result)
            merge_result = merge_into_db(quote)
            if merge_result.get("status") != "ok":
                return {"status": "error", "error": str(merge_result), "trace_id": trace_id}

        return {
            "status": "ok",
            "data": {
                "file_name": result.get("metadata", {}).get("file_name"),
                "grand_total_huf": result.get("metadata", {}).get("grand_total"),
                "line_items": len(result.get("line_items", [])),
                "warnings": result.get("warnings", []),
            },
            "trace_id": trace_id,
        }

    except Exception as exc:
        logger.exception("[%s] Ingestion failed", trace_id)
        return {
            "status": "error",
            "error": f"Ingestion failed: {exc}",
            "trace_id": trace_id,
        }


async def handle_market_analysis(
    params: dict[str, Any],
    policy_service: Any,
    skill_registry: Any,
    trace_id: str,
) -> dict[str, Any]:
    """
    Market Analyst handler.

    Input contract:
      { "question_hu": str }

    Output contract:
      { "generated_sql": str, "row_count": int, "rows": [...],
        "error": str|None }
    """
    sr = policy_service.check_structural("market_analyst", "execute_sql", trace_id)
    if not sr.passed:
        return {"status": "error", "error": sr.reason, "trace_id": trace_id}

    sem = await policy_service.check_semantic(params, trace_id)
    if not sem.passed:
        return {"status": "error", "error": sem.reason, "trace_id": trace_id}

    try:
        from renovai.db.session import get_engine, get_session_maker
        from renovai.db.text_to_sql import TextToSQLEngine
    except ImportError:
        return {
            "status": "error",
            "error": "Text-to-SQL modules not available.",
            "trace_id": trace_id,
        }

    try:
        engine = get_engine(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db"))
        session_maker = get_session_maker(engine)
        sql_engine = TextToSQLEngine()

        async with session_maker() as session:
            result = await sql_engine.query(params.get("question_hu", ""), session)
            return {
                "status": "ok",
                "data": {
                    "generated_sql": result.get("sql", ""),
                    "row_count": result.get("row_count", 0),
                    "rows": result.get("rows", []),
                },
                "trace_id": trace_id,
            }

    except Exception as exc:
        logger.exception("[%s] Market analysis failed", trace_id)
        return {
            "status": "error",
            "error": f"Market analysis failed: {exc}",
            "trace_id": trace_id,
        }


async def handle_due_diligence(
    params: dict[str, Any],
    policy_service: Any,
    skill_registry: Any,
    trace_id: str,
) -> dict[str, Any]:
    """
    Due Diligence Advisor handler.

    Input contract:
      { "district": int, "area_sqm": float, "num_rooms": int,
        "building_type": str, "building_era": str,
        "condition": str, "known_issues": [str] }

    Output contract:
      { "questions_for_seller": [...], "inspection_checklist": [...],
        "red_flags": [...], "overall_risk": str, "summary_hu": str,
        "sources_cited": [...] }
    """
    sr = policy_service.check_structural("due_diligence", "generate_advisory", trace_id)
    if not sr.passed:
        return {"status": "error", "error": sr.reason, "trace_id": trace_id}

    sem = await policy_service.check_semantic(params, trace_id)
    if not sem.passed:
        return {"status": "error", "error": sem.reason, "trace_id": trace_id}

    # Progressive disclosure: load due_diligence skill instructions
    instructions = skill_registry.load_instructions("due_diligence")

    try:
        from renovai.advisor.pre_purchase import ApartmentProfile, generate_report
        from renovai.rag.pipeline import RAGPipeline
        from renovai.rag.vector_store import RenovAIVectorStore, VectorStoreConfig
        from renovai.rag.embedder import EmbedderConfig, embed_chunks
        from renovai.rag.retriever import RetrievalConfig
        from renovai.rag.gemini_client import GeminiConfig
        from renovai.ingestion.inflation_calc import load_price_index
    except ImportError:
        return {
            "status": "error",
            "error": "RAG or advisor modules not available. Run data pipeline first.",
            "trace_id": trace_id,
        }

    try:
        data_root = Path(__file__).resolve().parent.parent / "data"

        # Build apartment profile
        profile = ApartmentProfile(
            address_district=params.get("district", 1),
            floor_area_sqm=params.get("area_sqm", 55.0),
            num_rooms=params.get("num_rooms", 2),
            building_type=params.get("building_type", "tégla"),
            building_era_approx=params.get("building_era", "1960_1990"),
            current_condition=params.get("condition", "közepes"),
            known_issues=params.get("known_issues", []),
            has_seen_in_person=params.get("has_seen_in_person", False),
            asking_price_million_huf=params.get("asking_price_million_huf"),
        )

        # Init RAG pipeline
        vs_config = VectorStoreConfig(
            persist_dir=str(data_root / "chroma_db"),
            collection_name="renovai_quotes",
        )
        vector_store = RenovAIVectorStore(vs_config)

        embed_config = EmbedderConfig(
            model_name="text-embedding-004",
            api_key=os.getenv("GOOGLE_API_KEY"),
        )
        ret_config = RetrievalConfig(
            n_semantic=5,
            n_keyword=3,
            rerank_top_k=8,
            min_score_threshold=0.3,
        )
        gemini_config = GeminiConfig(
            model_name="gemini-2.5-flash",
            api_key=os.getenv("GOOGLE_API_KEY"),
            temperature=0.3,
        )

        pipeline = RAGPipeline(vector_store, embed_config, ret_config, gemini_config)

        # Load price index
        mat_path = data_root / "raw" / "inflation" / "materials_cpi.csv"
        lab_path = data_root / "raw" / "inflation" / "labor_cpi.csv"
        price_index = load_price_index(mat_path, lab_path)

        # Generate report
        report = generate_report(
            profile=profile,
            rag_pipeline=pipeline,
            model_dir=data_root / "models",
            price_index=price_index,
            gemini_config=gemini_config,
        )

        return {
            "status": "ok",
            "data": {
                "questions_for_seller": [
                    q.model_dump() for q in report.questions_for_seller
                ],
                "inspection_checklist": [
                    c.model_dump() for c in report.inspection_checklist
                ],
                "red_flags": [r.model_dump() for r in report.red_flags],
                "cost_estimate": report.cost_estimate,
                "similar_cases": report.similar_cases,
                "overall_risk": report.overall_risk,
                "summary_hu": report.summary_hu,
                "sources_cited": report.rag_sources_used,
            },
            "trace_id": trace_id,
        }

    except Exception as exc:
        logger.exception("[%s] Due diligence generation failed", trace_id)
        return {
            "status": "error",
            "error": f"Due diligence failed: {exc}",
            "trace_id": trace_id,
        }
