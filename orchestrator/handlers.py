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

from renovai.safety.green_team import GreenTeamService

logger = logging.getLogger(__name__)


async def _green_team_gate(
    green_team_service: GreenTeamService,
    trace_id: str,
    user_intent: str,
    proposed_action: str,
    confidence: float,
    semantic_risk: str,
    proposed_response: dict[str, Any],
) -> dict[str, Any]:
    """
    Run the Green Team human-in-the-loop gate for a handler result.

    Evaluates the existing confidence / semantic-risk signals and returns the
    payload to merge into the handler's response so the UI can render a
    "needs review" state instead of a plain number.

    Returns:
        ``{"needs_intervention": bool}`` and, when intervention is required, a
        nested ``"green_team"`` dict with the approval-request metadata
        (request_id, risk_level, trigger_reason).
    """
    decision = await green_team_service.evaluate(
        trace_id=trace_id,
        user_intent=user_intent,
        confidence=confidence,
        proposed_action=proposed_action,
        proposed_response=proposed_response,
        semantic_risk=semantic_risk,
    )
    payload: dict[str, Any] = {"needs_intervention": decision.needs_intervention}
    if decision.needs_intervention and decision.request is not None:
        payload["green_team"] = {
            "request_id": decision.request.request_id,
            "risk_level": decision.request.risk_level,
            "trigger_reason": decision.request.trigger_reason,
        }
    return payload


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
    green_team_service: GreenTeamService | None = None,
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
        "similar_quotes": [...], "warnings": [...],
        "needs_intervention": bool }
    """
    # Structural gate
    sr = policy_service.check_structural("cost_estimator", "produce_estimate", trace_id)
    if not sr.passed:
        return {"status": "error", "error": sr.reason, "trace_id": trace_id}

    # Semantic gate on input params
    sem = await policy_service.check_semantic(
        params, trace_id, role="cost_estimator", action="produce_estimate",
    )
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
        from renovai.predictor.feature_extractor import ApartmentInput, apartment_input_to_features
    except ImportError:
        return {
            "status": "error",
            "error": "Price model modules not available. Run data pipeline first.",
            "trace_id": trace_id,
        }

    try:
        scope_flags = params.get("scope_flags", {})
        renovation_scope = params.get("renovation_scope", "full")
        is_full = renovation_scope == "full" or all(
            scope_flags.get(k, False) for k in ("demolition", "electrical", "plumbing")
        )

        # Full renovation always includes AC
        if is_full:
            scope_flags.setdefault("ac", True)

        # Build apartment input
        apt = ApartmentInput(
            district=params.get("district", 1),
            total_area_sqm=params.get("area_sqm", 55.0),
            num_rooms=params.get("num_rooms", 2),
            building_era=params.get("building_era"),
            needs_plumbing=scope_flags.get("plumbing", False),
            needs_electrical=scope_flags.get("electrical", False),
            needs_flooring=scope_flags.get("flooring", False),
            needs_full_demolition=scope_flags.get("demolition", False),
            needs_windows_doors=scope_flags.get("windows_doors", False),
            needs_insulation=scope_flags.get("insulation", False),
            needs_ac=scope_flags.get("ac", False),
            suspected_slag=scope_flags.get("slag", False),
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
            apartment_input_to_features(apt), data_root / "processed" / "quotes_json", top_k=3,
        )

        warnings = []
        if len(similar) < 3:
            warnings.append(
                "Figyelem: az adatbázis mindössze 30 idézetet tartalmaz, "
                "ebből {} hasonló található.".format(len(similar))
            )

        # --- Structural cost drivers (on top of corpus-based estimate) ---
        from renovai.predictor.structural_cost import (
            ceiling_height_multiplier, apply_height_surcharge,
            detect_active_chains, chain_total_cost,
            apply_infrastructure_minimums,
            compute_logistics_surcharge, elevator_surcharge,
            chimney_technician_cost,
        )

        scope = params.get("scope_flags", {})
        area_sqm = params.get("area_sqm", 55.0)
        building_era = params.get("building_era")
        ceiling_height = params.get("ceiling_height")
        elevator_type = params.get("elevator_type")
        gas_heating = params.get("gas_heating", False)
        floor_number = params.get("floor_number", 1)
        renovation_scope = params.get("renovation_scope", "full")

        # Adjustments breakdown for Vibe Diff
        adjustments: dict[str, Any] = {
            "height_surcharge_huf": 0,
            "chain_cost_huf": 0,
            "infra_minimums_huf": 0,
            "logistics_surcharge_huf": 0,
            "elevator_surcharge_huf": 0,
            "chimney_technician_huf": 0,
        }

        base_low = estimate.get("estimate_low_huf", 0)
        base_mid = estimate.get("estimate_mid_huf", 0)
        base_high = estimate.get("estimate_high_huf", 0)

        # 1 — Height multiplier: only applies to painting/plastering labor
        # Painting/plastering is ~15% of total mid estimate, ~70% of that is labor
        painting_share = int(base_mid * 0.15)
        painting_labor = int(painting_share * 0.70)
        adj_labor, height_surcharge = apply_height_surcharge(
            ceiling_height, area_sqm, painting_labor,
        )
        base_low += int(height_surcharge * 0.85)
        base_mid += height_surcharge
        base_high += int(height_surcharge * 1.20)
        adjustments["height_surcharge_huf"] = height_surcharge

        # 2 — Cascading cost chains (area-scaled: materials like EPS, concrete, flooring scale with m²)
        active_chains = detect_active_chains(scope, building_era, floor_number, gas_heating)
        chain_costs = chain_total_cost(
            active_chains, area_sqm=area_sqm,
            target_date=date.fromisoformat(target_date), price_index=price_index,
        )
        chain_delta = chain_costs["point"]
        if chain_delta:
            base_low += int(chain_costs["low"] * 0.85)
            base_mid += chain_delta
            base_high += int(chain_costs["high"] * 1.20)
        adjustments["chain_cost_huf"] = chain_delta

        # 3 — Infrastructure minimums
        is_full = renovation_scope == "full" or all(
            scope.get(k, False) for k in ("demolition", "electrical", "plumbing")
        )
        min_low, min_mid, min_high, min_logs = apply_infrastructure_minimums(
            base_low, base_mid, base_high, is_full, gas_heating,
            target_date=date.fromisoformat(target_date), price_index=price_index,
        )
        infra_delta = min_mid - base_mid
        base_low, base_mid, base_high = min_low, min_mid, min_high
        adjustments["infra_minimums_huf"] = infra_delta
        warnings.extend(min_logs)

        # 4 — Logistics surcharge (15% of total labor)
        # Total labor is ~55% of mid estimate
        total_labor = int(base_mid * 0.55)
        log_surcharge = int(compute_logistics_surcharge(total_labor))
        if log_surcharge:
            base_low += int(log_surcharge * 0.85)
            base_mid += log_surcharge
            base_high += int(log_surcharge * 1.20)
        adjustments["logistics_surcharge_huf"] = log_surcharge

        # 5 — Elevator surcharge
        elev_surcharge = int(elevator_surcharge(elevator_type, floor_number))
        if elev_surcharge:
            base_low += int(elev_surcharge * 0.85)
            base_mid += elev_surcharge
            base_high += int(elev_surcharge * 1.20)
            warnings.append(
                f"Lift hiánya miatt logisztikai pótlék: {elev_surcharge:,} Ft "
                "(szakértői becslés — pontosítandó)."
            )
        adjustments["elevator_surcharge_huf"] = elev_surcharge

        # 6 — Chimney technician (conditional, gas heating only)
        chim_cost = (
            chimney_technician_cost(
                floor_number,
                target_date=date.fromisoformat(target_date), price_index=price_index,
            ) if gas_heating else 0
        )
        if chim_cost:
            base_low += int(chim_cost * 0.85)
            base_mid += chim_cost
            base_high += int(chim_cost * 1.20)
            warnings.append(
                f"Kéménytechnikai szakember bevonása: +{chim_cost:,} Ft "
                "(egyedi gázfűtés miatt)."
            )
        adjustments["chimney_technician_huf"] = chim_cost

        # Inflation is already resolved per-quote inside scope_matched_estimate().
        # Structural add-ons (height, chains, infra, logistics, elevator, chimney)
        # are computed in current-day HUF and are NOT inflated again.

        # Build adjustment breakdown for Vibe Diff
        total_adjustments = sum(v for v in adjustments.values())
        adj_parts = []
        for k, v in adjustments.items():
            if v:
                adj_parts.append(f"{k}={v:,}Ft")
        adj_summary = "Structural adjustments: " + ", ".join(adj_parts) if adj_parts else "No structural adjustments applied."

        base_estimate_out = {
            "estimate_low_huf": estimate.get("estimate_low_huf", 0),
            "estimate_mid_huf": estimate.get("estimate_mid_huf", 0),
            "estimate_high_huf": estimate.get("estimate_high_huf", 0),
        }

        # --- Green Team human-in-the-loop gate --------------------------------
        # Confidence reuses the existing data-scarcity signal the handler already
        # produces (the "< 3 similar quotes" warning). This mirrors the legacy
        # FastAPI model_confidence invariant ("high" when >= 3 similar quotes).
        # The estimate numbers above are untouched; only gate metadata is added.
        if green_team_service is None:
            green_team_service = GreenTeamService()
        num_similar = len(similar)
        if num_similar >= 3:
            model_confidence = 0.85
        elif num_similar >= 1:
            model_confidence = 0.60
        else:
            model_confidence = 0.45
        _green_team = await _green_team_gate(
            green_team_service,
            trace_id=trace_id,
            user_intent="cost_estimation",
            proposed_action="produce_estimate",
            confidence=model_confidence,
            semantic_risk="low",
            proposed_response={
                "estimate_mid_huf": base_mid,
                "num_similar_quotes": num_similar,
                "warnings": warnings,
            },
        )

        return {
            "status": "ok",
            "data": {
                "estimate_low_huf": base_low,
                "estimate_mid_huf": base_mid,
                "estimate_high_huf": base_high,
                "inflation_adjusted_to": target_date,
                "inflation_factor_range": estimate.get("inflation_factor_range", {}),
                "similar_quotes": similar,
                "warnings": warnings,
                "base_estimate": base_estimate_out,
                "adjustments": adjustments,
                "adjustment_summary": adj_summary,
                "active_chains": [
                    {"id": c["id"], "cost_point": c["base_cost_point"] + c["per_sqm_cost_point"] * area_sqm, "description": c["description"]}
                    for c in active_chains
                ],
                **_green_team,
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

    sem = await policy_service.check_semantic(
        params, trace_id, role="ingestion", action="spawn_sandbox",
    )
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

    sem = await policy_service.check_semantic(
        params, trace_id, role="market_analyst", action="execute_sql",
    )
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
    green_team_service: GreenTeamService | None = None,
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
        "sources_cited": [...], "needs_intervention": bool }
    """
    sr = policy_service.check_structural("due_diligence", "generate_advisory", trace_id)
    if not sr.passed:
        return {"status": "error", "error": sr.reason, "trace_id": trace_id}

    sem = await policy_service.check_semantic(
        params, trace_id, role="due_diligence", action="generate_advisory",
    )
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

        # --- Green Team human-in-the-loop gate --------------------------------
        # Reuse the existing semantic risk level (report.overall_risk); there is
        # no confidence score in this path, so a neutral baseline confidence is
        # passed and the risk level is the driving signal.
        if green_team_service is None:
            green_team_service = GreenTeamService()
        _green_team = await _green_team_gate(
            green_team_service,
            trace_id=trace_id,
            user_intent="due_diligence",
            proposed_action="generate_advisory",
            confidence=0.90,
            semantic_risk={
                "alacsony": "low", "közepes": "medium", "magas": "high",
            }.get(report.overall_risk, "low"),
            proposed_response={"overall_risk": report.overall_risk},
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
                **_green_team,
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
    green_team_service: GreenTeamService | None = None,
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
        "recommended_experts": [...], "confidence": {...},
        "needs_intervention": bool }
    """
    sr = policy_service.check_structural("expert_interviewer", "assess_risk", trace_id)
    if not sr.passed:
        return {"status": "error", "error": sr.reason, "trace_id": trace_id}

    sem = await policy_service.check_semantic(
        params, trace_id, role="expert_interviewer", action="assess_risk",
    )
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

    # --- Green Team human-in-the-loop gate --------------------------------
    # Reuse the existing computed risk level (overall_risk) and confidence
    # score already produced above.
    if green_team_service is None:
        green_team_service = GreenTeamService()
    _green_team = await _green_team_gate(
        green_team_service,
        trace_id=trace_id,
        user_intent="expert_interview",
        proposed_action="assess_risk",
        confidence=confidence_score,
        semantic_risk={
            "LOW": "low", "MEDIUM": "medium", "HIGH": "high", "CRITICAL": "critical",
        }.get(overall_risk, "low"),
        proposed_response={"overall_risk": overall_risk, "num_red_flags": num_red_flags},
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
            **_green_team,
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
    target_date: date | None = None,
    price_index: Any | None = None,
) -> dict[str, Any]:
    """
    Construction Planner handler — step-by-step renovation sequencing with
    itemized costs per phase.

    Input contract:
      { "area_sqm": float, "building_era": str|None,
        "scope_flags": {...},
        "wall_condition": {"wallpaper": bool},
        "ceiling_height": float|None, "elevator_type": str|None,
        "gas_heating": bool, "floor_number": int,
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

    sem = await policy_service.check_semantic(
        params, trace_id, role="construction_planner", action="generate_sequence",
    )
    if not sem.passed:
        return {"status": "error", "error": sem.reason, "trace_id": trace_id}

    # Progressive disclosure: load construction-planner skill
    instructions = skill_registry.load_instructions("construction-planner")

    # Dated structural add-ons: 2025 expert-reference constants are inflated
    # to target_date below (Item F). Default to today; load price_index the
    # same way handle_cost_estimation() does when not already supplied.
    if target_date is None:
        target_date = date.today()
    if price_index is None:
        from renovai.ingestion.inflation_calc import load_price_index

        data_root = Path(__file__).resolve().parent.parent / "data"
        price_index = load_price_index(
            data_root / "raw" / "inflation" / "materials_cpi.csv",
            data_root / "raw" / "inflation" / "labor_cpi.csv",
        )

    area_sqm = params.get("area_sqm", 55.0)
    scope = params.get("scope_flags", {})
    wall_condition = params.get("wall_condition", {})
    building_era = params.get("building_era", "")
    floor_construction = params.get("floor_construction", "")

    # New structural cost driver params
    ceiling_height = params.get("ceiling_height")
    elevator_type = params.get("elevator_type")
    gas_heating = params.get("gas_heating", False)
    floor_number = params.get("floor_number", 1)

    has_shower = scope.get("built_in_shower", False)
    is_full_renovation = params.get("renovation_scope", "full") == "full"
    era_int = int(building_era) if building_era and building_era.isdigit() else 9999
    floor_work_requested = scope.get("flooring", False) or scope.get("demolition", False)
    slag_from_flags = scope.get("slag", False)
    if not slag_from_flags and era_int < 1960 and floor_work_requested:
        slag_from_flags = True

    # Item G1: call the corpus-based estimator to source per-category costs.
    # Build an ApartmentInput from plan_params and run scope_matched_estimate.
    from renovai.predictor.feature_extractor import ApartmentInput

    plan_apt = ApartmentInput(
        district=params.get("district", 5),
        total_area_sqm=area_sqm,
        num_rooms=params.get("num_rooms", 2),
        building_era=era_int if era_int != 9999 else None,
        needs_plumbing=scope.get("plumbing", is_full_renovation),
        needs_electrical=scope.get("electrical", is_full_renovation),
        needs_flooring=scope.get("flooring", is_full_renovation),
        needs_full_demolition=scope.get("demolition", is_full_renovation),
        needs_windows_doors=scope.get("windows_doors", is_full_renovation),
        needs_insulation=scope.get("insulation", is_full_renovation),
        needs_ac=scope.get("ac", is_full_renovation),
        suspected_slag=slag_from_flags,
    )
    try:
        from renovai.predictor.price_model import scope_matched_estimate
        from renovai.db.session import get_engine, get_session_maker

        db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")
        estimator_engine = get_engine(db_url)
        estimator_sm = get_session_maker(estimator_engine)
        plan_est = await scope_matched_estimate(
            plan_apt, estimator_sm, price_index, target_date, include_breakdown=True,
        )
        categories = plan_est.get("categories", {}) if plan_est else {}
    except Exception as exc:
        logger.warning("[%s] scope_matched_estimate failed: %s — falling back to hardcoded", trace_id, exc)
        categories = {}
        plan_est = None

    # Helper: get phase costs from a scope's categories entry.
    # Falls back to None if categories unavailable; caller provides fallback.
    # Material/labor split uses the codebase-standard 55/45 ratio.
    _LABOR_SHARE = 0.55

    def _phase_from_scope(scope_name: str):
        """Return (mat_low, mat_high, lab_low, lab_high, data_source) from corpus."""
        c = categories.get(scope_name, {})
        if not c or not c.get("estimate_huf"):
            return None, None, None, None, "hardcoded_2025"
        el = c["estimate_huf"]["low"]
        em = c["estimate_huf"]["mid"]
        eh = c["estimate_huf"]["high"]
        mat_lo = int(el * (1.0 - _LABOR_SHARE))
        mat_hi = int(eh * (1.0 - _LABOR_SHARE))
        lab_lo = int(el * _LABOR_SHARE)
        lab_hi = int(eh * _LABOR_SHARE)
        if c.get("fallback_used"):
            ds = "corpus_fallback"
        else:
            ds = "corpus"
        return mat_lo, mat_hi, lab_lo, lab_hi, ds

    # Sparse-category corpus support tracking
    # Categories with < 2 quotes in the DB corpus get a data-limitation warning
    SPARSE_FLAGS = {"insulation", "windows_doors", "kitchen", "bathroom", "drywall", "ac", "heating"}

    from renovai.predictor.structural_cost import (
        ceiling_height_multiplier, apply_height_surcharge,
        detect_active_chains, chain_total_cost,
        apply_infrastructure_minimums,
        compute_logistics_surcharge, elevator_surcharge,
        chimney_technician_cost,
    )

    phases: list[dict] = []
    warnings: list[str] = []

    # Cascading logic: pre-1960 buildings with floor work trigger full Slag Chain
    # (era_int, floor_work_requested, slag_from_flags computed above for ApartmentInput)
    has_slag = slag_from_flags
    if not scope.get("slag", False) and era_int < 1960 and floor_work_requested:
        has_slag = True
        warnings.append(
            "1960 előtti épületben a padlómunka kohósalak "
            "láncreakciót indíthat el. Statikus vizsgálat kötelező!"
        )
    total_low = 0
    total_high = 0
    total_labor_low = 0
    total_labor_high = 0

    # Phase 0: Full Slag Chain — aggregated block (triggered if era < 1960 AND floor work)
    if has_slag:
        slag_labor_low = 1_300_000
        slag_labor_high = 2_000_000
        slag_eps_low = 700_000
        slag_eps_high = 850_000
        slag_concrete_per_sqm_low = 6_000
        slag_concrete_per_sqm_high = 9_000
        slag_concrete_low = int(slag_concrete_per_sqm_low * area_sqm)
        slag_concrete_high = int(slag_concrete_per_sqm_high * area_sqm)
        slag_mat_total_low = slag_eps_low + slag_concrete_low
        slag_mat_total_high = slag_eps_high + slag_concrete_high
        slag_labor_total_low = slag_labor_low
        slag_labor_total_high = slag_labor_high

        phases.append({
            "step": 0,
            "name": "Kohósalak lánc — Teljes salakmentesítés",
            "description": (
                "Teljes kohósalak lánc: salak eltávolítás, EPS szigetelés, "
                "betonozás és szintezés. Statikus felügyelet mellett."
            ),
            "material_cost_range": f"{slag_mat_total_low:,} - {slag_mat_total_high:,} Ft",
            "labor_cost_range": f"{slag_labor_total_low:,} - {slag_labor_total_high:,} Ft",
            "slag_breakdown": {
                "removal_labor": f"{slag_labor_low:,} - {slag_labor_high:,} Ft",
                "eps_material": f"{slag_eps_low:,} - {slag_eps_high:,} Ft",
                "concrete_leveling": f"{slag_concrete_low:,} - {slag_concrete_high:,} Ft  ({slag_concrete_per_sqm_low:,} - {slag_concrete_per_sqm_high:,} Ft/nm)",
            },
            "data_source": "hardcoded_2025",
        })
        total_low += slag_mat_total_low + slag_labor_total_low
        total_high += slag_mat_total_high + slag_labor_total_high
        total_labor_low += slag_labor_total_low
        total_labor_high += slag_labor_total_high
        warnings.append(
            "Salak lánc: eltávolítás → EPS → betonozás. "
            "Statikus szakvélemény kötelező!"
        )

    # Chain: Ajtó/Padló-lánc (triggers for pre-1970 + windows_doors/flooring)
    active_chains = detect_active_chains(scope, building_era, floor_number, gas_heating)
    chain_costs = chain_total_cost(
        active_chains, area_sqm=area_sqm,
        target_date=target_date, price_index=price_index,
    )
    if chain_costs["point"]:
        chain = active_chains[0]
        phases.append({
            "step": max(p["step"] for p in phases) + 1 if phases else 1,
            "name": "Ajtó/Padló lánc — Teljes aljzatfelújítás",
            "description": " → ".join(chain["steps"]),
            "material_cost_range": f"{chain_costs['low']:,} - {chain_costs['high']:,} Ft",
            "labor_cost_range": "0 Ft (lánc anyagköltségben benne)",
            "chain_id": chain["id"],
            "data_source": "hardcoded_2025",
        })
        total_low += chain_costs["low"]
        total_high += chain_costs["high"]
        warnings.append(
            f"{chain['description']} (szakértői adat, korpusz által nem validált)."
        )

    # Phase 1: Demolition (skippable in partial mode) — corpus-wired via needs_full_demolition
    if scope.get("demolition", is_full_renovation):
        dm_l, dm_h, dl_l, dl_h, dd_src = _phase_from_scope("needs_full_demolition")
        if dm_l is not None:
            demo_mat_low, demo_mat_high = dm_l, dm_h
            demo_lab_low, demo_lab_high = dl_l, dl_h
        else:
            demo_mat_low, demo_mat_high = 25000, 50000
            demo_lab_low = int(1000 * area_sqm)
            demo_lab_high = int(2000 * area_sqm)
            dd_src = "hardcoded_2025"
        phases.append({
            "step": max(p["step"] for p in phases) + 1 if phases else 1,
            "name": "Bontás (Demolition)",
            "description": "Régi burkolatok, válaszfalak, szerelvények eltávolítása",
            "material_cost_range": f"{demo_mat_low:,} - {demo_mat_high:,} Ft",
            "labor_cost_range": f"{demo_lab_low:,} - {demo_lab_high:,} Ft",
            "data_source": dd_src,
        })
        total_low += demo_mat_low + demo_lab_low
        total_high += demo_mat_high + demo_lab_high
        total_labor_low += demo_lab_low
        total_labor_high += demo_lab_high

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
            "data_source": "hardcoded_2025",
        })
        total_low += masonry_low + mason_labor_low
        total_high += masonry_high + mason_labor_high
        total_labor_low += mason_labor_low
        total_labor_high += mason_labor_high

    # Phase 2b: Drywall / suspended ceiling (optional, after masonry)
    if is_full_renovation or scope.get("drywall", False):
        dw_mat_low = 5000
        dw_mat_high = 6200
        dw_lab_low = 9500
        dw_lab_high = 11500
        dw_mat_total_low = int(dw_mat_low * area_sqm)
        dw_mat_total_high = int(dw_mat_high * area_sqm)
        dw_lab_total_low = int(dw_lab_low * area_sqm)
        dw_lab_total_high = int(dw_lab_high * area_sqm)
        phases.append({
            "step": max(p["step"] for p in phases) + 1 if phases else 1,
            "name": "Gipszkarton és álmennyezet (Drywall & Ceiling)",
            "description": "Gipszkarton falazás, álmennyezet kialakítása, CD profil vázszerkezet",
            "material_cost_range": f"{dw_mat_total_low:,} - {dw_mat_total_high:,} Ft",
            "labor_cost_range": f"{dw_lab_total_low:,} - {dw_lab_total_high:,} Ft",
            "data_source": "hardcoded_2025",
        })
        total_low += dw_mat_total_low + dw_lab_total_low
        total_high += dw_mat_total_high + dw_lab_total_high
        total_labor_low += dw_lab_total_low
        total_labor_high += dw_lab_total_high

    # Phase 2c: Windows / doors replacement — stays unit-count-based hardcoded.
    # The SCOPE_CATEGORY_MAP fallback (min_premium / area) treats windows/doors
    # as area-scaled, but window count scales with room count, not floor area.
    # Revert to the old per-unit logic until real nyílászáró corpus data exists
    # (post-re-tagging), at which point the corpus path would use per-sqm via
    # needs_windows_doors in the categories dict.
    if is_full_renovation or scope.get("windows_doors", False):
        win_per_unit_low = 200000
        win_per_unit_high = 400000
        num_units = max(1, int(area_sqm / 15))
        win_mat_low = win_per_unit_low * num_units
        win_mat_high = win_per_unit_high * num_units
        win_lab_low = 25000 * num_units
        win_lab_high = 35000 * num_units
        phases.append({
            "step": max(p["step"] for p in phases) + 1 if phases else 1,
            "name": "Nyílászáró csere (Windows & Doors)",
            "description": f"Új ablakok ({num_units} db) és beltéri ajtók cseréje, tokok beépítése",
            "material_cost_range": f"{win_mat_low:,} - {win_mat_high:,} Ft",
            "labor_cost_range": f"{win_lab_low:,} - {win_lab_high:,} Ft",
            "data_source": "hardcoded_2025",
        })
        total_low += win_mat_low + win_lab_low
        total_high += win_mat_high + win_lab_high
        total_labor_low += win_lab_low
        total_labor_high += win_lab_high
        warnings.append(
            "Nyílászáró csere: az új ablakok és ajtók beépítése a kőműves "
            "munka után, a gépészet előtt történjen."
        )

    # Phase 3: Rough-in (MEP) — corpus-wired from plumbing + electrical + AC.
    # Heating is NOT in SCOPE_CATEGORY_MAP yet; stays hardcoded.
    has_rough_in = (
        is_full_renovation
        or scope.get("plumbing", False)
        or scope.get("electrical", False)
        or scope.get("heating", False)
        or scope.get("ac", False)
    )
    if has_rough_in:
        # Sum corpus contributions from plumbing + electrical + AC
        pm_l, pm_h, pl_l, pl_h, _ = _phase_from_scope("needs_plumbing")
        em_l, em_h, el_l, el_h, _ = _phase_from_scope("needs_electrical")
        am_l, am_h, al_l, al_h, _ = _phase_from_scope("needs_ac")
        rough_mat_low = (pm_l or 0) + (em_l or 0) + (am_l or 0)
        rough_mat_high = (pm_h or 0) + (em_h or 0) + (am_h or 0)
        rough_lab_low = (pl_l or 0) + (el_l or 0) + (al_l or 0)
        rough_lab_high = (pl_h or 0) + (el_h or 0) + (al_h or 0)

        # Determine combined data_source: corpus if any scope uses corpus,
        # corpus_fallback if any uses fallback but none are corpus,
        # otherwise hardcoded_2025
        srcs = set(
            _phase_from_scope(s)[4]
            for s in ("needs_plumbing", "needs_electrical", "needs_ac")
            if categories.get(s)
        )
        if "corpus" in srcs:
            rough_src = "corpus"
        elif "corpus_fallback" in srcs:
            rough_src = "corpus_fallback"
        else:
            rough_src = "hardcoded_2025"

        desc_parts = ["Vízvezeték", "villanyvezeték"]
        if scope.get("heating", is_full_renovation):
            rough_mat_low += 300000
            rough_mat_high += 600000
            rough_lab_low += 300000
            rough_lab_high += 500000
            desc_parts.append("fűtéscsövek/radiátorok")
        if scope.get("ac", False):
            desc_parts.append("klíma előkészítés")
        phases.append({
            "step": max(p["step"] for p in phases) + 1,
            "name": "Gépészet (Plumbing, Electrical, HVAC)",
            "description": " és ".join(desc_parts) + " elhelyezése, szerelése",
            "material_cost_range": f"{rough_mat_low:,} - {rough_mat_high:,} Ft",
            "labor_cost_range": f"{rough_lab_low:,} - {rough_lab_high:,} Ft",
            "data_source": rough_src,
        })
        total_low += rough_mat_low + rough_lab_low
        total_high += rough_mat_high + rough_lab_high
        total_labor_low += rough_lab_low
        total_labor_high += rough_lab_high

    # Phase 3b: Insulation — corpus-wired via needs_insulation (fallback-only)
    if is_full_renovation or scope.get("insulation", False):
        im_l, im_h, il_l, il_h, in_src = _phase_from_scope("needs_insulation")
        if im_l is not None:
            ins_mat_low, ins_mat_high = im_l, im_h
            ins_lab_low, ins_lab_high = il_l, il_h
        else:
            ins_mat_low = int(2500 * area_sqm)
            ins_mat_high = int(5000 * area_sqm)
            ins_lab_low = int(2000 * area_sqm)
            ins_lab_high = int(4000 * area_sqm)
            in_src = "hardcoded_2025"
        phases.append({
            "step": max(p["step"] for p in phases) + 1 if phases else 1,
            "name": "Szigetelés (Insulation)",
            "description": "Hőszigetelés és/vagy hangszigetelés, párazáró fólia, EPS/ásványgyapot",
            "material_cost_range": f"{ins_mat_low:,} - {ins_mat_high:,} Ft",
            "labor_cost_range": f"{ins_lab_low:,} - {ins_lab_high:,} Ft",
            "data_source": in_src,
        })
        total_low += ins_mat_low + ins_lab_low
        total_high += ins_mat_high + ins_lab_high
        total_labor_low += ins_lab_low
        total_labor_high += ins_lab_high

    # Phase 4: Plastering (with wallpaper surcharge if applicable, skippable in partial mode)
    if is_full_renovation or scope.get("plastering", True):
        plaster_material = 80000
        plaster_labor_low = 180000
        plaster_labor_high = 380000
        # Apply ceiling-height multiplier to plastering labor (vertical surface trade)
        if ceiling_height and ceiling_height > 2.75:
            adj_low_pl, _ = apply_height_surcharge(ceiling_height, area_sqm, plaster_labor_low)
            adj_high_pl, _ = apply_height_surcharge(ceiling_height, area_sqm, plaster_labor_high)
            plaster_labor_low = int(adj_low_pl)
            plaster_labor_high = int(adj_high_pl)
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
            "data_source": "hardcoded_2025",
        })
        total_low += plaster_material + plaster_labor_low
        total_high += (plaster_material + 100000) + plaster_labor_high
        total_labor_low += plaster_labor_low
        total_labor_high += plaster_labor_high

    # Phase 5: Flooring (with optional waterproofing upgrade, skippable in partial mode)
    # Phase 5: Flooring — corpus-wired via needs_flooring
    if is_full_renovation or scope.get("flooring", True):
        fm_l, fm_h, fl_l, fl_h, fl_src = _phase_from_scope("needs_flooring")
        if fm_l is not None:
            floor_material = (fm_l + fm_h) // 2
            floor_material_high_range = floor_material + 120000  # buffer
            floor_labor_low, floor_labor_high = fl_l, fl_h
            floor_material_low = min(fm_l, fm_h)
        else:
            floor_material = 130000
            floor_material_low = 130000
            floor_material_high_range = 250000
            floor_labor_low, floor_labor_high = 300000, 600000
            fl_src = "hardcoded_2025"
        if has_shower:
            floor_material += 130000
            floor_material_low += 130000
            floor_material_high_range += 130000
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
            "material_cost_range": f"{floor_material_low:,} - {floor_material_high_range:,} Ft",
            "labor_cost_range": f"{floor_labor_low:,} - {floor_labor_high:,} Ft",
            "data_source": fl_src,
        })
        total_low += floor_material_low + floor_labor_low
        total_high += floor_material_high_range + floor_labor_high
        total_labor_low += floor_labor_low
        total_labor_high += floor_labor_high

    # Phase 6: Painting & fixtures (skippable in partial mode)
    # Labor costs scale with ceiling height (wall surface area)
    if is_full_renovation or scope.get("painting", True) or scope.get("kitchen", False):
        paint_material = 50000
        paint_labor_low = 200000
        paint_labor_high = 400000
        desc_6 = "Falfestés, kapcsolók, dugaljak, lámpák, ajtók szerelése"
        if scope.get("kitchen", is_full_renovation):
            paint_material += 300000
            paint_labor_low += 150000
            paint_labor_high += 250000
            desc_6 += ", konyhabútor szerelés"

        # Apply ceiling-height multiplier to painting labor
        adj_lab_low, _ = apply_height_surcharge(ceiling_height, area_sqm, paint_labor_low)
        adj_lab_high, _ = apply_height_surcharge(ceiling_height, area_sqm, paint_labor_high)
        paint_labor_low = int(adj_lab_low)
        paint_labor_high = int(adj_lab_high)
        if ceiling_height and ceiling_height > 2.75:
            desc_6 += f" (belmagasság: {ceiling_height:.1f}m, labor szorzó: {ceiling_height_multiplier(ceiling_height):.1f}x)"

        phases.append({
            "step": max(p["step"] for p in phases) + 1,
            "name": "Festés és szerelés (Painting & Fixtures)",
            "description": desc_6,
            "material_cost_range": f"{paint_material:,} - {paint_material + 50000:,} Ft",
            "labor_cost_range": f"{paint_labor_low:,} - {paint_labor_high:,} Ft",
            "data_source": "hardcoded_2025",
        })
        total_low += paint_material + paint_labor_low
        total_high += (paint_material + 50000) + paint_labor_high
        total_labor_low += paint_labor_low
        total_labor_high += paint_labor_high

    # Phase I/II: Infrastructure minimums — defensive top-up via the shared
    # structural_cost.apply_infrastructure_minimums() instead of the old
    # unconditional 300k electrical / 800k gas adds. The function only tops up
    # to the floor when the accrued phases imply electrical (12% of mid) /
    # gas-heating (20% of mid) coverage below the minimum — matching
    # handle_cost_estimation() (handlers.py:192). Both calls start from the same
    # pre-minimum base, so their deltas sum to exactly the combined top-up the
    # function computes in one shot; this lets us keep the two minimums as
    # separate phases while staying faithful to the shared function's semantics.
    pre_min_mid = (total_low + total_high) // 2

    # Electrical top-up (gas disabled so only the electrical check applies)
    _, elec_adj_mid, _, _ = apply_infrastructure_minimums(
        total_low, pre_min_mid, total_high, is_full_renovation, False,
    )
    elec_delta = elec_adj_mid - pre_min_mid

    # Combined top-up with the real gas flag; gas portion = combined - electrical
    _, combined_adj_mid, _, infra_logs = apply_infrastructure_minimums(
        total_low, pre_min_mid, total_high, is_full_renovation, gas_heating,
    )
    gas_delta = (combined_adj_mid - pre_min_mid) - elec_delta

    if elec_delta:
        phases.append({
            "step": max(p["step"] for p in phases) + 1 if phases else 1,
            "name": "Elektromos szabványosítás (Electrical Standardization)",
            "description": "Minden fázis után: elektromos hálózat szabványosítása, "
                           "új elosztótábla, biztonsági földelés. Kötelező minimum.",
            "material_cost_range": f"{elec_delta:,} - {elec_delta:,} Ft",
            "labor_cost_range": "0 Ft (építési munkadíjban benne)",
"is_infrastructure_minimum": True,
            "data_source": "hardcoded_2025",
        })
        total_low += elec_delta
        total_high += elec_delta

    if gas_delta:
        phases.append({
            "step": max(p["step"] for p in phases) + 1 if phases else 1,
            "name": "Gáz/Fűtés alapinfrastruktúra (Gas/Heating Baseline)",
            "description": "Kéményvizsgálat, gázterv, kéménybélelés, "
                            "fűtésrendszer tervezése. Kötelező minimum.",
            "material_cost_range": f"{gas_delta:,} - {gas_delta:,} Ft",
            "labor_cost_range": "0 Ft (építési munkadíjban benne)",
            "is_infrastructure_minimum": True,
            "data_source": "hardcoded_2025",
        })
        total_low += gas_delta
        total_high += gas_delta

    if elec_delta or gas_delta:
        warnings.extend(infra_logs)

    # Phase III: Chimney technician (conditional, gas heating only)
    if gas_heating:
        chim_cost = chimney_technician_cost(
            floor_number,
            target_date=target_date, price_index=price_index,
        )
        chim_cost_low = int(chim_cost * 0.85)
        chim_cost_high = int(chim_cost * 1.15)
        phases.append({
            "step": max(p["step"] for p in phases) + 1 if phases else 1,
            "name": "Kéménytechnikai szakember (Chimney Specialist)",
            "description": (
                f"Kéménytechnikai felülvizsgálat és karbantartás "
                f"({floor_number or 1}. emelet). Egyedi gázfűtés miatt kötelező."
            ),
            "material_cost_range": f"{chim_cost_low:,} - {chim_cost_high:,} Ft",
            "labor_cost_range": "0 Ft (anyagköltségben benne)",
            "data_source": "hardcoded_2025",
        })
        total_low += chim_cost_low
        total_high += chim_cost_high
        warnings.append(
            f"Kéménytechnikai szakember: +{chim_cost:,} Ft "
            "(egyedi gázfűtés miatt kötelező)."
        )

    # Logistics surcharge: 15% of total labor (full renovation only), via the
    # shared structural_cost.compute_logistics_surcharge() instead of an inline
    # reimplementation — same number, single source of truth (handlers.py:203).
    logistics_surcharge_low = 0
    logistics_surcharge_high = 0
    if is_full_renovation:
        logistics_surcharge_low = int(compute_logistics_surcharge(total_labor_low))
        logistics_surcharge_high = int(compute_logistics_surcharge(total_labor_high))
        # Elevator-based adjustment
        elev_extra = int(elevator_surcharge(elevator_type, floor_number))
        logistics_desc = (
            "Sitt elszállítás, védőfóliázás, lépcsőház védelem, "
            "konténer bérlés. 15% a teljes munkadíjra vetítve."
        )
        if elev_extra:
            logistics_surcharge_low += int(elev_extra * 0.85)
            logistics_surcharge_high += int(elev_extra * 1.15)
            logistics_desc += (
                f" Lift hiánya miatti pótlék: +{elev_extra:,} Ft "
                "(helyettesítő becslés — pontosítandó)."
            )
            warnings.append(
                f"Lift hiánya miatt logisztikai pótlék: {elev_extra:,} Ft "
                "(szakértői becslés, pontosítandó)."
            )
        phases.append({
            "step": max(p["step"] for p in phases) + 1 if phases else 1,
            "name": "Logisztikai pótlék (Logistics Surcharge)",
            "description": logistics_desc,
            "material_cost_range": "0 Ft (anyagköltség a fő tételekben)",
            "labor_cost_range": f"{logistics_surcharge_low:,} - {logistics_surcharge_high:,} Ft",
            "is_infrastructure_minimum": True,
            "data_source": "hardcoded_2025",
        })
        total_low += logistics_surcharge_low
        total_high += logistics_surcharge_high

    total_mid = (total_low + total_high) // 2

    # Build Hungarian Vibe Diff (mandatory for Tab 2)
    hidden_chains = []
    if has_slag:
        hidden_chains.append(
            "Kohósalak lánc: A padlómunka miatt szükséges salakmentesítés "
            "automatikusan elindítja a teljes láncot (eltávolítás → EPS szigetelés "
            "→ betonozás). Ez nem látható egyszerű szemrevételezéssel."
        )
    if chain_costs["point"]:
        hidden_chains.append(
            "Ajtó/Padló-lánc: Régi épületben az ajtócsere vagy parketta felbontása "
            "során előkerülő kohósalak miatt szükséges teljes aljzatfelújítás "
            f"(+{chain_costs['point']:,} Ft anyagköltség). "
            "Szakértői becslés — korpusz által nem validált."
        )
    if wall_condition.get("wallpaper", False):
        hidden_chains.append(
            "Fűrészporos tapéta lánc: A tapéta eltávolítása feltárja a vakolat "
            "hiányát, ami Q3 minőségű újravakolást tesz szükségessé."
        )
    # Ceiling height explanation
    ch = ceiling_height or 2.75
    if ch > 2.75:
        mult = ceiling_height_multiplier(ch)
        ref_mult = ceiling_height_multiplier(2.75)
        hidden_chains.append(
            f"Belmagasság alapú munkaerő-korrekció: A {ch:.1f}m-es belmagasság "
            f"miatt {mult:.1f}x szorzót alkalmaztam a festés és vakolás munkadíjára "
            f"(referencia: {ref_mult:.1f}x 2.75m-nél). "
            "Ezek a munkák falfelület-arányosak, nem alapterület-arányosak."
        )
    if is_full_renovation:
        hidden_chains.append(
            "Infrastrukturális minimumok: Teljes felújítás esetén az elektromos "
            f"szabványosítás ({elec_delta:,.0f} Ft pótlék) minden esetben kötelező."
        )
        if gas_heating:
            hidden_chains.append(
                "Gáz/fűtés alapinfrastruktúra: Egyedi gázfűtés esetén a "
                f"kéményvizsgálat, gázterv és kéménybélelés ({gas_delta:,.0f} Ft pótlék) kötelező minimum."
            )
        if gas_heating:
            hidden_chains.append(
                f"Kéménytechnikai szakember: Egyedi gázfűtés miatt "
                f"kéménytechnikai szakember bevonása szükséges "
                f"(+{chimney_technician_cost(floor_number, target_date=target_date, price_index=price_index):,} Ft)."
            )
        hidden_chains.append(
            "Logisztikai pótlék: A sitt elszállítás és védelmi költségek a "
            "teljes munkadíj 15%-át teszik ki, ami gyakran alultervezett tétel."
        )

    # Data-limitation warning for sparse-category selections
    active_sparse = [f for f in SPARSE_FLAGS if scope.get(f, False)]
    if active_sparse:
        sparse_names_hu = {
            "insulation": "szigetelés",
            "windows_doors": "nyílászáró csere",
            "kitchen": "konyhabútor",
            "bathroom": "fürdőszoba",
            "drywall": "gipszkarton",
            "ac": "klíma",
            "heating": "fűtésrendszer",
        }
        names_hu = [sparse_names_hu[f] for f in active_sparse]
        warnings.append(
            f"Adat korlátozás: a(z) {', '.join(names_hu)} munkafázisokhoz "
            f"kevés összehasonlítható piaci adat áll rendelkezésre az adatbázisban. "
            f"A megadott árképzés tájékoztató jellegű."
        )

    vibe_diff_hu = (
        f"A felújítási terv {total_mid:,} Ft várható összköltséggel számol. "
        f"A becslés {len(phases)} fázisra bontva tartalmazza az anyag- és munkadíjakat."
    )
    if hidden_chains:
        vibe_diff_hu += "\n\n**Rejtett technológiai láncok (Hidden Chains):**\n"
        for i, chain in enumerate(hidden_chains, 1):
            vibe_diff_hu += f"\n{i}. {chain}"

    if total_mid > 10_000_000:
        vibe_diff_hu += (
            "\n\n**Indoklás a {:,} Ft-os összköltséghez:**\n"
            "A magas összköltség a következő tényezők együttes hatásából adódik: "
            "a kiválasztott építési korszak és a kért munkák kombinációja "
            "több rejtett technológiai láncot aktivált. "
            "Ezek a láncok olyan kötelező munkafázisokat takarnak, "
            "amelyek egymás nélkül nem végezhetők el szakszerűen."
            .format(total_mid)
        )

    # Build dynamic confidence score from the data_source mix
    corpus_mid = sum(
        c["estimate_huf"]["mid"] for c in categories.values()
        if c.get("data_quality") in ("sufficient", "single_quote") and not c.get("fallback_used")
    )
    fallback_mid = sum(
        c["estimate_huf"]["mid"] for c in categories.values()
        if c.get("fallback_used")
    )
    hardcoded_mid = total_mid - corpus_mid - fallback_mid
    if hardcoded_mid < 0:
        hardcoded_mid = 0  # rounding safety

    n_corpus = sum(1 for p in phases if p.get("data_source") == "corpus")
    n_fallback = sum(1 for p in phases if p.get("data_source") == "corpus_fallback")
    n_hardcoded = sum(1 for p in phases if p.get("data_source") == "hardcoded_2025")
    n_total = n_corpus + n_fallback + n_hardcoded or 1
    corpus_pct = corpus_mid / total_mid * 100 if total_mid else 0
    fallback_pct = fallback_mid / total_mid * 100 if total_mid else 0
    hardcoded_pct = hardcoded_mid / total_mid * 100 if total_mid else 0

    base_score = 0.60  # baseline when 100% hardcoded
    corpus_bonus = 0.25 * (corpus_mid / total_mid) if total_mid else 0
    fallback_bonus = 0.10 * (fallback_mid / total_mid) if total_mid else 0
    confidence_score = min(0.95, base_score + corpus_bonus + fallback_bonus)
    if has_slag:
        confidence_score = max(confidence_score - 0.05, 0.50)

    reasoning_parts = []
    if corpus_pct > 0:
        reasoning_parts.append(
            f"{n_corpus} of {n_total} phases ({corpus_pct:.0f}% of estimated cost) "
            f"are based on real corpus data from similar renovations"
        )
    if fallback_pct > 0:
        reasoning_parts.append(
            f"{n_fallback} use corpus-informed fallback reference pricing ({fallback_pct:.0f}%)"
        )
    if hardcoded_pct > 0:
        reasoning_parts.append(
            f"the remainder ({hardcoded_pct:.0f}%) uses expert-sourced reference pricing"
        )
    confidence_reasoning = "; ".join(reasoning_parts) + "."

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
                "score": round(confidence_score, 3),
                "reasoning": confidence_reasoning,
            },
            "vibe_diff": {
                "explanation_hu": vibe_diff_hu,
                "explanation_en": (
                    f"Renovation plan totals {total_mid:,} HUF expected cost. "
                    f"The estimate is broken down into {len(phases)} phases with "
                    f"material and labor costs."
                ),
                "hidden_chains": hidden_chains,
                "total_exceeds_10m": total_mid > 10_000_000,
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
