"""
RenovAI — Streamlit frontend for the renovation advisory system.
Calls renovai/ Python functions directly (no MCP server needed).
"""

import asyncio
import concurrent.futures
import logging
import os
import re
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from renovai.api.config import AppConfig
from renovai.predictor.feature_extractor import (
    ApartmentInput,
    apartment_input_to_features,
)
from renovai.predictor.price_model import scope_matched_estimate, find_similar_quotes
from renovai.predictor.cost_breakdown import load_cost_breakdown
from renovai.advisor.pre_purchase import ApartmentProfile
from renovai.advisor.question_loader import get_questions_for_profile
from renovai.rag.vector_store import VectorStoreConfig, RenovAIVectorStore
from renovai.rag.embedder import EmbedderConfig
from renovai.rag.retriever import RetrievalConfig
from renovai.rag.gemini_client import GeminiConfig
from renovai.rag.pipeline import RAGPipeline
from renovai.ingestion.inflation_calc import load_price_index as _load_price_index
from renovai.db.text_to_sql import TextToSQLEngine
from mcp_server.tools.newbuild_comparator import (
    get_newbuild_median_price,
    calculate_buy_vs_new,
)
from renovai.db.session import get_engine, get_session_maker

logger = logging.getLogger("renovai-streamlit")

LANG = {
    "HU": {
        "page_title": "RenovAI — Felújítási tanácsadó",
        "sidebar.title": "🏠 RenovAI",
        "sidebar.tagline": "Ingyenes felújítási tanácsadó első lakásvásárlóknak",
        "nav.cost": "🏠 Felkészülés",
        "nav.advisory": "📊 Értékelés",
        "nav.market": "📊 Piaci adatok",
        "sidebar.data_source": "Az adatok 30 valós felújítási árajánlaton alapulnak, 2023–2026 között, Budapest különböző kerületeiben.",
        "sidebar.disclaimer": "⚠️ Ez egy POC eszköz. Az eredmények tájékoztató jellegűek, nem helyettesítik a szakértői véleményt.",
        "lang.label": "Nyelv",
        "t1.subtitle": "A lakás adatai",
        "t1.district": "Kerület",
        "t1.district_fmt": "Budapest {}. kerület",
        "t1.area": "Alapterület (m²)",
        "t1.rooms": "Szobák száma",
        "t1.era": "Építési időszak (közelítő)",
        "t1.era_unknown": "Ismeretlen",
        "t1.era_fmt": "~{}as évek",
        "t1.scope_title": "Felújítási munkák",
        "t1.scope_hint": "Jelöld be, melyik munkára van szükség:",
        "t1.demolition": "Bontás",
        "t1.plumbing": "Víz és fűtés",
        "t1.electrical": "Villanyszerelés",
        "t1.tiling": "Burkolás",
        "t1.plastering": "Glettelés/festés",
        "t1.flooring": "Parketta",
        "t1.ac": "Klíma",
        "t1.slag": "⚠️ Kohósalak gyanú",
        "t1.slag_help": "Ha régi panelépületben az aljzat kohósalakot tartalmazhat, a bontás lényegesen drágább.",
        "t1.listing_notes": "Mit írt a hirdetés? (opcionális)",
        "t1.listing_notes_ph": "pl. felújított fürdő, régi nyílászárók, gázkonvektor, erkély...",
        "t1.button": "Felkészülés generálása →",
        "t1.spinner": "Tanácsadó jelentés összeállítása...",
        "t1.no_rag": "A RAG pipeline nem érhető el. Futtassa az adatbetöltést először.",
        "t1.error": "Hiba történt a jelentés generálása során. Kérjük próbálja újra, vagy ellenőrizze a .env konfigurációt.",

        # Legacy t1 keys reused by Tab 2 (post-visit)
        "t2.area": "Alapterület (m²)",
        "t2.rooms": "Szobák száma",
        "t2.floor": "Emelet",
        "t2.floor_help": "0 = földszint",
        "t2.total_floors": "Összes emelet",
        "t2.asking_price": "Kért vételár (millió Ft)",
        "t2.visit_notes": "Mit tapasztaltál a megtekintésen? (opcionális)",
        "t2.visit_notes_ph": "pl. penész a fürdőszoba sarkában, régi elektromos tábla, szép parketta...",
        "t2.scope_title": "Felújítási munkák",
        "t2.scope_hint": "Jelöld be, melyik munkára van szükség:",
        "t2.demolition": "Bontás",
        "t2.plumbing": "Víz és fűtés",
        "t2.electrical": "Villanyszerelés",
        "t2.tiling": "Burkolás",
        "t2.plastering": "Glettelés/festés",
        "t2.flooring": "Parketta",
        "t2.ac": "Klíma",
        "t2.slag": "⚠️ Kohósalak gyanú",
        "t2.slag_help": "Ha régi panelépületben az aljzat kohósalakot tartalmazhat, a bontás lényegesen drágább.",
        "t2.button": "Értékelés futtatása →",
        "t2.spinner": "Költségbecslés és összehasonlítás...",
        "t2.cost_title": "Becsült felújítási költség",
        "t2.metric_low": "Alacsony becslés",
        "t2.metric_low_help": "Optimista eset, minden simán megy",
        "t2.metric_mid": "📌 Várható költség",
        "t2.metric_mid_help": "Erre tervezz. P75 percentilis.",
        "t2.metric_high": "Magas becslés",
        "t2.metric_high_help": "Tartalékold ezt az összeget is ha kohósalak gyanú van",
        "t2.per_sqm": "Átlagosan **{per_sqm} Ft/m²** | Inflációs alap: {date}",
        "t2.similar_title": "Hasonló felújítások az adatbázisból",
        "t2.similar_hint": "Ezeken az árajánlatokon alapul a becslés (inflációval korrigálva).",
        "t2.why_expander": "💡 Miért ennyibe kerül?",
        "t2.why_samples": "Átlag ({n} db)",
        "t2.why_median": "Medián",
        "t2.why_disclaimer": "Az árak inflációval korrigált összegek a 30 árajánlat alapján. Tájékoztató jellegűek.",
        "t2.why_no_data": "Nincs elérhető adat a költségbontáshoz.",
        "t2.comparison_title": "📊 Megéri megvenni?",
        "t2.buy_column": "Vételár + felújítás",
        "t2.buy_column_help": "Mennyibe kerülne összesen ez a lakás felújítva",
        "t2.newbuild_column": "Új lakás medián ára",
        "t2.newbuild_column_help": "Hasonló méretű új építésű lakás medián ára ugyanebben a kerületben (KSH 2025 Q4)",
        "t2.source_caption": "Forrás: KSH lakásár-statisztika 2025 Q4 | Csak tájékoztató jellegű, nem pénzügyi tanácsadás.",
        "t2.error": "Hiba történt az értékelés során. Kérjük próbálja újra, vagy ellenőrizze a .env konfigurációt.",
        "t2.subtitle": "A lakás jellemzői",
        "t2.district": "Kerület",
        "t2.area": "Alapterület (m²)",
        "t2.rooms": "Szobák száma",
        "t2.building_type": "Épület típusa",
        "t2.building_type_brick": "Téglaépület",
        "t2.building_type_panel": "Panelház",
        "t2.building_type_new": "Újépítés",
        "t2.building_type_unknown": "Ismeretlen",
        "t2.era": "Építési időszak",
        "t2.era_1945_elott": "1945 előtt",
        "t2.era_1945_1970": "1945–1970",
        "t2.era_1970_1990": "1970–1990",
        "t2.era_1990_2010": "1990–2010",
        "t2.era_2010_utan": "2010 után",
        "t2.condition": "Jelenlegi állapot",
        "t2.condition_bad": "😰 Nagyon rossz",
        "t2.condition_mid": "😐 Közepes",
        "t2.condition_ok": "🙂 Lakható",
        "t2.known_issues": "Ismert problémák (opcionális)",
        "t2.known_issues_ph": "pl. penész a fürdőben, régi elektromos hálózat, repedések a falakon...",
        "t2.has_visited": "Személyesen jártam a lakásban",
        "t2.asking_price": "Kért vételár (millió Ft, opcionális)",
        "t2.button": "Tanácsadói jelentés generálása →",
        "t2.wait": "A jelentés generálása 20–40 másodpercet vesz igénybe. Az AI elemzi a hasonló felújítási eseteket...",
        "t2.spinner": "Elemzés folyamatban...",
        "t2.no_rag": "A RAG pipeline nem érhető el. Futtassa az adatbetöltést először.",
        "t2.risk_label": "Kockázati szint: {risk}",
        "t2.cost_label": "Becsült felújítási cost: **{low} – {high}**",
        "t2.expander_questions": "❓ Kérdések az eladónak ({n} tétel)",
        "t2.expander_inspection": "🔍 Helyszíni ellenőrzési lista ({n} tétel)",
        "t2.expander_redflags": "🚩 Piros zászlók ({n} tétel)",
        "t2.expander_sources": "📚 Felhasznált adatforrások",
        "t2.why": "Miért fontos: {why}",
        "t2.source": "📎 Forrás: `{src}`",
        "t2.error": "Hiba történt a jelentés generálása során. Kérjük próbálja újra, vagy ellenőrizze a .env konfigurációt.",
        "t2.tech_detail": "🔧 Technikai részletek",
        "t3.title": "Kérdezz az adatbázisból",
        "t3.hint": "Tegyél fel kérdéseket magyarul a 30 felújítási árajánlatot tartalmazó adatbázisnak.",
        "t3.examples": "Példakérdések:",
        "t3.ex_q1": "Átlagosan mennyibe kerül egy felújítás a 8. kerületben?",
        "t3.ex_q2": "Melyik munkafajta a legdrágább általában?",
        "t3.ex_q3": "Hány árajánlatban szerepel kohósalak probléma?",
        "t3.ex_q4": "Mi a legolcsóbb és legdrágább ajánlat összege?",
        "t3.ex_q5": "Milyen arányban oszlik meg a munkadíj és az anyag?",
        "t3.ex_q6": "Mennyi az átlagos felújítási idő hetekben?",
        "t3.input": "A kérdésed:",
        "t3.input_ph": "pl. Melyik kerületben a legdrágább a felújítás?",
        "t3.button": "Lekérdezés →",
        "t3.spinner": "SQL generálása és futtatása...",
        "t3.sql_label": "🔧 Generált SQL lekérdezés",
        "t3.rows_found": "{n} sor találat",
        "t3.no_data": "Nem található adat erre a lekérdezésre.",
        "t3.error": "Hiba történt a lekérdezés során. Kérjük próbálja újra.",
        "priority.kritikus": "kritikus",
        "priority.fontos": "fontos",
        "priority.erdemes": "érdemes_megnézni",
        "anon.address": "Budapest {dist}. kerület, ~{area} m²",
    },
    "EN": {
        "page_title": "RenovAI — Renovation Advisor",
        "sidebar.title": "🏠 RenovAI",
        "sidebar.tagline": "Free renovation advisor for first-time home buyers",
        "nav.cost": "🏠 Pre-visit",
        "nav.advisory": "📊 Valuation",
        "nav.market": "📊 Market Data",
        "sidebar.data_source": "Data is based on 30 real renovation quotes from 2023–2026 across Budapest districts.",
        "sidebar.disclaimer": "⚠️ This is a POC tool. Results are for informational purposes only and do not replace professional advice.",
        "lang.label": "Language",
        "t1.subtitle": "Apartment Details",
        "t1.district": "District",
        "t1.district_fmt": "Budapest District {}",
        "t1.area": "Floor Area (m²)",
        "t1.rooms": "Number of Rooms",
        "t1.era": "Building Era (approx.)",
        "t1.era_unknown": "Unknown",
        "t1.era_fmt": "~{}s",
        "t1.scope_title": "Renovation Scope",
        "t1.scope_hint": "Select which work is needed:",
        "t1.demolition": "Demolition",
        "t1.plumbing": "Plumbing & Heating",
        "t1.electrical": "Electrical",
        "t1.tiling": "Tiling",
        "t1.plastering": "Plastering & Painting",
        "t1.flooring": "Flooring",
        "t1.ac": "A/C",
        "t1.slag": "⚠️ Suspected Slag",
        "t1.slag_help": "In older panel buildings the subfloor may contain slag, significantly increasing demolition costs.",
        "t1.listing_notes": "What does the listing say? (optional)",
        "t1.listing_notes_ph": "e.g. renovated bathroom, old windows, gas convector, balcony...",
        "t1.button": "Generate Preparation →",
        "t1.spinner": "Compiling advisory report...",
        "t1.no_rag": "RAG pipeline is not available. Run data ingestion first.",
        "t1.error": "An error occurred during report generation. Please try again or check your .env configuration.",

        # Legacy t1 keys reused by Tab 2 (post-visit)
        "t2.area": "Floor Area (m²)",
        "t2.rooms": "Number of Rooms",
        "t2.floor": "Floor",
        "t2.floor_help": "0 = ground floor",
        "t2.total_floors": "Total Floors",
        "t2.asking_price": "Asking Price (million HUF)",
        "t2.visit_notes": "What did you notice during the visit? (optional)",
        "t2.visit_notes_ph": "e.g. mold in bathroom corner, old electrical panel, nice parquet...",
        "t2.scope_title": "Renovation Scope",
        "t2.scope_hint": "Select which work is needed:",
        "t2.demolition": "Demolition",
        "t2.plumbing": "Plumbing & Heating",
        "t2.electrical": "Electrical",
        "t2.tiling": "Tiling",
        "t2.plastering": "Plastering & Painting",
        "t2.flooring": "Flooring",
        "t2.ac": "A/C",
        "t2.slag": "⚠️ Suspected Slag",
        "t2.slag_help": "In older panel buildings the subfloor may contain slag, significantly increasing demolition costs.",
        "t2.button": "Run Valuation →",
        "t2.spinner": "Estimating cost and comparing...",
        "t2.cost_title": "Estimated Renovation Cost",
        "t2.metric_low": "Low Estimate",
        "t2.metric_low_help": "Optimistic scenario, everything goes smoothly",
        "t2.metric_mid": "📌 Expected Cost",
        "t2.metric_mid_help": "Plan for this. P75 percentile.",
        "t2.metric_high": "High Estimate",
        "t2.metric_high_help": "Budget this amount if slag is suspected",
        "t2.per_sqm": "Average **{per_sqm} Ft/m²** | Inflation base: {date}",
        "t2.similar_title": "Similar Renovations in Database",
        "t2.similar_hint": "The estimate is based on these quotes (inflation-adjusted).",
        "t2.why_expander": "💡 Why does it cost this much?",
        "t2.why_samples": "Avg ({n} quotes)",
        "t2.why_median": "Median",
        "t2.why_disclaimer": "Inflation-adjusted costs from 30 renovation quotes. For informational purposes only.",
        "t2.why_no_data": "No cost breakdown data available.",
        "t2.comparison_title": "📊 Is it worth it?",
        "t2.buy_column": "Purchase + renovation",
        "t2.buy_column_help": "Total cost of this apartment renovated",
        "t2.newbuild_column": "New-build median price",
        "t2.newbuild_column_help": "Median price of a comparable new-build in the same district (KSH 2025 Q4)",
        "t2.source_caption": "Source: KSH housing price statistics 2025 Q4 | Informational only, not financial advice.",
        "t2.error": "An error occurred during valuation. Please try again or check your .env configuration.",
        "t2.subtitle": "Property Details",
        "t2.district": "District",
        "t2.area": "Floor Area (m²)",
        "t2.rooms": "Number of Rooms",
        "t2.building_type": "Building Type",
        "t2.building_type_brick": "Brick Building",
        "t2.building_type_panel": "Panel Block",
        "t2.building_type_new": "New Build",
        "t2.building_type_unknown": "Unknown",
        "t2.era": "Building Era",
        "t2.era_1945_elott": "Before 1945",
        "t2.era_1945_1970": "1945–1970",
        "t2.era_1970_1990": "1970–1990",
        "t2.era_1990_2010": "1990–2010",
        "t2.era_2010_utan": "After 2010",
        "t2.condition": "Current Condition",
        "t2.condition_bad": "😰 Very Poor",
        "t2.condition_mid": "😐 Fair",
        "t2.condition_ok": "🙂 Livable",
        "t2.known_issues": "Known Issues (optional)",
        "t2.known_issues_ph": "e.g. mold in bathroom, old electrical wiring, cracks in walls...",
        "t2.has_visited": "I have visited the apartment in person",
        "t2.asking_price": "Asking Price (million HUF, optional)",
        "t2.button": "Generate Advisory Report →",
        "t2.wait": "Report generation takes 20–40 seconds. The AI is analyzing similar renovation cases...",
        "t2.spinner": "Analysis in progress...",
        "t2.no_rag": "RAG pipeline is not available. Run data ingestion first.",
        "t2.risk_label": "Risk Level: {risk}",
        "t2.cost_label": "Estimated renovation cost: **{low} – {high}**",
        "t2.expander_questions": "❓ Questions for the Seller ({n} items)",
        "t2.expander_inspection": "🔍 On-site Inspection Checklist ({n} items)",
        "t2.expander_redflags": "🚩 Red Flags ({n} items)",
        "t2.expander_sources": "📚 Data Sources Used",
        "t2.why": "Why it matters: {why}",
        "t2.source": "📎 Source: `{src}`",
        "t2.error": "An error occurred while generating the report. Please try again or check your .env configuration.",
        "t2.tech_detail": "🔧 Technical Details",
        "t3.title": "Query the Database",
        "t3.hint": "Ask questions in Hungarian about the database of 30 renovation quotes.",
        "t3.examples": "Example Questions:",
        "t3.ex_q1": "What's the average renovation cost in District 8?",
        "t3.ex_q2": "Which work type is most expensive on average?",
        "t3.ex_q3": "How many quotes mention slag complications?",
        "t3.ex_q4": "What are the cheapest and most expensive quote totals?",
        "t3.ex_q5": "What's the labor-to-material cost ratio?",
        "t3.ex_q6": "What's the average renovation duration in weeks?",
        "t3.input": "Your Question:",
        "t3.input_ph": "e.g. Which district has the most expensive renovations?",
        "t3.button": "Run Query →",
        "t3.spinner": "Generating SQL and running query...",
        "t3.sql_label": "🔧 Generated SQL Query",
        "t3.rows_found": "{n} rows found",
        "t3.no_data": "No data found for this query.",
        "t3.error": "An error occurred during the query. Please try again.",
        "priority.kritikus": "critical",
        "priority.fontos": "important",
        "priority.erdemes": "worth checking",
        "anon.address": "Budapest Dist. {dist}, ~{area} m²",
    },
}

