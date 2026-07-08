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


# ---------------------------------------------------------------------------
# Expert Interviewer handler (Tab 1: building-physics due diligence)
# ---------------------------------------------------------------------------

async def handle_expert_interview(
    params: dict[str, Any],
    policy_service: Any,
    skill_registry: Any,
    trace_id: str,
) -> dict[str, Any]:
    """
    Expert Interviewer handler — building-physics inspection and red-flag
    identification for the pre-purchase due diligence Tab 1 workflow.

    Input contract:
      { "district": int, "area_sqm": float, "num_rooms": int,
        "building_type": str, "building_era": str,
        "floor_construction": str|None, "wall_condition": {...},
        "has_seen_in_person": bool }

    Output contract:
      { "red_flags": [...], "overall_risk": str, "summary_hu": str,
        "inspection_checklist": [...], "questions_for_seller": [...],
        "recommended_experts": [...], "confidence": {...} }
    """
    sr = policy_service.check_structural("expert_interviewer", "assess_risk", trace_id)
    if not sr.passed:
        return {"status": "error", "error": sr.reason, "trace_id": trace_id}

    sem = await policy_service.check_semantic(params, trace_id)
    if not sem.passed:
        return {"status": "error", "error": sem.reason, "trace_id": trace_id}

    # Progressive disclosure: load expert-interviewer skill
    instructions = skill_registry.load_instructions("expert-interviewer")

    # Determine building era for red-flag priority logic
    building_era = params.get("building_era", "unknown")
    building_type = params.get("building_type", "unknown")
    floor_construction = params.get("floor_construction", "")
    wall_condition = params.get("wall_condition", {})
    has_slag = params.get("scope_flags", {}).get("slag", False) or "salak" in str(params)

    red_flags: list[dict] = []
    questions: list[str] = []
    checklist: list[str] = []
    recommended_experts: list[str] = []
    risk_factors: list[str] = []

    # Red-flag priority 1: Pre-1960 slag (CRITICAL)
    if has_slag or (building_era and building_era.isdigit() and int(building_era) < 1960):
        red_flags.append({
            "risk": "CRITICAL",
            "title": "Kohósalak a födémben",
            "detail": (
                "Az acél gerendás födém kohósalak kitöltése idővel nedvességet "
                "szív, ami a gerendák korróziójához és a födém süllyedéséhez vezet."
            ),
            "estimated_cost": "3 000 - 5 000 Ft/nm eltávolítás",
            "action": "Statikus szakvélemény és salakmentesítés szükséges.",
        })
        recommended_experts.append("Statikus (statical engineer)")
        questions.append("Van-e ismert salak a födémben? Történt-e már salakmentesítés?")
        checklist.append("Ellenőrizze a pince vagy alulról látható födémszerkezetet")
        risk_factors.append("kohósalak")

    # Red-flag priority 2: 1970s panel aluminium wiring (HIGH)
    if building_type == "panel" and building_era and building_era.isdigit():
        era_year = int(building_era)
        if 1965 <= era_year <= 1985:
            red_flags.append({
                "risk": "HIGH",
                "title": "Alumínium vezetékek a villanyhálózatban",
                "detail": (
                    "A 70-es évek panel épületeiben gyakori az alumínium "
                    "vezeték, amely idővel törékennyé válik és tűzveszélyes."
                ),
                "estimated_cost": "3 000 - 5 000 Ft/nm csere",
                "action": "Villanyszerelői átvizsgálás és teljes vezetékcsere javasolt.",
            })
            recommended_experts.append("Villanyszerelő (electrician)")
            questions.append("Mikor volt utoljára a villanyhálózat felújítva?")
            checklist.append("Kérje el a villanyhálózati dokumentációt")
            risk_factors.append("alumínium vezeték")

    # Red-flag priority 3: Pre-1920 brick foundation (HIGH) - Födém megerősítés
    if building_era and building_era.isdigit() and int(building_era) < 1920:
        red_flags.append({
            "risk": "HIGH",
            "title": "Alapozási kockázat (régi tégla épület)",
            "detail": (
                "Az 1920 előtt épült tégla épületek alapozása gyakran "
                "mészkő vagy tégla alap, ami idővel süllyedhet. "
                "A tégla boltíves acél gerendás födém megerősítése szükséges."
            ),
            "estimated_cost": (
                "Födém megerősítés: ~402 000 Ft anyag, "
                "320 000 - 420 000 Ft munkadíj"
            ),
            "action": (
                "Statikus szakvélemény a födém és alapozás állapotáról. "
                "Tégla boltíves acél gerendás födém esetén erősítés szükséges."
            ),
        })
        recommended_experts.append("Statikus (statical engineer)")
        questions.append("Van-e repedés a teherhordó falakon? Egyenletesek-e a padlók?")
        checklist.append("Ellenőrizze a függőleges és vízszintes repedéseket a falakon")
        risk_factors.append("alapozás")

    # Red-flag: 1920-1965 betontálcás födém (MEDIUM) — walls can start from slab
    if building_era and building_era.isdigit() and 1920 <= int(building_era) <= 1965:
        if "betontálcás" in floor_construction or "beton" in floor_construction:
            red_flags.append({
                "risk": "MEDIUM",
                "title": "Betontálcás födém — falak indíthatók a födémről",
                "detail": (
                    "A betontálcás vasbeton gerendás födém esetén az új falak "
                    "közvetlenül a födémről indíthatók, megerősítés nem szükséges."
                ),
                "estimated_cost": "Nincs plusz költség (megerősítés nem szükséges)",
                "action": (
                    "Beton tálcáról indítható a fal, megerősítés nem szükséges. "
                    "Tervezéskor ezt vegye figyelembe."
                ),
            })
            checklist.append("Ellenőrizze a födém típusát — betontálcás esetén nincs szükség megerősítésre")
            risk_factors.append("betontálcás födém")

    # Red-flag priority 4: Sawdust wallpaper (MEDIUM)
    if wall_condition.get("wallpaper", False):
        red_flags.append({
            "risk": "MEDIUM",
            "title": "Fűrészporos tapéta a falakon",
            "detail": (
                "A fűrészporos tapéta alatt általában nincs vakolat. "
                "Eltávolítása plusz költséggel és Q3 minőségű újravakolással jár."
            ),
            "estimated_cost": "80 000 - 100 000 Ft anyag, 180 000 - 280 000 Ft munkadíj",
            "action": "Tapéta kaparás és Q3 minőségű vakolás szükséges.",
        })
        checklist.append("Koppintson a falakra — üreges hang tapéta alatti vakolathiányra utal")
        risk_factors.append("tapéta")

    # Build confidence
    num_red_flags = len(red_flags)
    if num_red_flags == 0:
        confidence_score = 0.9
        confidence_reasoning = "No red flags detected for this building profile."
    elif num_red_flags <= 2:
        confidence_score = 0.8
        confidence_reasoning = f"{num_red_flags} red flag(s) identified with high-certainty building-era matches."
    else:
        confidence_score = 0.75
        confidence_reasoning = f"{num_red_flags} red flags identified; some based on era defaults rather than confirmed data."

    # Overall risk
    if any(r.get("risk") == "CRITICAL" for r in red_flags):
        overall_risk = "CRITICAL"
    elif any(r.get("risk") == "HIGH" for r in red_flags):
        overall_risk = "HIGH"
    elif red_flags:
        overall_risk = "MEDIUM"
    else:
        overall_risk = "LOW"

    summary_hu = (
        f"A(z) {params.get('area_sqm', '?')} nm-es, "
        f"{params.get('building_type', '?')} építésű lakás "
        f"átvizsgálása {num_red_flags} potenciális kockázatot tárt fel. "
        f"Összesített kockázati szint: {overall_risk}. "
        + ("A legkritikusabb: kohósalak a födémben." if has_slag else "")
    )

    return {
        "status": "ok",
        "data": {
            "red_flags": red_flags,
            "overall_risk": overall_risk,
            "summary_hu": summary_hu,
            "inspection_checklist": checklist,
            "questions_for_seller": questions,
            "recommended_experts": recommended_experts,
            "risk_factors": risk_factors,
            "confidence": {
                "score": confidence_score,
                "reasoning": confidence_reasoning,
            },
        },
        "trace_id": trace_id,
    }


