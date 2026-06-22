"""
RenovAI MCP Server

Exposes three renovation due-diligence capabilities as MCP tools so that
any MCP client (orchestrator agent, civic-tech portal, tenant union app)
can call them over a standard protocol.

Threat model / data-limitation note:
  - The price model is trained on n=30 historical quotes, most without
    floor-area data.  Estimates are RAG-grounded similar-case comparisons
    first, regression numbers second.  Every response includes the number
    of similar cases found and a warning when the corpus is thin.
  - The RAG pipeline requires a populated ChromaDB vector store.  If the
    store is empty, the advisory tool returns a clear error asking the
    caller to run ingestion first.
  - The Text-to-SQL engine requires a populated SQLite database and a
    valid GOOGLE_API_KEY in the environment.  It returns the generated
    SQL alongside the results so callers can audit what was run.
"""

import os
import logging
from datetime import date
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from mcp.server import FastMCP
from dotenv import load_dotenv

from renovai.predictor.feature_extractor import ApartmentInput, apartment_input_to_features
from renovai.predictor.price_model import predict, find_similar_quotes
from renovai.advisor.pre_purchase import generate_report, ApartmentProfile
from renovai.advisor.report_renderer import render_report_md
from renovai.rag.vector_store import VectorStoreConfig, RenovAIVectorStore
from renovai.rag.embedder import EmbedderConfig
from renovai.rag.retriever import RetrievalConfig
from renovai.rag.gemini_client import GeminiConfig
from renovai.rag.pipeline import RAGPipeline
from renovai.ingestion.inflation_calc import load_price_index
from renovai.db.text_to_sql import TextToSQLEngine
from renovai.db.session import get_engine, get_session_maker, init_db

load_dotenv()
logger = logging.getLogger("renovai-mcp")

SCOPE_MAP = {
    "villany": {"needs_electrical": True},
    "viz": {"needs_plumbing": True},
    "viz_futes": {"needs_plumbing": True},
    "futes": {"needs_plumbing": True},
    "burkolas": {"needs_flooring": True},
    "bontas": {"needs_full_demolition": True},
    "festes": {},
    "nyilaszaro": {},
    "konyha": {},
    "furdo": {},
    "futes_rendszer": {"needs_plumbing": True},
    "szigeteles": {},
    "teljes": {"needs_plumbing": True, "needs_electrical": True, "needs_flooring": True, "needs_full_demolition": True},
}


class RenovAIState:
    def __init__(self):
        self._initialized = False
        self.rag_pipeline: Optional[RAGPipeline] = None
        self.price_index = None
        self.models_dir: Optional[Path] = None
        self.quotes_json_dir: Optional[Path] = None
        self.gemini_config: Optional[GeminiConfig] = None
        self.db_session_maker = None
        self.text_to_sql: Optional[TextToSQLEngine] = None
        self.init_error: Optional[str] = None

    def ensure_initialized(self):
        if self._initialized:
            return
        api_key = os.getenv("GOOGLE_API_KEY", "")
        chroma_dir = os.getenv("CHROMA_DB_PATH", "data/chroma_db")
        models_dir = os.getenv("MODELS_DIR", "data/models")
        quotes_json_dir = os.getenv("QUOTES_JSON_DIR", "data/processed/quotes_json")
        database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")
        inflation_materials_csv = os.getenv("INFLATION_MATERIALS_CSV", "data/raw/inflation/materials_cpi.csv")
        inflation_labor_csv = os.getenv("INFLATION_LABOR_CSV", "data/raw/inflation/labor_cpi.csv")

        errors = []

        if not api_key:
            errors.append("GOOGLE_API_KEY is not set in .env or environment")

        try:
            if Path(chroma_dir).exists():
                vs_config = VectorStoreConfig(persist_dir=chroma_dir)
                vector_store = RenovAIVectorStore(vs_config)
                stats = vector_store.get_collection_stats()
                logger.info("Vector store initialized: %s", stats)
            else:
                logger.warning("ChromaDB directory not found at %s", chroma_dir)
                vector_store = None

            emb_config = EmbedderConfig(api_key=api_key)
            ret_config = RetrievalConfig()
            self.gemini_config = GeminiConfig(api_key=api_key)

            if vector_store is not None:
                self.rag_pipeline = RAGPipeline(
                    vector_store, emb_config, ret_config, self.gemini_config
                )
                logger.info("RAG pipeline initialized")
            else:
                self.rag_pipeline = None

            if Path(inflation_materials_csv).exists() and Path(inflation_labor_csv).exists():
                self.price_index = load_price_index(
                    Path(inflation_materials_csv), Path(inflation_labor_csv)
                )
            else:
                logger.warning("Inflation CSVs not found, price index will be None")

            self.models_dir = Path(models_dir)
            self.quotes_json_dir = Path(quotes_json_dir)

            engine = get_engine(database_url)
            self.db_session_maker = get_session_maker(engine)
            self.text_to_sql = TextToSQLEngine(api_key=api_key)

        except Exception as exc:
            errors.append(str(exc))
            logger.error("Initialization error: %s", exc)

        if errors:
            self.init_error = "; ".join(errors)
            logger.warning("Partial init: %s", self.init_error)
        self._initialized = True


