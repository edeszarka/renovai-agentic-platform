"""
RenovAI — Streamlit frontend for the renovation advisory system.
Calls renovai/ Python functions directly (no MCP server needed).
"""

import asyncio
import concurrent.futures
import logging
import os
import re
import uuid

from pathlib import Path

from orchestrator.handlers import handle_expert_interview, handle_construction_planning
from orchestrator.policy_service import PolicyService
from orchestrator.skill_registry import SkillRegistry

import streamlit as st

from renovai.api.config import AppConfig
from renovai.ingestion.inflation_calc import load_price_index as _load_price_index
from renovai.db.session import get_engine, get_session_maker

logger = logging.getLogger("renovai-streamlit")

LANG = {
    "HU": {
        "page_title": "RenovAI — Felújítási tanácsadó",
        "sidebar.title": "🏠 RenovAI",
        "sidebar.tagline": "Szakértő felújítási tanácsadó",
        "nav.tab1": "🔍 Vevői Felkészítő",
        "nav.tab2": "📋 Felújítási Tervező",
        "sidebar.data_source": "Az adatok 30 valós felújítási árajánlaton alapulnak, 2023–2026 között, Budapest különböző kerületeiben.",
        "sidebar.disclaimer": "⚠️ Ez egy POC eszköz. Az eredmények tájékoztató jellegűek, nem helyettesítik a szakértői véleményt.",
        "lang.label": "Nyelv",
        "tab1.subtitle": "Vevői Felkészítő — Kérdések az eladóhoz",
        "tab1.desc": "Töltsd ki a lakás adatait a hirdetés alapján, és készülj fel szakszerű kérdésekkel a megtekintésre.",
        "tab1.district": "Kerület",
        "tab1.district_fmt": "Budapest {}. kerület",
        "tab1.area": "Alapterület (m²)",
        "tab1.rooms": "Szobák száma",
        "tab1.era": "Építési időszak (közelítő)",
        "tab1.era_unknown": "Ismeretlen",
        "tab1.era_1900_elott": "1900 előtt",
        "tab1.era_1900_1945": "1900–1945",
        "tab1.era_1945_1970": "1945–1970",
        "tab1.era_1970_1990": "1970–1990",
        "tab1.era_1990_utan": "1990 után",
        "tab1.building_type": "Épület típusa",
        "tab1.building_type_brick": "Téglaépület",
        "tab1.building_type_panel": "Panelház",
        "tab1.building_type_new": "Újépítés",
        "tab1.building_type_unknown": "Ismeretlen",
        "tab1.condition": "Jelenlegi állapot",
        "tab1.condition_bad": "😰 Nagyon rossz",
        "tab1.condition_mid": "😐 Közepes",
        "tab1.condition_ok": "🙂 Lakható",
        "tab1.floor_construction": "Födém szerkezete (ha ismert)",
        "tab1.floor_unknown": "Nem ismert",
        "tab1.floor_acel_gerendas": "Tégla boltíves / acél gerendás",
        "tab1.floor_beton_talcas": "Betontálcás födém",
        "tab1.floor_panel": "Panel födém",
        "tab1.wall_condition": "Fal állapota (ha ismert)",
        "tab1.wall_unknown": "Nem ismert",
        "tab1.wall_fureszporos": "Fűrészporos tapéta",
        "tab1.wall_normal": "Normál vakolt fal",
        "tab1.wall_40_60_vakolat": "40–60 éves vakolat",
        "tab1.listing_notes": "Mit írt a hirdetés? (opcionális)",
        "tab1.listing_notes_ph": "pl. felújított fürdő, régi nyílászárók, gázkonvektor, erkély...",
        "tab1.button": "Felkészítő jelentés →",
        "tab1.spinner": "Szakértői elemzés összeállítása...",
        "tab1.error": "Hiba történt a jelentés generálása során.",
        "tab1.red_flags_title": "🚩 Piros zászlók ({n} tétel)",
        "tab1.questions_title": "❓ Kérdések az eladónak ({n} tétel)",
        "tab1.checklist_title": "🔍 Helyszíni ellenőrzési lista ({n} tétel)",
        "tab1.experts_title": "👷 Javasolt szakértők",
        "tab1.risk_label": "Összesített kockázat: **{risk}**",
        "tab1.summary_label": "Összefoglaló",
        "tab1.confidence_label": "Megbízhatóság: {score}",
        "tab1.green_team_label": "⚠️ Ez az eredmény felülvizsgálatot igényel (Green Team ellenőrzés).",
        "tab1.green_team_hint": "Az eredmény megjelenik, de szakértői áttekintés javasolt a döntés előtt.",

        "tab2.subtitle": "Felújítási Tervező — Részletes terv és költségvetés",
        "tab2.desc": "Add meg a lakás adatait, és válaszd ki a felújítás típusát. A rendszer ütemtervet és költségbecslést készít.",
        "tab2.scope_label": "Felújítás típusa",
        "tab2.scope_full": "Teljes felújítás",
        "tab2.scope_partial": "Részleges felújítás",
        "tab2.scope_full_help": "Minden fázis: bontás, kőműves, gépészet, vakolás, burkolás, festés",
        "tab2.scope_partial_help": "Csak a kiválasztott munkafázisok",
        "tab2.district": "Kerület",
        "tab2.district_fmt": "Budapest {}. kerület",
        "tab2.area": "Alapterület (m²)",
        "tab2.rooms": "Szobák száma",
        "tab2.era": "Építési időszak",
        "tab2.era_1900_elott": "1900 előtt",
        "tab2.era_1900_1945": "1900–1945",
        "tab2.era_1945_1970": "1945–1970",
        "tab2.era_1970_1990": "1970–1990",
        "tab2.era_1990_utan": "1990 után",
        "tab2.era_unknown": "Ismeretlen",
        "tab2.building_type": "Épület típusa",
        "tab2.building_type_brick": "Téglaépület",
        "tab2.building_type_panel": "Panelház",
        "tab2.building_type_new": "Újépítés",
        "tab2.building_type_unknown": "Ismeretlen",
        "tab2.floor_construction": "Födém szerkezete (ha ismert)",
        "tab2.floor_unknown": "Nem ismert",
        "tab2.floor_acel_gerendas": "Tégla boltíves / acél gerendás",
        "tab2.floor_beton_talcas": "Betontálcás födém",
        "tab2.floor_panel": "Panel födém",
        "tab2.wall_condition": "Fal állapota (ha ismert)",
        "tab2.wall_unknown": "Nem ismert",
        "tab2.wall_fureszporos": "Fűrészporos tapéta",
        "tab2.wall_normal": "Normál vakolt fal",
        "tab2.wall_40_60_vakolat": "40–60 éves vakolat",
        "tab2.phases_title": "📋 Felújítási fázisok",
        "tab2.phase_step": "{step}. {name}",
        "tab2.phase_desc": "{desc}",
        "tab2.phase_material": "💰 Anyagköltség: {cost}",
        "tab2.phase_labor": "🔧 Munkadíj: {cost}",
        "tab2.total_title": "💰 Becsült összköltség",
        "tab2.total_low": "Alacsony becslés",
        "tab2.total_mid": "📌 Várható költség",
        "tab2.total_high": "Magas becslés",
        "tab2.warnings_title": "⚠️ Figyelmeztetések",
        "tab2.button": "Terv generálása →",
        "tab2.spinner": "Felújítási terv összeállítása...",
        "tab2.error": "Hiba történt a terv generálása során.",
        "tab2.why_title": "💡 Költségindoklás (Vibe Diff)",
        "tab2.advanced_header": "💡 Részletes beállítások",
        "tab2.advanced_caption": "Extra paraméterek a pontosabb költségbecsléshez",
        "tab2.ceiling_height": "Belmagasság (m)",
        "tab2.ceiling_height_help": "Alapértelmezett: 2.75 m. 3.2 m felett a festés/vakolás munkadíj nő.",
        "tab2.floor_number": "Emelet",
        "tab2.floor_number_help": "Hányadik emeleten van a lakás (lift nélkül többletköltség)",
        "tab2.elevator": "Lift típusa",
        "tab2.elevator_unknown": "Nem ismert",
        "tab2.elevator_none": "Nincs lift",
        "tab2.elevator_small": "Van lift (<240 kg, személylift)",
        "tab2.elevator_large": "Van lift (≥240 kg, teherlift)",
        "tab2.gas_heating": "Egyedi gázfűtés (cirkó/gázkazán)",
        "tab2.gas_heating_help": "Gázfűtés esetén kötelező kéménytechnikai szakember bevonása",
        "tab2.market_comparison": "📊 Piaci összehasonlítás (Ft/m²)",
        "tab2.market_comparison_help": "Az adatbázisban lévő összes árajánlatból számolt négyzetméterárak",
        "tab2.market_avg": "Átlagár / m²",
        "tab2.market_median": "Mediánár / m²",
        "tab2.market_max": "Max ár / m²",
        "tab2.market_min": "Min ár / m²",
        "tab2.market_count": "Ajánlatok száma",
        "tab2.market_your_estimate": "Becsült átlagár / m²",
        "tab2.market_scale_avg": "Korpusz átlag →",
        "tab2.market_scale_median": "Korpusz medián →",
        "tab2.market_scale_plan": "Ütemterv →",
        "tab2.market_unit": "Ft/m²",
        "priority.kritikus": "kritikus",
        "priority.fontos": "fontos",
        "priority.erdemes": "érdemes_megnézni",
        "anon.address": "Budapest {dist}. kerület, ~{area} m²",
    },
    "EN": {
        "page_title": "RenovAI — Renovation Advisor",
        "sidebar.title": "🏠 RenovAI",
        "sidebar.tagline": "Expert renovation advisory tool",
        "nav.tab1": "🔍 Buyer Preparation",
        "nav.tab2": "📋 Renovation Planner",
        "sidebar.data_source": "Data is based on 30 real renovation quotes from 2023–2026 across Budapest districts.",
        "sidebar.disclaimer": "⚠️ This is a POC tool. Results are for informational purposes only and do not replace professional advice.",
        "lang.label": "Language",
        "tab1.subtitle": "Buyer Preparation — Questions for the Seller",
        "tab1.desc": "Fill in the apartment details from the listing and prepare expert-level questions for the site visit.",
        "tab1.district": "District",
        "tab1.district_fmt": "Budapest District {}",
        "tab1.area": "Floor Area (m²)",
        "tab1.rooms": "Number of Rooms",
        "tab1.era": "Building Era (approx.)",
        "tab1.era_unknown": "Unknown",
        "tab1.era_1900_elott": "Before 1900",
        "tab1.era_1900_1945": "1900–1945",
        "tab1.era_1945_1970": "1945–1970",
        "tab1.era_1970_1990": "1970–1990",
        "tab1.era_1990_utan": "After 1990",
        "tab1.building_type": "Building Type",
        "tab1.building_type_brick": "Brick Building",
        "tab1.building_type_panel": "Panel Block",
        "tab1.building_type_new": "New Build",
        "tab1.building_type_unknown": "Unknown",
        "tab1.condition": "Current Condition",
        "tab1.condition_bad": "😰 Very Poor",
        "tab1.condition_mid": "😐 Fair",
        "tab1.condition_ok": "🙂 Livable",
        "tab1.floor_construction": "Floor Construction (if known)",
        "tab1.floor_unknown": "Unknown",
        "tab1.floor_acel_gerendas": "Brick arch / steel beam",
        "tab1.floor_beton_talcas": "Concrete tray slab",
        "tab1.floor_panel": "Panel slab",
        "tab1.wall_condition": "Wall Condition (if known)",
        "tab1.wall_unknown": "Unknown",
        "tab1.wall_fureszporos": "Sawdust wallpaper",
        "tab1.wall_normal": "Normal plastered wall",
        "tab1.wall_40_60_vakolat": "40–60 year old plaster",
        "tab1.listing_notes": "What does the listing say? (optional)",
        "tab1.listing_notes_ph": "e.g. renovated bathroom, old windows, gas convector, balcony...",
        "tab1.button": "Generate Preparation Report →",
        "tab1.spinner": "Compiling expert analysis...",
        "tab1.error": "An error occurred during report generation.",
        "tab1.red_flags_title": "🚩 Red Flags ({n} items)",
        "tab1.questions_title": "❓ Questions for the Seller ({n} items)",
        "tab1.checklist_title": "🔍 On-site Inspection Checklist ({n} items)",
        "tab1.experts_title": "👷 Recommended Experts",
        "tab1.risk_label": "Overall Risk: **{risk}**",
        "tab1.summary_label": "Summary",
        "tab1.confidence_label": "Confidence: {score}",
        "tab1.green_team_label": "⚠️ This result requires human review (Green Team check).",
        "tab1.green_team_hint": "The result is shown, but an expert review is recommended before deciding.",

        "tab2.subtitle": "Renovation Planner — Detailed Plan & Budget",
        "tab2.desc": "Enter the apartment details and select the renovation type. The system generates a phased plan and cost estimate.",
        "tab2.scope_label": "Renovation Type",
        "tab2.scope_full": "Full Renovation",
        "tab2.scope_partial": "Partial Renovation",
        "tab2.scope_full_help": "All phases: demolition, masonry, rough-in, plastering, flooring, painting",
        "tab2.scope_partial_help": "Only selected work phases",
        "tab2.district": "District",
        "tab2.district_fmt": "Budapest District {}",
        "tab2.area": "Floor Area (m²)",
        "tab2.rooms": "Number of Rooms",
        "tab2.era": "Building Era",
        "tab2.era_1900_elott": "Before 1900",
        "tab2.era_1900_1945": "1900–1945",
        "tab2.era_1945_1970": "1945–1970",
        "tab2.era_1970_1990": "1970–1990",
        "tab2.era_1990_utan": "After 1990",
        "tab2.era_unknown": "Unknown",
        "tab2.building_type": "Building Type",
        "tab2.building_type_brick": "Brick Building",
        "tab2.building_type_panel": "Panel Block",
        "tab2.building_type_new": "New Build",
        "tab2.building_type_unknown": "Unknown",
        "tab2.floor_construction": "Floor Construction (if known)",
        "tab2.floor_unknown": "Unknown",
        "tab2.floor_acel_gerendas": "Brick arch / steel beam",
        "tab2.floor_beton_talcas": "Concrete tray slab",
        "tab2.floor_panel": "Panel slab",
        "tab2.wall_condition": "Wall Condition (if known)",
        "tab2.wall_unknown": "Unknown",
        "tab2.wall_fureszporos": "Sawdust wallpaper",
        "tab2.wall_normal": "Normal plastered wall",
        "tab2.wall_40_60_vakolat": "40–60 year old plaster",
        "tab2.phases_title": "📋 Renovation Phases",
        "tab2.phase_step": "{step}. {name}",
        "tab2.phase_desc": "{desc}",
        "tab2.phase_material": "💰 Material: {cost}",
        "tab2.phase_labor": "🔧 Labor: {cost}",
        "tab2.total_title": "💰 Estimated Total Cost",
        "tab2.total_low": "Low Estimate",
        "tab2.total_mid": "📌 Expected Cost",
        "tab2.total_high": "High Estimate",
        "tab2.warnings_title": "⚠️ Warnings",
        "tab2.button": "Generate Plan →",
        "tab2.spinner": "Compiling renovation plan...",
        "tab2.error": "An error occurred during plan generation.",
        "tab2.why_title": "💡 Cost Explanation (Vibe Diff)",
        "tab2.advanced_header": "💡 Advanced Settings",
        "tab2.advanced_caption": "Extra parameters for more accurate cost estimation",
        "tab2.ceiling_height": "Ceiling Height (m)",
        "tab2.ceiling_height_help": "Default: 2.75 m. Above 3.2 m, painting/plastering labor costs increase.",
        "tab2.floor_number": "Floor",
        "tab2.floor_number_help": "Which floor is the apartment on (no elevator adds surcharge)",
        "tab2.elevator": "Elevator Type",
        "tab2.elevator_unknown": "Unknown",
        "tab2.elevator_none": "No elevator",
        "tab2.elevator_small": "Has elevator (<240 kg, passenger)",
        "tab2.elevator_large": "Has elevator (≥240 kg, freight)",
        "tab2.gas_heating": "Individual gas heating (circulator/boiler)",
        "tab2.gas_heating_help": "Gas heating requires a chimney technician specialist",
        "tab2.market_comparison": "📊 Market Comparison (Ft/m²)",
        "tab2.market_comparison_help": "Per-square-meter prices from all quotes in the database",
        "tab2.market_avg": "Average / m²",
        "tab2.market_median": "Median / m²",
        "tab2.market_max": "Max price / m²",
        "tab2.market_min": "Min price / m²",
        "tab2.market_count": "Number of quotes",
        "tab2.market_your_estimate": "Estimated avg / m²",
        "tab2.market_scale_avg": "Corpus avg →",
        "tab2.market_scale_median": "Corpus median →",
        "tab2.market_scale_plan": "Plan estimate →",
        "tab2.market_unit": "Ft/m²",
        "priority.kritikus": "critical",
        "priority.fontos": "important",
        "priority.erdemes": "worth checking",
        "anon.address": "Budapest Dist. {dist}, ~{area} m²",
    },
}