# ---------------------------------------------------------------------------
# Construction Planner handler (Tab 2: technical sequencing & cost logic)
# ---------------------------------------------------------------------------

async def handle_construction_planning(
    params: dict[str, Any],
    policy_service: Any,
    skill_registry: Any,
    trace_id: str,
) -> dict[str, Any]:
    """
    Construction Planner handler — step-by-step renovation sequencing with
    itemized costs per phase.

    Input contract:
      { "area_sqm": float, "building_era": str|None,
        "scope_flags": {"plumbing": bool, "electrical": bool,
                        "flooring": bool, "demolition": bool,
                        "slag": bool, "built_in_shower": bool},
        "wall_condition": {"wallpaper": bool},
        "want_sequence": bool }

    Output contract:
      { "phases": [{"step": int, "name": str, "description": str,
                    "material_cost_range": str, "labor_cost_range": str}],
        "total_estimate": {"low_huf": int, "mid_huf": int, "high_huf": int},
        "warnings": [...], "confidence": {...} }
    """
    sr = policy_service.check_structural("construction_planner", "generate_sequence", trace_id)
    if not sr.passed:
        return {"status": "error", "error": sr.reason, "trace_id": trace_id}

    sem = await policy_service.check_semantic(params, trace_id)
    if not sem.passed:
        return {"status": "error", "error": sem.reason, "trace_id": trace_id}

    # Progressive disclosure: load construction-planner skill
    instructions = skill_registry.load_instructions("construction-planner")

    area_sqm = params.get("area_sqm", 55.0)
    scope = params.get("scope_flags", {})
    wall_condition = params.get("wall_condition", {})
    building_era = params.get("building_era", "")
    floor_construction = params.get("floor_construction", "")

    # Cascading logic: pre-1920 buildings with door work imply slag risk
    has_slag = scope.get("slag", False)
    if not has_slag and building_era and building_era.isdigit() and int(building_era) < 1920:
        if scope.get("door_replacement", False) or scope.get("flooring", False):
            has_slag = True
            warnings.append(
                "1920 előtti épületben az ajtó-/padlómunka kohósalak "
                "veszélyt jelezhet. Statikus vizsgálat javasolt!"
            )

    has_shower = scope.get("built_in_shower", False)
    is_full_renovation = params.get("renovation_scope", "full") == "full"

    phases: list[dict] = []
    warnings: list[str] = []
    total_low = 0
    total_high = 0

    # Phase 0: Slag removal (if applicable)
    if has_slag:
        slag_low = int(3000 * area_sqm)
        slag_high = int(5000 * area_sqm)
        phases.append({
            "step": 0,
            "name": "Salak bontás és elszállítás",
            "description": "Kohósalak eltávolítása a födémből, statikus felügyelet mellett",
            "material_cost_range": "0 Ft (nincs anyagköltség)",
            "labor_cost_range": f"{slag_low:,} - {slag_high:,} Ft",
        })
        total_low += slag_low
        total_high += slag_high
        warnings.append(
            "Salak eltávolítás előtt statikus szakvélemény szükséges!"
        )

    # Phase 1: Demolition (skippable in partial mode)
    if scope.get("demolition", is_full_renovation):
        demo_low = int(1000 * area_sqm)
        demo_high = int(2000 * area_sqm)
        phases.append({
            "step": max(p["step"] for p in phases) + 1 if phases else 1,
            "name": "Bontás (Demolition)",
            "description": "Régi burkolatok, válaszfalak, szerelvények eltávolítása",
            "material_cost_range": "0 - 50 000 Ft",
            "labor_cost_range": f"{demo_low:,} - {demo_high:,} Ft",
        })
        total_low += demo_low + 25000
        total_high += demo_high + 50000

    # Phase 2: Masonry (+ floor reinforcement for pre-1920 with acél gerendás, skippable in partial mode)
    if is_full_renovation or scope.get("masonry", True):
        masonry_low = 249000
        masonry_high = 402000
        mason_labor_low = 280000
        mason_labor_high = 420000

        floor_reinforcement = False
        if building_era and building_era.isdigit() and int(building_era) < 1920:
            if "acél" in floor_construction or "boltíves" in floor_construction or not floor_construction:
                floor_reinforcement = True
                masonry_low = 402000  # Reinforced floor material cost
                masonry_high = 402000
                mason_labor_low = 320000
                mason_labor_high = 420000
                warnings.append(
                    "Födém megerősítés szükséges a gerendák között "
                    "(tégla boltíves acél gerendás födém)!"
                )
        elif building_era and building_era.isdigit() and 1920 <= int(building_era) <= 1965:
            if "betontálcás" in floor_construction or "beton" in floor_construction:
                # No reinforcement needed — walls can start from the slab
                pass

        masonry_desc = (
            "Új falak építése, födém erősítés (acél gerendák között), "
            "ajtónyílások kialakítása"
            if floor_reinforcement else
            "Új falak építése, ajtónyílások kialakítása, betontálcáról indítható"
        )
        phases.append({
            "step": max(p["step"] for p in phases) + 1 if phases else 1,
            "name": "Kőműves (Masonry)",
            "description": masonry_desc,
            "material_cost_range": f"{masonry_low:,} - {masonry_high:,} Ft",
            "labor_cost_range": f"{mason_labor_low:,} - {mason_labor_high:,} Ft",
        })
        total_low += masonry_low + mason_labor_low
        total_high += masonry_high + mason_labor_high

    # Phase 3: Rough-in (plumbing + electrical, skippable in partial mode)
    if is_full_renovation or scope.get("plumbing", False) or scope.get("electrical", False):
        rough_low = int(200000 + 500000)
        rough_high = int(500000 + 1000000)
        phases.append({
            "step": max(p["step"] for p in phases) + 1,
            "name": "Gépészet (Plumbing & Electrical)",
            "description": "Vízvezeték, villanyvezeték, fűtéscsövek elhelyezése",
            "material_cost_range": "200 000 - 500 000 Ft",
            "labor_cost_range": "500 000 - 1 000 000 Ft",
        })
        total_low += rough_low
        total_high += rough_high

    # Phase 4: Plastering (with wallpaper surcharge if applicable, skippable in partial mode)
    if is_full_renovation or scope.get("plastering", True):
        plaster_material = 80000
        plaster_labor_low = 180000
        plaster_labor_high = 380000
        if wall_condition.get("wallpaper", False):
            plaster_material += 100000  # scraping surcharge
            plaster_labor_low += 180000
            plaster_labor_high += 280000
            warnings.append("Fűrészporos tapéta miatt kaparás és Q3 vakolás szükséges!")
        phases.append({
            "step": max(p["step"] for p in phases) + 1,
            "name": "Vakolás és glettelés (Plastering)",
            "description": (
                "Tapéta kaparás, csiszolás, vakolás, glettelés"
                if wall_condition.get("wallpaper", False)
                else "Csiszolás, vakolás, glettelés"
            ),
            "material_cost_range": f"{plaster_material:,} - {plaster_material + 100000:,} Ft",
            "labor_cost_range": f"{plaster_labor_low:,} - {plaster_labor_high:,} Ft",
        })
        total_low += plaster_material + plaster_labor_low
        total_high += (plaster_material + 100000) + plaster_labor_high

    # Phase 5: Flooring (with optional waterproofing upgrade, skippable in partial mode)
    if is_full_renovation or scope.get("flooring", True):
        floor_material = 130000
        floor_labor_low = 300000
        floor_labor_high = 600000
        if has_shower:
            floor_material += 130000
            floor_labor_low += 50000
            floor_labor_high += 80000
            warnings.append("Épített zuhany miatt cementbázisú szigetelés szükséges!")
        phases.append({
            "step": max(p["step"] for p in phases) + 1,
            "name": "Burkolás (Flooring & Tiling)",
            "description": (
                "Csempe/járólap burkolás, cementbázisú vízszigetelés"
                if has_shower
                else "Csempe/járólap burkolás"
            ),
            "material_cost_range": f"{floor_material:,} - {floor_material + 120000:,} Ft",
            "labor_cost_range": f"{floor_labor_low:,} - {floor_labor_high:,} Ft",
        })
        total_low += floor_material + floor_labor_low
        total_high += (floor_material + 120000) + floor_labor_high

    # Phase 6: Painting & fixtures (skippable in partial mode)
    if is_full_renovation or scope.get("painting", True):
        paint_material = 50000
        paint_labor_low = 200000
        paint_labor_high = 400000
        phases.append({
            "step": max(p["step"] for p in phases) + 1,
            "name": "Festés és szerelés (Painting & Fixtures)",
            "description": "Falfestés, kapcsolók, dugaljak, lámpák, ajtók szerelése",
            "material_cost_range": f"{paint_material:,} - {paint_material + 50000:,} Ft",
            "labor_cost_range": f"{paint_labor_low:,} - {paint_labor_high:,} Ft",
        })
        total_low += paint_material + paint_labor_low
        total_high += (paint_material + 50000) + paint_labor_high

    total_mid = (total_low + total_high) // 2

    return {
        "status": "ok",
        "data": {
            "phases": phases,
            "total_estimate": {
                "low_huf": total_low,
                "mid_huf": total_mid,
                "high_huf": total_high,
            },
            "warnings": warnings,
            "confidence": {
                "score": 0.8 if not has_slag else 0.75,
                "reasoning": (
                    "Construction plan based on area-scaled corpus averages. "
                    "Actual costs depend on exact building conditions."
                ),
            },
        },
        "trace_id": trace_id,
    }


# ---------------------------------------------------------------------------
# Handler resolver
# ---------------------------------------------------------------------------

HANDLER_MAP: dict[str, Any] = {
    "cost_estimation": handle_cost_estimation,
    "ingestion": handle_ingestion,
    "market_analysis": handle_market_analysis,
    "due_diligence": handle_due_diligence,
    "expert_interview": handle_expert_interview,
    "construction_planning": handle_construction_planning,
}


def resolve_handler(intent: str) -> Any:
    """Resolve a canonical intent name to its async handler function."""
    handler = HANDLER_MAP.get(intent)
    if handler is None:
        raise ValueError(f"Unknown intent: '{intent}'. Available: {list(HANDLER_MAP.keys())}")
    return handler