def _build_apartment_input(district: int, area_sqm: float, num_rooms: int, scope: list[str]) -> ApartmentInput:
    needs_plumbing = False
    needs_electrical = False
    needs_flooring = False
    needs_full_demolition = False
    suspected_slag = False
    for s in scope:
        mapping = SCOPE_MAP.get(s, {})
        if mapping.get("needs_plumbing"):
            needs_plumbing = True
        if mapping.get("needs_electrical"):
            needs_electrical = True
        if mapping.get("needs_flooring"):
            needs_flooring = True
        if mapping.get("needs_full_demolition"):
            needs_full_demolition = True
        if mapping.get("suspected_slag"):
            suspected_slag = True

    return ApartmentInput(
        district=district,
        total_area_sqm=area_sqm,
        num_rooms=num_rooms,
        needs_plumbing=needs_plumbing,
        needs_electrical=needs_electrical,
        needs_flooring=needs_flooring,
        needs_full_demolition=needs_full_demolition,
        suspected_slag=suspected_slag,
    )


state = RenovAIState()
mcp = FastMCP("renovai", log_level="WARNING")


@mcp.tool(
    name="estimate_renovation_cost",
    description=(
        "Estimate the total renovation cost (HUF) for a Hungarian apartment given its "
        "district, size, room count, and scope of work.  "
        "Pass scope as a list of Hungarian keywords from: "
        "villany (electrical), viz_futes (plumbing/heating), burkolas (flooring), "
        "bontas (demolition), festes (painting), nyilaszaro (windows/doors), "
        "konyha (kitchen), furdo (bathroom), futes_rendszer (heating system), "
        "szigeteles (insulation), teljes (full renovation).  "
        "Returns a dict with low/mid/high estimates, up to 3 similar historical cases, "
        "and a warning if the corpus is too small to be reliable."
    ),
)
def estimate_renovation_cost(
    district: int,
    area_sqm: float,
    num_rooms: int,
    scope: list[str],
) -> dict:
    """Estimate renovation cost for a Hungarian apartment based on comparable historical quotes."""
    state.ensure_initialized()
    apt_input = _build_apartment_input(district, area_sqm, num_rooms, scope)
    features = apartment_input_to_features(apt_input)
    target_date = date.today()

    if state.price_index is None or not state.models_dir.exists():
        return {
            "low": None,
            "mid": None,
            "high": None,
            "similar_cases": [],
            "warning": "Price model or inflation data not available. Run data pipeline first.",
        }

    result = predict(
        features=features,
        price_index=state.price_index,
        target_date=target_date,
        model_dir=state.models_dir,
    )

    similar = []
    if state.quotes_json_dir.exists():
        similar = find_similar_quotes(features, state.quotes_json_dir, top_k=3)

    warnings = []
    if result.get("warning"):
        warnings.append(result["warning"])
    if len(similar) < 3:
        warnings.append(
            f"Only {len(similar)} comparable historical quotes found (n=30 corpus). "
            "Estimates are indicative, not a contractor quote."
        )

    return {
        "low_huf": result["estimate_low_huf"],
        "mid_huf": result["estimate_mid_huf"],
        "high_huf": result["estimate_high_huf"],
        "inflation_adjusted_to": str(target_date),
        "similar_cases": similar,
        "num_similar_cases": len(similar),
        "warning": "; ".join(warnings) if warnings else None,
    }