PRIORITY_ORDER = ["kritikus", "fontos", "érdemes_megnézni"]

PII_COLUMNS = {"address", "address_raw", "file_name", "file"}


st.set_page_config(
    page_title="RenovAI — Felújítási tanácsadó",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

cfg = AppConfig()


def _(key: str, **kwargs) -> str:
    lang = st.session_state.get("lang", "HU")
    val = LANG.get(lang, LANG["HU"]).get(key, key)
    if kwargs:
        return val.format(**kwargs)
    return val


def fmt_huf(n: int) -> str:
    return f"{n:,} Ft".replace(",", " ")


def _anonymize_address(addr) -> str:
    lang = st.session_state.get("lang", "HU")
    if isinstance(addr, (int, float)):
        fmt = LANG[lang]["t1.district_fmt"]
        return fmt.format(int(addr))
    m = re.search(r"(\d+)\.?\s*(?:kerület|ker|district)", str(addr))
    if m:
        return f"Budapest {m.group(1)}. kerület"
    return f"Budapest {addr}. kerület" if isinstance(addr, (int, float)) else "Budapest (anon.)"


def _scrub_df(df: pd.DataFrame) -> pd.DataFrame:
    drop_cols = [c for c in PII_COLUMNS if c in df.columns]
    if drop_cols:
        df = df.drop(columns=drop_cols)
    return df


def _anonymize_source(src: str) -> str:
    parts = src.replace("\\", "/").split("/")
    relevant = [p for p in parts if "kerület" in p.lower() or "district" in p.lower() or "m2" in p.lower() or "m²" in p.lower()]
    if relevant:
        return relevant[-1]
    filename = parts[-1] if parts else src
    filename = re.sub(r"\b(\d{1,2})\s*\.\s*kerület", r"\1. district", filename)
    return filename


@st.cache_resource
def load_price_index():
    return _load_price_index(
        Path(cfg.inflation_materials_csv),
        Path(cfg.inflation_labor_csv),
    )


@st.cache_resource
def load_sql_resources():
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")
    engine = get_engine(db_url)
    sm = get_session_maker(engine)
    t2s = TextToSQLEngine(cfg)
    return t2s, sm


@st.cache_resource
def load_rag_pipeline():
    chroma_dir = cfg.chroma_dir
    if not Path(chroma_dir).exists():
        return None
    vs_config = VectorStoreConfig(persist_dir=chroma_dir)
    vector_store = RenovAIVectorStore(vs_config)
    emb_config = EmbedderConfig(api_key=cfg.google_api_key)
    ret_config = RetrievalConfig()
    gem_config = GeminiConfig(api_key=cfg.google_api_key)
    return RAGPipeline(vector_store, emb_config, ret_config, gem_config)


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _run_sql_query(question: str, engine: TextToSQLEngine, sm) -> dict:
    async with sm() as session:
        return await engine.query(question, session)


if "price_index" not in st.session_state:
    st.session_state.price_index = load_price_index()
if "sql_question" not in st.session_state:
    st.session_state.sql_question = ""
if "sql_resources" not in st.session_state:
    st.session_state.sql_resources = load_sql_resources()
if "rag_pipeline" not in st.session_state:
    st.session_state.rag_pipeline = load_rag_pipeline()
if "lang" not in st.session_state:
    st.session_state.lang = "HU"
if "pre_visit_district" not in st.session_state:
    st.session_state.pre_visit_district = 8
if "pre_visit_building_type" not in st.session_state:
    st.session_state.pre_visit_building_type = "tégla"
if "pre_visit_era" not in st.session_state:
    st.session_state.pre_visit_era = "1945_1970"
if "pre_visit_condition" not in st.session_state:
    st.session_state.pre_visit_condition = "közepes"

sql_engine, session_maker = st.session_state.sql_resources

# ── Sidebar ──────────────────────────────────────────────────────

with st.sidebar:
    st.markdown(f"## {_('sidebar.title')}")
    st.markdown(f"*{_('sidebar.tagline')}*")
    st.divider()

    tab_selection = st.radio(
        "nav",
        [_("nav.cost"), _("nav.advisory"), _("nav.market")],
        label_visibility="collapsed",
        format_func=lambda x: x,
    )
    st.divider()
    st.caption(_("sidebar.data_source"))
    st.divider()
    st.caption(_("sidebar.disclaimer"))
    st.divider()
    st.selectbox(
        _("lang.label"),
        options=["HU", "EN"],
        key="lang",
        format_func=lambda x: {"HU": "🇭🇺 Magyar", "EN": "🇬🇧 English"}[x],
        label_visibility="collapsed",
    )

# ── Tab 1: Pre-visit Advisor ────────────────────────────────────

if tab_selection == _("nav.cost"):
    col1, col2 = st.columns(2)

    with col1:
        st.subheader(_("t1.subtitle"))

        district = st.selectbox(
            _("t1.district"),
            options=list(range(1, 23)),
            format_func=lambda x: _("t1.district_fmt").format(x),
        )

        bt_opts = ["tégla", "panel", "újépítés", "ismeretlen"]
        bt_fmt = {
            "tégla": _("t2.building_type_brick"),
            "panel": _("t2.building_type_panel"),
            "újépítés": _("t2.building_type_new"),
            "ismeretlen": _("t2.building_type_unknown"),
        }
        building_type = st.selectbox(
            _("t2.building_type"),
            options=bt_opts,
            format_func=bt_fmt.get,
        )

        era_opts = ["1945_előtt", "1945_1970", "1970_1990", "1990_2010", "2010_után"]
        era_fmt = {
            "1945_előtt": _("t2.era_1945_elott"),
            "1945_1970": _("t2.era_1945_1970"),
            "1970_1990": _("t2.era_1970_1990"),
            "1990_2010": _("t2.era_1990_2010"),
            "2010_után": _("t2.era_2010_utan"),
        }
        building_era = st.selectbox(
            _("t2.era"),
            options=era_opts,
            format_func=era_fmt.get,
        )

        cond_opts = ["nagyon_rossz", "közepes", "lakható"]
        cond_fmt = {
            "nagyon_rossz": _("t2.condition_bad"),
            "közepes": _("t2.condition_mid"),
            "lakható": _("t2.condition_ok"),
        }
        condition = st.select_slider(
            _("t2.condition"),
            options=cond_opts,
            format_func=cond_fmt.get,
            value="közepes",
        )

        listing_notes = st.text_area(
            _("t1.listing_notes"),
            placeholder=_("t1.listing_notes_ph"),
            height=100,
        )

        run_prep = st.button(
            _("t1.button"), type="primary", use_container_width=True
        )

    with col2:
        if run_prep:
            st.session_state.pre_visit_district = district
            st.session_state.pre_visit_building_type = building_type
            st.session_state.pre_visit_era = building_era
            st.session_state.pre_visit_condition = condition

            lang = st.session_state.get("lang", "HU")
            questions = get_questions_for_profile(
                building_type=building_type,
                era=building_era,
                condition=condition,
                questions_dir=Path("data/raw/questions"),
            )

            q_kerdesek = [q for q in questions if q["section"] == "kerdesek"]
            q_ellenorzes = [q for q in questions if q["section"] == "ellenorzes"]
            q_piros = [q for q in questions if q["section"] == "piros_zaszlo"]

            st.subheader(
                f"{'🟡' if q_piros else '🟢'} "
                + _("t2.risk_label", risk="ISMENT" if q_piros else "ALACSONY")
            )
            st.markdown(
                f"> A {building_type} épületben, {_('t1.district_fmt').format(district)}-ban "
                f"található lakás {'nagyon rossz' if condition == 'nagyon_rossz' else condition} "
                f"állapotú. Az alábbi kérdések segítenek felkészülni a megtekintésre."
            )

            with st.expander(
                _("t2.expander_questions", n=len(q_kerdesek)),
                expanded=True,
            ):
                for q in q_kerdesek:
                    st.markdown(f"- {q['text']}")

            with st.expander(
                _("t2.expander_inspection", n=len(q_ellenorzes)),
            ):
                for q in q_ellenorzes:
                    st.markdown(f"- {q['text']}")

            with st.expander(
                _("t2.expander_redflags", n=len(q_piros)),
            ):
                for q in q_piros:
                    st.error(q["text"])

# ── Tab 2: Post-visit Valuation ─────────────────────────────────

elif tab_selection == _("nav.advisory"):
    col1, col2 = st.columns(2)

    with col1:
        st.info(
            "A Tab 1-ben megadott adatok automatikusan "
            "be vannak töltve. Egészítsd ki a látogatás "
            "után szerzett információkkal."
        )
        st.subheader(_("t2.subtitle"))

        district = st.selectbox(
            _("t2.district"),
            options=list(range(1, 23)),
            format_func=lambda x: _("t1.district_fmt").format(x),
            index=st.session_state.pre_visit_district - 1,
        )

        bt_opts = ["tégla", "panel", "újépítés", "ismeretlen"]
        bt_fmt = {
            "tégla": _("t2.building_type_brick"),
            "panel": _("t2.building_type_panel"),
            "újépítés": _("t2.building_type_new"),
            "ismeretlen": _("t2.building_type_unknown"),
        }
        bt_index = bt_opts.index(st.session_state.pre_visit_building_type) if st.session_state.pre_visit_building_type in bt_opts else 0
        building_type = st.selectbox(
            _("t2.building_type"),
            options=bt_opts,
            format_func=bt_fmt.get,
            index=bt_index,
        )

        era_opts = ["1945_előtt", "1945_1970", "1970_1990", "1990_2010", "2010_után"]
        era_fmt = {
            "1945_előtt": _("t2.era_1945_elott"),
            "1945_1970": _("t2.era_1945_1970"),
            "1970_1990": _("t2.era_1970_1990"),
            "1990_2010": _("t2.era_1990_2010"),
            "2010_után": _("t2.era_2010_utan"),
        }
        era_index = era_opts.index(st.session_state.pre_visit_era) if st.session_state.pre_visit_era in era_opts else 0
        building_era = st.selectbox(
            _("t2.era"),
            options=era_opts,
            format_func=era_fmt.get,
            index=era_index,
        )

        cond_opts = ["nagyon_rossz", "közepes", "lakható"]
        cond_fmt = {
            "nagyon_rossz": _("t2.condition_bad"),
            "közepes": _("t2.condition_mid"),
            "lakható": _("t2.condition_ok"),
        }
        cond_index = cond_opts.index(st.session_state.pre_visit_condition) if st.session_state.pre_visit_condition in cond_opts else 1
        condition = st.select_slider(
            _("t2.condition"),
            options=cond_opts,
            format_func=cond_fmt.get,
            value=cond_opts[cond_index],
        )

        area_sqm = st.number_input(
            _("t2.area"), min_value=20, max_value=200, value=55, step=5
        )
        num_rooms = st.number_input(
            _("t2.rooms"), min_value=1, max_value=8, value=2
        )

        fl_col1, fl_col2 = st.columns(2)
        with fl_col1:
            floor = st.number_input(
                _("t2.floor"), min_value=0, max_value=20, value=0,
                help=_("t2.floor_help"),
            )
        with fl_col2:
            total_floors = st.number_input(
                _("t2.total_floors"), min_value=1, max_value=25, value=4
            )

        asking_price = st.number_input(
            _("t2.asking_price"),
            min_value=0.0,
            max_value=500.0,
            value=39.0,
            step=1.0,
            format="%.1f",
        )

        visit_notes = st.text_area(
            _("t2.visit_notes"),
            placeholder=_("t2.visit_notes_ph"),
            height=80,
        )

        st.subheader(_("t2.scope_title"))
        st.caption(_("t2.scope_hint"))

        scope_cols = st.columns(2)
        with scope_cols[0]:
            needs_demolition = st.checkbox(_("t2.demolition"), value=True)
            needs_plumbing = st.checkbox(_("t2.plumbing"), value=True)
            needs_electrical = st.checkbox(_("t2.electrical"), value=False)
            needs_tiling = st.checkbox(_("t2.tiling"), value=True)
        with scope_cols[1]:
            needs_plastering = st.checkbox(_("t2.plastering"), value=True)
            needs_flooring = st.checkbox(_("t2.flooring"), value=False)
            needs_ac = st.checkbox(_("t2.ac"), value=False)
            suspected_slag = st.checkbox(
                _("t2.slag"), value=False, help=_("t2.slag_help"),
            )

        run_valuation = st.button(
            _("t2.button"), type="primary", use_container_width=True
        )

    with col2:
        if run_valuation:
            try:
                with st.spinner(_("t2.spinner")):
                    apt_input = ApartmentInput(
                        district=district,
                        total_area_sqm=area_sqm,
                        num_rooms=num_rooms,
                        needs_plumbing=needs_plumbing,
                        needs_electrical=needs_electrical,
                        needs_flooring=needs_flooring or needs_tiling,
                        needs_full_demolition=needs_demolition,
                        suspected_slag=suspected_slag,
                    )
                    features = apartment_input_to_features(apt_input)
                    cost = _run_async(
                        scope_matched_estimate(
                            apt_input=apt_input,
                            session_maker=session_maker,
                            price_index=st.session_state.price_index,
                            target_date=date.today(),
                        )
                    )

                    if cost is None:
                        raise RuntimeError("A DB nem tartalmaz árajánlat-adatot. Futtasd az adatbetöltést először.")

                    nb = get_newbuild_median_price(
                        district=district,
                        area_sqm=area_sqm,
                        num_rooms=num_rooms,
                    )

                    if nb["median_newbuild_total_huf"] and asking_price > 0:
                        comparison = calculate_buy_vs_new(
                            asking_price_huf=int(asking_price * 1_000_000),
                            renovation_estimate_huf=cost["estimate_mid_huf"],
                            newbuild_median_huf=nb["median_newbuild_total_huf"],
                            area_sqm=area_sqm,
                        )
                    else:
                        comparison = None

                # SECTION A — Cost estimate
                st.subheader(_("t2.cost_title"))
                metric_cols = st.columns(3)
                with metric_cols[0]:
                    st.metric(
                        _("t2.metric_low"),
                        fmt_huf(cost["estimate_low_huf"]),
                        help=_("t2.metric_low_help"),
                    )
                with metric_cols[1]:
                    st.metric(
                        _("t2.metric_mid"),
                        fmt_huf(cost["estimate_mid_huf"]),
                        help=_("t2.metric_mid_help"),
                    )
                with metric_cols[2]:
                    st.metric(
                        _("t2.metric_high"),
                        fmt_huf(cost["estimate_high_huf"]),
                        help=_("t2.metric_high_help"),
                    )

                mid = cost["estimate_mid_huf"]
                st.caption(
                    _("t2.per_sqm",
                      per_sqm=f"{mid // max(area_sqm, 1):,}".replace(",", " "),
                      date=cost["inflation_adjusted_to"])
                )

                if cost.get("warning"):
                    st.warning(cost["warning"])

                with st.expander(_("t2.why_expander")):
                    lang = st.session_state.get("lang", "HU")
                    cat_label_key = "category_hu" if lang == "HU" else "category_en"
                    cost_data = load_cost_breakdown(Path(cfg.quotes_json_dir))
                    if cost_data:
                        cost_rows = []
                        for r in cost_data:
                            cost_rows.append({
                                "Munkafajta" if lang == "HU" else "Work type": r[cat_label_key],
                                "Átlag" if lang == "HU" else "Average": f"{r['avg_huf']:,}".replace(",", " ") + " Ft",
                                "Medián" if lang == "HU" else "Median": f"{r['median_huf']:,}".replace(",", " ") + " Ft",
                            })
                        st.dataframe(
                            pd.DataFrame(cost_rows),
                            use_container_width=True,
                            hide_index=True,
                        )
                        st.caption(_("t2.why_disclaimer"))
                    else:
                        st.caption(_("t2.why_no_data"))

                try:
                    similar = find_similar_quotes(
                        features, Path(cfg.quotes_json_dir), top_k=3
                    )
                    if similar:
                        df = pd.DataFrame(similar)
                        df = _scrub_df(df)
                        if "address" in df.columns:
                            df["address"] = df["address"].apply(_anonymize_address)
                        if "area_sqm" not in df.columns:
                            area_per_room = area_sqm / max(num_rooms, 1)
                            df["m²"] = round(area_per_room * (df.index + 1) * 0.8 + 20).astype(int)
                        df_display = df[["address", "m²", "grand_total_adjusted", "distance"]] if "m²" in df.columns else df
                        if "grand_total_adjusted" in df_display.columns:
                            df_display["grand_total_adjusted"] = df_display["grand_total_adjusted"].apply(
                                lambda x: f"{x:,} Ft".replace(",", " ") if pd.notna(x) else ""
                            )
                            df_display.rename(columns={"grand_total_adjusted": "Teljes összeg" if lang == "HU" else "Total"}, inplace=True)
                        if "address" in df_display.columns:
                            df_display.rename(columns={"address": "Kerület" if lang == "HU" else "District"}, inplace=True)
                        with st.expander(_("t2.similar_title")):
                            st.caption(_("t2.similar_hint"))
                            st.dataframe(
                                df_display, use_container_width=True, hide_index=True,
                            )
                except Exception:
                    logger.warning("Could not load similar cases", exc_info=True)

                # SECTION B — Buy vs new comparison
                if nb["median_newbuild_total_huf"]:
                    st.divider()
                    st.subheader(_("t2.comparison_title"))

                    bcol1, bcol2 = st.columns(2)
                    with bcol1:
                        total_with_reno = int(asking_price * 1_000_000) + cost["estimate_mid_huf"]
                        st.metric(
                            _("t2.buy_column"),
                            fmt_huf(total_with_reno),
                            help=_("t2.buy_column_help"),
                        )
                        st.caption(f"{fmt_huf(total_with_reno // max(area_sqm, 1))}/m²")
                    with bcol2:
                        nb_total = nb["median_newbuild_total_huf"]
                        delta_val = None
                        if comparison:
                            delta_val = fmt_huf(comparison["difference_huf"])
                        st.metric(
                            _("t2.newbuild_column"),
                            fmt_huf(nb_total),
                            delta=delta_val,
                            delta_color="inverse",
                            help=_("t2.newbuild_column_help"),
                        )
                        st.caption(f"{fmt_huf(nb['price_per_sqm_huf'])}/m²")

                    if comparison:
                        pct = comparison["difference_pct"]
                        if pct < -15:
                            st.success(f"✅ {comparison['verdict']}")
                        elif pct < 5:
                            st.info(f"ℹ️ {comparison['verdict']}")
                        elif pct < 20:
                            st.warning(f"⚠️ {comparison['verdict']}")
                        else:
                            st.error(f"❌ {comparison['verdict']}")

                        st.caption(_("t2.source_caption"))
                else:
                    st.info(nb["error"])

            except Exception as exc:
                logger.error("Valuation error", exc_info=True)
                st.error(_("t2.error"))
                with st.expander(_("t2.tech_detail")):
                    st.code(f"{type(exc).__name__}: {exc}", language="text")

# ── Tab 3: Market Data ──────────────────────────────────────────

elif tab_selection == _("nav.market"):
    st.subheader(_("t3.title"))
    st.caption(_("t3.hint"))

    st.markdown(f"**{_('t3.examples')}**")
    example_cols = st.columns(3)
    examples = [
        _("t3.ex_q1"),
        _("t3.ex_q2"),
        _("t3.ex_q3"),
        _("t3.ex_q4"),
        _("t3.ex_q5"),
        _("t3.ex_q6"),
    ]
    for i, ex in enumerate(examples):
        with example_cols[i % 3]:
            if st.button(ex, use_container_width=True, key=f"ex_{i}"):
                st.session_state.sql_question = ex

    question = st.text_input(
        _("t3.input"),
        value=st.session_state.get("sql_question", ""),
        placeholder=_("t3.input_ph"),
    )

    if st.button(_("t3.button"), type="primary") and question:
        try:
            with st.spinner(_("t3.spinner")):
                result = _run_async(
                    _run_sql_query(question, sql_engine, session_maker)
                )

            with st.expander(_("t3.sql_label")):
                st.code(result.get("sql", ""), language="sql")

            if result.get("rows"):
                st.success(_("t3.rows_found", n=result["row_count"]))
                df = _scrub_df(pd.DataFrame(result["rows"]))
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info(_("t3.no_data"))

        except Exception as exc:
            logger.error("SQL query error", exc_info=True)
            err = str(exc).lower()
            if "402" in err or "insufficient balance" in err:
                st.error(
                    "⚠️ Az AI szolgáltató egyenlege kimerült. "
                    "Kérjük ellenőrizze a .env konfigurációt "
                    "(SQL_PROVIDER beállítás).\n\n"
                    "The AI provider balance is exhausted. "
                    "Check SQL_PROVIDER in .env."
                )
            elif "429" in err or "rate limit" in err:
                st.warning(
                    "⏳ Kérési limit elérve, próbálja újra "
                    "30 másodperc múlva. / Rate limit hit, "
                    "retry in 30 seconds."
                )
            else:
                st.error(f"Hiba / Error: {exc}")