st.set_page_config(
    page_title="RenovAI — Felújítási tanácsadó",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

cfg = AppConfig()

ERAS_TAB1 = {
    "1950": "1955",
    "1960": "1965",
    "1970": "1975",
    "1980": "1985",
    "1990": "1995",
    "2000": "2005",
    "2010": "2015",
    "2020": "2025",
}

ERAS_TAB2 = {
    "1950": "1980",
    "1960": "1985",
    "1970": "1990",
    "1980": "1995",
    "1990": "2000",
    "2000": "2005",
    "2010": "2015",
    "2020": "2025",
}


def _init_handler():
    """Initialize policy, registry, and trace_id from session state or defaults."""
    if "policy" in st.session_state and "registry" in st.session_state and "trace_id" in st.session_state:
        return (
            st.session_state.policy,
            st.session_state.registry,
            st.session_state.trace_id,
        )
    from renovai.api.config import AppConfig
    from renovai.db.session import get_engine, get_session_maker
    import uuid
    import logging
    logger = logging.getLogger("renovai-streamlit")
    cfg = AppConfig()
    engine = get_engine(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db"))
    sm = get_session_maker(engine)
    policy = PolicyService(logger=logger, cfg=cfg, session_maker=sm)
    registry = SkillRegistry()
    registry.load_all()
    trace_id = f"st-{uuid.uuid4().hex[:12]}"
    st.session_state.policy = policy
    st.session_state.registry = registry
    st.session_state.trace_id = trace_id
    return policy, registry, trace_id


def _init_session_state():
    """Initialize session state variables required by the app."""
    if "price_index" not in st.session_state:
        from renovai.ingestion.inflation_calc import load_price_index as _load_price_index
        st.session_state.price_index = _load_price_index(
            Path(st.session_state.cfg.inflation_materials_csv),
            Path(st.session_state.cfg.inflation_labor_csv),
        )
    if "lang" not in st.session_state:
        st.session_state.lang = "HU"


def _(key: str, **kwargs) -> str:
    lang = st.session_state.get("lang", "HU")
    val = LANG.get(lang, LANG["HU"]).get(key, key)
    if kwargs:
        return val.format(**kwargs)
    return val


def fmt_huf(n: int) -> str:
    return f"{n:,} Ft".replace(",", " ")


if "price_index" not in st.session_state:
    st.session_state.price_index = load_price_index()
if "lang" not in st.session_state:
    st.session_state.lang = "HU"
if "session_maker" not in st.session_state:
    st.session_state.session_maker = load_sql_resources()

session_maker = st.session_state.session_maker

# ── Sidebar ──────────────────────────────────────────────────────

with st.sidebar:
    st.markdown(f"## {_('sidebar.title')}")
    st.markdown(f"*{_('sidebar.tagline')}*")
    st.divider()

    tab_selection = st.radio(
        "nav",
        [_("nav.tab1"), _("nav.tab2")],
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

if tab_selection == _("nav.tab1"):
    col1, col2 = st.columns(2)

    with col1:
        st.subheader(_("tab1.subtitle"))
        st.caption(_("tab1.desc"))

        district = st.selectbox(
            _("tab1.district"),
            options=list(range(1, 23)),
            format_func=lambda x: _("tab1.district_fmt").format(x),
        )

        area_sqm = st.number_input(
            _("tab1.area"), min_value=20, max_value=200, value=55, step=5
        )
        num_rooms = st.number_input(
            _("tab1.rooms"), min_value=1, max_value=8, value=2
        )

        bt_opts = ["tégla", "panel", "újépítés", "ismeretlen"]
        bt_fmt = {
            "tégla": _("tab1.building_type_brick"),
            "panel": _("tab1.building_type_panel"),
            "újépítés": _("tab1.building_type_new"),
            "ismeretlen": _("tab1.building_type_unknown"),
        }
        building_type = st.selectbox(
            _("tab1.building_type"),
            options=bt_opts,
            format_func=bt_fmt.get,
        )

        era_opts = ["1900_elott", "1900_1945", "1945_1970", "1970_1990", "1990_utan"]
        era_fmt = {
            "1900_elott": _("tab1.era_1900_elott"),
            "1900_1945": _("tab1.era_1900_1945"),
            "1945_1970": _("tab1.era_1945_1970"),
            "1970_1990": _("tab1.era_1970_1990"),
            "1990_utan": _("tab1.era_1990_utan"),
        }
        era_key = st.selectbox(
            _("tab1.era"),
            options=era_opts,
            format_func=era_fmt.get,
        )

        cond_opts = ["nagyon_rossz", "közepes", "lakható"]
        cond_fmt = {
            "nagyon_rossz": _("tab1.condition_bad"),
            "közepes": _("tab1.condition_mid"),
            "lakható": _("tab1.condition_ok"),
        }
        condition = st.select_slider(
            _("tab1.condition"),
            options=cond_opts,
            format_func=cond_fmt.get,
            value="közepes",
        )

        floor_opts = ["ismeretlen", "acél_gerendás", "betontálcás", "panel"]
        floor_fmt = {
            "ismeretlen": _("tab1.floor_unknown"),
            "acél_gerendás": _("tab1.floor_acel_gerendas"),
            "betontálcás": _("tab1.floor_beton_talcas"),
            "panel": _("tab1.floor_panel"),
        }
        floor_construction = st.selectbox(
            _("tab1.floor_construction"),
            options=floor_opts,
            format_func=floor_fmt.get,
        )

        wall_opts = ["ismeretlen", "fűrészporos_tapéta", "normál_vakolt", "40_60_éves_vakolat"]
        wall_fmt = {
            "ismeretlen": _("tab1.wall_unknown"),
            "fűrészporos_tapéta": _("tab1.wall_fureszporos"),
            "normál_vakolt": _("tab1.wall_normal"),
            "40_60_éves_vakolat": _("tab1.wall_40_60_vakolat"),
        }
        wall_condition_sel = st.selectbox(
            _("tab1.wall_condition"),
            options=wall_opts,
            format_func=wall_fmt.get,
        )

        listing_notes = st.text_area(
            _("tab1.listing_notes"),
            placeholder=_("tab1.listing_notes_ph"),
            height=100,
        )

        run_prep = st.button(
            _("tab1.button"), type="primary", use_container_width=True
        )

    with col2:
        if run_prep:
            with st.spinner(_("tab1.spinner")):
                try:
                    # Map era_key to a numeric year for the handler
                    era_year_map = {
                        "1900_elott": "1890",
                        "1900_1945": "1920",
                        "1945_1970": "1955",
                        "1970_1990": "1980",
                        "1990_utan": "2000",
                    }
                    era_year = ERAS_TAB1.get(era_key, "1955")

                    wall_condition = {}
                    if wall_condition_sel == "fűrészporos_tapéta":
                        wall_condition["wallpaper"] = True
                    if wall_condition_sel == "40_60_éves_vakolat":
                        wall_condition["old_plaster"] = True

                    floor_map = {
                        "ismeretlen": "",
                        "acél_gerendás": "acél gerendás",
                        "betontálcás": "betontálcás",
                        "panel": "panel födém",
                    }

                    has_slag = floor_construction == "acél_gerendás" and era_key in ("1900_elott", "1900_1945", "1945_1970")

                    params = {
                        "district": district,
                        "area_sqm": area_sqm,
                        "num_rooms": num_rooms,
                        "building_type": building_type,
                        "building_era": era_year,
                        "floor_construction": floor_map.get(floor_construction, ""),
                        "wall_condition": wall_condition,
                        "scope_flags": {"slag": has_slag},
                        "has_seen_in_person": False,
                        "condition": condition,
                    }

                    policy, registry, trace_id = _init_handler()
                    registry = SkillRegistry()
                    registry.load_all()
                    trace_id = f"st-{uuid.uuid4().hex[:12]}"

                    result = _run_async(
                        handle_expert_interview(params, policy, registry, trace_id)
                    )

                    if result.get("status") != "ok":
                        st.error(_("tab1.error"))
                        st.code(str(result.get("error", "")), language="text")
                    else:
                        data = result["data"]
                        red_flags = data.get("red_flags", [])
                        questions = data.get("questions_for_seller", [])
                        checklist = data.get("inspection_checklist", [])
                        experts = data.get("recommended_experts", [])
                        risk = data.get("overall_risk", "LOW")
                        summary = data.get("summary_hu", "")
                        conf = data.get("confidence", {})

                        risk_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
                        st.subheader(
                            f"{risk_icon.get(risk, '🟢')} "
                            + _("tab1.risk_label", risk=risk)
                        )
                        st.markdown(f"> {summary}")

                        if red_flags:
                            with st.expander(
                                _("tab1.red_flags_title", n=len(red_flags)),
                                expanded=len(red_flags) > 0,
                            ):
                                for rf in red_flags:
                                    risk_icon_r = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
                                    st.markdown(
                                        f"**{risk_icon_r.get(rf.get('risk', 'MEDIUM'), '🟡')} "
                                        f"{rf['title']}**"
                                    )
                                    st.markdown(f"{rf.get('detail', '')}")
                                    if rf.get('estimated_cost'):
                                        st.markdown(f"💰 *Becsült költség:* {rf['estimated_cost']}")
                                    if rf.get('action'):
                                        st.markdown(f"✅ *Javaslat:* {rf['action']}")
                                    st.divider()

                        if questions:
                            with st.expander(
                                _("tab1.questions_title", n=len(questions)),
                                expanded=True,
                            ):
                                for q in questions:
                                    st.markdown(f"- {q}")

                        if checklist:
                            with st.expander(
                                _("tab1.checklist_title", n=len(checklist)),
                            ):
                                for c in checklist:
                                    st.markdown(f"- {c}")

                        if experts:
                            with st.expander(
                                _("tab1.experts_title"),
                            ):
                                for e in experts:
                                    st.markdown(f"- 👷 {e}")

                        if conf:
                            st.caption(
                                _("tab1.confidence_label",
                                  score=f"{conf.get('score', 0):.0%}")
                            )

                        if data.get("needs_intervention"):
                            st.warning(_("tab1.green_team_label"))
                            st.caption(_("tab1.green_team_hint"))

                except Exception as exc:
                    logger.error("Pre-visit report error", exc_info=True)
                    st.error(_("tab1.error"))
                    with st.expander("🔧 Technikai részletek"):
                        st.code(f"{type(exc).__name__}: {exc}", language="text")

# ── Tab 2: Post-visit Valuation ─────────────────────────────────

elif tab_selection == _("nav.tab2"):
    col1, col2 = st.columns(2)

    with col1:
        st.subheader(_("tab2.subtitle"))
        st.caption(_("tab2.desc"))

        renovation_scope = st.radio(
            _("tab2.scope_label"),
            options=["full", "partial"],
            format_func=lambda x: _("tab2.scope_full") if x == "full" else _("tab2.scope_partial"),
            horizontal=True,
            help=_("tab2.scope_full_help") if "full" else _("tab2.scope_partial_help"),
        )

        district = st.selectbox(
            _("tab2.district"),
            options=list(range(1, 23)),
            format_func=lambda x: _("tab2.district_fmt").format(x),
        )

        area_sqm = st.number_input(
            _("tab2.area"), min_value=20, max_value=200, value=55, step=5
        )
        num_rooms = st.number_input(
            _("tab2.rooms"), min_value=1, max_value=8, value=2
        )

        era_opts_t2 = ["1900_elott", "1900_1945", "1945_1970", "1970_1990", "1990_utan", "ismeretlen"]
        era_fmt_t2 = {
            "1900_elott": _("tab2.era_1900_elott"),
            "1900_1945": _("tab2.era_1900_1945"),
            "1945_1970": _("tab2.era_1945_1970"),
            "1970_1990": _("tab2.era_1970_1990"),
            "1990_utan": _("tab2.era_1990_utan"),
            "ismeretlen": _("tab2.era_unknown"),
        }
        era_key_t2 = st.selectbox(
            _("tab2.era"),
            options=era_opts_t2,
            format_func=era_fmt_t2.get,
        )

        bt_opts_t2 = ["tégla", "panel", "újépítés", "ismeretlen"]
        bt_fmt_t2 = {
            "tégla": _("tab2.building_type_brick"),
            "panel": _("tab2.building_type_panel"),
            "újépítés": _("tab2.building_type_new"),
            "ismeretlen": _("tab2.building_type_unknown"),
        }
        building_type_t2 = st.selectbox(
            _("tab2.building_type"),
            options=bt_opts_t2,
            format_func=bt_fmt_t2.get,
        )

        floor_opts_t2 = ["ismeretlen", "acél_gerendás", "betontálcás", "panel"]
        floor_fmt_t2 = {
            "ismeretlen": _("tab2.floor_unknown"),
            "acél_gerendás": _("tab2.floor_acel_gerendas"),
            "betontálcás": _("tab2.floor_beton_talcas"),
            "panel": _("tab2.floor_panel"),
        }
        floor_construction_t2 = st.selectbox(
            _("tab2.floor_construction"),
            options=floor_opts_t2,
            format_func=floor_fmt_t2.get,
        )

        wall_opts_t2 = ["ismeretlen", "fűrészporos_tapéta", "normál_vakolt", "40_60_éves_vakolat"]
        wall_fmt_t2 = {
            "ismeretlen": _("tab2.wall_unknown"),
            "fűrészporos_tapéta": _("tab2.wall_fureszporos"),
            "normál_vakolt": _("tab2.wall_normal"),
            "40_60_éves_vakolat": _("tab2.wall_40_60_vakolat"),
        }
        wall_condition_t2 = st.selectbox(
            _("tab2.wall_condition"),
            options=wall_opts_t2,
            format_func=wall_fmt_t2.get,
        )

        st.markdown("---")
        st.markdown(f"**{_('tab2.advanced_header')}**")
        st.caption(_("tab2.advanced_caption"))
        adv_cols = st.columns(2)
        with adv_cols[0]:
            ceiling_height = st.number_input(
                _("tab2.ceiling_height"),
                min_value=2.0, max_value=6.0, value=2.75, step=0.05,
                format="%.2f",
                help=_("tab2.ceiling_height_help"),
            )
        with adv_cols[1]:
            floor_number = st.number_input(
                _("tab2.floor_number"),
                min_value=1, max_value=50, value=1,
                help=_("tab2.floor_number_help"),
            )

        elevator_opts = ["unknown", "none", "small", "large"]
        elevator_fmt = {
            "unknown": _("tab2.elevator_unknown"),
            "none": _("tab2.elevator_none"),
            "small": _("tab2.elevator_small"),
            "large": _("tab2.elevator_large"),
        }
        elevator_type = st.selectbox(
            _("tab2.elevator"),
            options=elevator_opts,
            format_func=elevator_fmt.get,
        )

        gas_heating = st.checkbox(
            _("tab2.gas_heating"),
            value=False,
            help=_("tab2.gas_heating_help"),
        )

        partial_scope = {}
        if renovation_scope == "partial":
            scope_header = "Munkafázisok kiválasztása" if st.session_state.get("lang", "HU") == "HU" else "Select Work Phases"
            st.subheader(scope_header)
            st.caption("Jelöld be a tervezett munkafázisokat" if st.session_state.get("lang", "HU") == "HU" else "Select the planned work phases")

            # Grouped by work type category
            t = lambda hu, en: hu if st.session_state.get("lang", "HU") == "HU" else en

            st.markdown(f"**{t('Szerkezeti munkák', 'Structural Work')}**")
            scope_cols = st.columns(3)
            with scope_cols[0]:
                partial_scope["demolition"] = st.checkbox(t("Bontás", "Demolition"), value=True)
            with scope_cols[1]:
                partial_scope["masonry"] = st.checkbox(t("Kőműves falazás", "Masonry"), value=True)
            with scope_cols[2]:
                partial_scope["drywall"] = st.checkbox(t("Gipszkarton/álmennyezet", "Drywall/ceiling"), value=False)

            st.markdown(f"**{t('Gépészet', 'Mechanical (MEP)')}**")
            scope_cols = st.columns(3)
            with scope_cols[0]:
                partial_scope["plumbing"] = st.checkbox(t("Víz és fűtés", "Plumbing"), value=False)
            with scope_cols[1]:
                partial_scope["electrical"] = st.checkbox(t("Villanyszerelés", "Electrical"), value=False)
            with scope_cols[2]:
                partial_scope["heating"] = st.checkbox(t("Fűtésrendszer", "Heating system"), value=False)

            st.markdown(f"**{t('Felületek', 'Surfaces')}**")
            scope_cols = st.columns(3)
            with scope_cols[0]:
                partial_scope["plastering"] = st.checkbox(t("Vakolás/glettelés", "Plastering"), value=True)
            with scope_cols[1]:
                partial_scope["flooring"] = st.checkbox(t("Burkolás", "Flooring/tiling"), value=True)
            with scope_cols[2]:
                partial_scope["painting"] = st.checkbox(t("Festés", "Painting"), value=True)

            st.markdown(f"**{t('Szigetelés és nyílászáró', 'Insulation & Openings')}**")
            scope_cols = st.columns(3)
            with scope_cols[0]:
                partial_scope["insulation"] = st.checkbox(t("Szigetelés", "Insulation"), value=False)
            with scope_cols[1]:
                partial_scope["windows_doors"] = st.checkbox(t("Nyílászáró csere", "Windows/doors"), value=False)
            with scope_cols[2]:
                partial_scope["ac"] = st.checkbox(t("Klíma", "AC"), value=False)

            st.markdown(f"**{t('Helyiségek', 'Rooms')}**")
            scope_cols = st.columns(3)
            with scope_cols[0]:
                partial_scope["kitchen"] = st.checkbox(t("Konyhabútor", "Kitchen cabinetry"), value=False)
            with scope_cols[1]:
                partial_scope["bathroom"] = st.checkbox(t("Fürdőszoba", "Bathroom"), value=False)
            with scope_cols[2]:
                partial_scope["built_in_shower"] = st.checkbox(t("Épített zuhany", "Built-in shower"), value=False)

        run_plan = st.button(
            _("tab2.button"), type="primary", use_container_width=True
        )

    with col2:
        if run_plan:
            with st.spinner(_("tab2.spinner")):
                try:
                    era_year_map_t2 = {
                        "1900_elott": "1890",
                        "1900_1945": "1920",
                        "1945_1970": "1955",
                        "1970_1990": "1980",
                        "1990_utan": "2000",
                        "ismeretlen": "1980",
                    }
                    era_year_t2 = ERAS_TAB2.get(era_key_t2, "1980")

                    wall_cond = {}
                    if wall_condition_t2 == "fűrészporos_tapéta":
                        wall_cond["wallpaper"] = True

                    floor_map_t2 = {
                        "ismeretlen": "",
                        "acél_gerendás": "acél gerendás",
                        "betontálcás": "betontálcás",
                        "panel": "panel födém",
                    }

                    scope_flags = {
                        "slag": floor_construction_t2 == "acél_gerendás" and era_key_t2 in ("1900_elott", "1900_1945", "1945_1970"),
                        "built_in_shower": False,
                    }

                    if renovation_scope == "full":
                        scope_flags.update({
                            "demolition": True,
                            "masonry": True,
                            "plumbing": True,
                            "electrical": True,
                            "plastering": True,
                            "flooring": True,
                            "painting": True,
                        })
                    else:
                        scope_flags.update(partial_scope)

                    plan_params = {
                        "area_sqm": area_sqm,
                        "building_type": building_type_t2,
                        "building_era": era_year_t2,
                        "floor_construction": floor_map_t2.get(floor_construction_t2, ""),
                        "wall_condition": wall_cond,
                        "scope_flags": scope_flags,
                        "renovation_scope": renovation_scope,
                        "want_sequence": True,
                        "ceiling_height": ceiling_height,
                        "elevator_type": elevator_type if elevator_type != "unknown" else None,
                        "gas_heating": gas_heating,
                        "floor_number": floor_number,
                    }

                    policy, registry, trace_id = _init_handler()
                    from orchestrator.skill_registry import SkillRegistry

                    policy, registry, trace_id = _init_handler()

                    result = _run_async(
                        handle_construction_planning(plan_params, policy, registry, trace_id)
                    )

                    if result.get("status") != "ok":
                        st.error(_("tab2.error"))
                        st.code(str(result.get("error", "")), language="text")
                    else:
                        data = result["data"]
                        phases = data.get("phases", [])
                        total = data.get("total_estimate", {})
                        warnings = data.get("warnings", [])
                        conf = data.get("confidence", {})

                        # Display phases
                        st.subheader(_("tab2.phases_title"))
                        for phase in phases:
                            step = phase.get("step", 0)
                            name = phase.get("name", "")
                            desc = phase.get("description", "")
                            mat_cost = phase.get("material_cost_range", "")
                            lab_cost = phase.get("labor_cost_range", "")

                            with st.container():
                                st.markdown(f"**{step}. {name}**")
                                st.caption(desc)
                                st.markdown(f"💰 {mat_cost}")
                                st.markdown(f"🔧 {lab_cost}")
                            st.divider()

                        # Display total
                        st.subheader(_("tab2.total_title"))
                        tcols = st.columns(3)
                        with tcols[0]:
                            st.metric(
                                _("tab2.total_low"),
                                fmt_huf(total.get("low_huf", 0)),
                            )
                        with tcols[1]:
                            st.metric(
                                _("tab2.total_mid"),
                                fmt_huf(total.get("mid_huf", 0)),
                            )
                        with tcols[2]:
                            st.metric(
                                _("tab2.total_high"),
                                fmt_huf(total.get("high_huf", 0)),
                            )

                        # Corpus stats
                        try:
                            from renovai.db.quote_stats import compute_per_sqm_stats
                            sm = get_session_maker(get_engine(
                                os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")
                            ))
                            corpus_stats = _run_async(compute_per_sqm_stats(sm))
                        except Exception as exc:
                            logger.warning("Corpus stats unavailable: %s", exc)
                            corpus_stats = None

                        if corpus_stats:
                            o = corpus_stats["overall_per_sqm"]
                            with st.expander(_("tab2.market_comparison"), expanded=True):
                                st.caption(
                                    _("tab2.market_comparison_help")
                                    + f" ({corpus_stats['num_quotes']} {_('tab2.market_count')})"
                                )
                                mcols = st.columns(4)
                                mcols[0].metric(_("tab2.market_avg"), f"{fmt_huf(o['avg'])} {_('tab2.market_unit')}")
                                mcols[1].metric(_("tab2.market_median"), f"{fmt_huf(o['median'])} {_('tab2.market_unit')}")
                                mcols[2].metric(_("tab2.market_min"), f"{fmt_huf(o['min'])} {_('tab2.market_unit')}")
                                mcols[3].metric(_("tab2.market_max"), f"{fmt_huf(o['max'])} {_('tab2.market_unit')}")

                                st.markdown("---")
                                st.markdown(f"**{_('tab2.phases_title')}** — {_('tab2.market_unit')}")

                                cat_rows = []
                                for key, cat in corpus_stats["by_category"].items():
                                    cat_rows.append({
                                        "Munkafázis" if st.session_state.get("lang", "HU") == "HU" else "Work phase":
                                            cat["label_hu"] if st.session_state.get("lang", "HU") == "HU" else cat["label_en"],
                                        _("tab2.market_count"): cat["count"],
                                        _("tab2.market_avg"): f"{fmt_huf(cat['avg_per_sqm'])} {_('tab2.market_unit')}",
                                        _("tab2.market_median"): f"{fmt_huf(cat['median_per_sqm'])} {_('tab2.market_unit')}",
                                        f"{_('tab2.market_min')} → {_('tab2.market_max')}":
                                            f"{fmt_huf(cat['min_per_sqm'])} → {fmt_huf(cat['max_per_sqm'])}",
                                    })
                                st.dataframe(cat_rows, use_container_width=True, hide_index=True)

                                st.markdown("---")
                                st.markdown(f"**{_('tab2.market_your_estimate')}** ({area_sqm} m²)")
                                s_cols = st.columns(3)
                                s_cols[0].metric(
                                    _("tab2.market_scale_avg"),
                                    fmt_huf(o["avg"] * area_sqm),
                                )
                                s_cols[1].metric(
                                    _("tab2.market_scale_median"),
                                    fmt_huf(o["median"] * area_sqm),
                                )
                                s_cols[2].metric(
                                    _("tab2.market_scale_plan"),
                                    fmt_huf(total.get("mid_huf", 0)),
                                )

                        if conf:
                            score = conf.get("score", 0)
                            reasoning = conf.get("reasoning", "")
                            if st.session_state.get("lang", "HU") == "HU":
                                st.caption(f"Megbízhatóság: {score:.0%} — {reasoning}")
                            else:
                                st.caption(f"Confidence: {score:.0%} — {reasoning}")

                        # Display warnings
                        if warnings:
                            st.subheader(_("tab2.warnings_title"))
                            for w in warnings:
                                st.warning(w)

                        # Display mandatory Vibe Diff (Hungarian)
                        vibe = data.get("vibe_diff")
                        if vibe:
                            vibe_hu = vibe.get("explanation_hu", "")
                            vibe_en = vibe.get("explanation_en", "")
                            is_hu = st.session_state.get("lang", "HU") == "HU"
                            lang = st.session_state.get("lang", "HU")
                            from renovai.safety.vibe_diff import VibeDiff, VibeDiffEngine

                            st.divider()
                            with st.expander(
                                "💡 Költségindoklás (Vibe Diff)"
                                if is_hu else
                                "💡 Cost Explanation (Vibe Diff)",
                                expanded=vibe.get("total_exceeds_10m", False),
                            ):
                                st.markdown(vibe_hu if is_hu else vibe_en)

                except Exception as exc:
                    logger.error("Renovation plan error", exc_info=True)
                    st.error(_("tab2.error"))
                    with st.expander("🔧 Technikai részletek" if st.session_state.get("lang", "HU") == "HU" else "🔧 Technical Details"):
                        st.code(f"{type(exc).__name__}: {exc}", language="text")