@mcp.tool(
    name="get_due_diligence_advice",
    description=(
        "Generate a pre-purchase due-diligence advisory report for a Hungarian apartment.  "
        "Provide district, area_sqm, building_type (panel / tegla / ujepites / ismeretlen), "
        "condition (nagyon_rossz / kozepes / lakohato), and any known_issues "
        "(e.g. ['kohosalak', 'nedvesedes', 'osztott_közmu']).  "
        "Returns a Hungarian-language report with questions_for_seller, inspection_checklist, "
        "red_flags, overall_risk, and the cost_estimate block from estimate_renovation_cost."
    ),
)
def get_due_diligence_advice(
    district: int,
    area_sqm: float,
    building_type: str,
    condition: str,
    known_issues: list[str],
) -> dict:
    """Generate pre-purchase advisory report for a Hungarian apartment buyer."""
    state.ensure_initialized()

    if state.rag_pipeline is None:
        return {
            "error": (
                "RAG pipeline is not available.  "
                "Run the data ingestion and vector store build first "
                "(see renovai/scripts/build_vector_store.py)."
            ),
            "questions_for_seller": [],
            "inspection_checklist": [],
            "red_flags": [],
            "overall_risk": "ismeretlen",
            "sources_cited": [],
        }

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

    if state.price_index is None:
        return {
            "error": "Inflation/price data not loaded.",
            "questions_for_seller": [],
            "inspection_checklist": [],
            "red_flags": [],
            "overall_risk": "ismeretlen",
            "sources_cited": [],
        }

    report = generate_report(
        profile=profile,
        rag_pipeline=state.rag_pipeline,
        price_predictor_model_dir=state.models_dir,
        price_index=state.price_index,
        gemini_config=state.gemini_config,
    )

    return {
        "questions_for_seller": [it.model_dump() for it in report.questions_for_seller],
        "inspection_checklist": [it.model_dump() for it in report.inspection_checklist],
        "red_flags": [it.model_dump() for it in report.red_flags],
        "cost_estimate": report.cost_estimate,
        "similar_cases": report.similar_cases,
        "overall_risk": report.overall_risk,
        "summary_hu": report.summary_hu,
        "sources_cited": report.rag_sources_used,
        "generated_at": report.generated_at.isoformat(),
    }


@mcp.tool(
    name="query_renovation_market",
    description=(
        "Ask a free-text Hungarian question about the renovation market data stored in the "
        "database (e.g. 'Mennyibe kerult atlagosan a villanyszereles a 2023-as arakban?' or "
        "'Melyik kerületben voltak a legdragabb felujitasok?').  "
        "The question is converted to SQL via Gemini, executed safely, and the results "
        "are returned alongside the generated SQL for auditability.  "
        "Only SELECT queries are permitted."
    ),
)
async def query_renovation_market(question_hu: str) -> dict:
    """Query the renovation database using natural Hungarian questions via Text-to-SQL."""
    state.ensure_initialized()

    if state.text_to_sql is None or state.db_session_maker is None:
        return {
            "question": question_hu,
            "sql": None,
            "rows": [],
            "row_count": 0,
            "error": "Database not configured. Check DATABASE_URL and GOOGLE_API_KEY.",
        }

    try:
        async with state.db_session_maker() as session:
            result = await state.text_to_sql.query(question_hu, session)

        if "error" in result:
            return {
                "question": question_hu,
                "sql": result.get("sql", ""),
                "rows": [],
                "row_count": 0,
                "error": result["error"],
            }

        return {
            "question": question_hu,
            "sql": result["sql"],
            "rows": result["rows"],
            "row_count": result["row_count"],
        }
    except Exception as exc:
        return {
            "question": question_hu,
            "sql": None,
            "rows": [],
            "row_count": 0,
            "error": str(exc),
        }


# SSE/HTTP app for Cloud Run deployments
sse_app = mcp.sse_app()

# Cloud Run forwards requests with the public hostname in the Host header.
# Starlette's default TrustedHostMiddleware (added by MCP's sse_app) rejects
# non-localhost hosts, so we override it to allow all.
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402
for mw in sse_app.user_middleware:
    if mw.cls is TrustedHostMiddleware:
        mw.options["allowed_hosts"] = ["*"]
        break
sse_app.middleware_stack = None  # force rebuild on next request

if __name__ == "__main__":
    is_cloud_run = os.getenv("CLOUD_RUN", "").lower() in ("true", "1", "yes")
    port = int(os.getenv("PORT", 8080))

    if is_cloud_run:
        import uvicorn
        logging.basicConfig(level=logging.INFO)
        logger.info("Starting RenovAI MCP server on SSE transport (Cloud Run) — port %d", port)
        uvicorn.run(sse_app, host="0.0.0.0", port=port, log_level="info")
    else:
        mcp.run(transport="stdio")
