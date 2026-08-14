import json
import re
import logging
from google import genai
from google.genai import types
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Literal, Tuple, Dict, Any, Callable
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, RetryError

from ..rag.pipeline import RAGPipeline, RAGResponse
from ..rag.retriever import Chunk
from ..rag.gemini_client import GeminiConfig
from ..ingestion.inflation_models import PriceIndex
from ..predictor.feature_extractor import ApartmentInput, apartment_input_to_features
from ..predictor.price_model import predict, find_similar_quotes

logger = logging.getLogger(__name__)

class ApartmentProfile(BaseModel):
    address_district: int
    floor_area_sqm: float
    num_rooms: int
    building_type: Literal["panel", "tégla", "újépítés", "ismeretlen"]
    building_era_approx: Optional[int]  # canonical representative year (e.g. 1960), not a band key
    current_condition: Literal[
        "nagyon_rossz",     # everything needs replacing
        "közepes",          # partially renovated
        "lakható",          # liveable but dated
    ]
    known_issues: List[str]     # free text, e.g. ["penész a fürdőben", "régi vezetékek"]
    has_seen_in_person: bool    # True if buyer has already visited
    asking_price_million_huf: Optional[float]

class ChecklistItem(BaseModel):
    category: str           # e.g. "Víz és csatorna", "Villanyszerelés"
    item: str               # the specific thing to check / question to ask
    priority: Literal["kritikus", "fontos", "érdemes_megnézni"] = "fontos"
    why: str = ""           # one sentence explanation in Hungarian
    rag_source: Optional[str] = None # source_file that this came from

class AdvisoryReport(BaseModel):
    apartment_profile: ApartmentProfile
    generated_at: datetime

    questions_for_seller: List[ChecklistItem]
    inspection_checklist: List[ChecklistItem]
    red_flags: List[ChecklistItem]

    cost_estimate: dict           # from Module 6 price predictor
    similar_cases: List[dict]     # from find_similar_quotes()

    rag_sources_used: List[str]
    overall_risk: Literal["alacsony", "közepes", "magas"]
    summary_hu: str               # 3–5 sentence executive summary in Hungarian

ADVISORY_QUERY_TEMPLATES = [
    "Mit kell megkérdezni az eladótól {building_type} épületnél vásárlás előtt?",
    "Mire kell figyelni {building_era} épített lakás felújításánál?",
    "Milyen rejtett problémák lehetnek {condition} állapotú lakásnál?",
    "Kohósalak és egyéb aljzat problémák bontáskor mit jelentenek?",
    "Ami nincs benne az árban felújításnál, mire kell számítani?",
    "Közös strangok és vízvezetékek csere mikor szükséges társasházban?",
    "Milyen kérdéseket kell feltenni a vállalkozónak az első találkozón?",
]

ERA_MAP = {
    1930: "1945 előtt",
    1960: "1945 és 1970 között",
    1980: "1970 és 1990 között",
    2000: "1990 és 2010 között",
    2015: "2010 után"
}

def _era_label(era: Optional[int]) -> str:
    """Map a canonical representative year to a human-readable era band."""
    if era is None:
        return "ismeretlen"
    for threshold in (1930, 1960, 1980, 2000, 2015):
        if era <= threshold:
            return ERA_MAP[threshold]
    return ERA_MAP[2015]

CONDITION_MAP = {
    "nagyon_rossz": "nagyon rossz (felújítandó)",
    "közepes": "közepes",
    "lakható": "lakható (régi)"
}

def gather_advisory_context(
    profile: ApartmentProfile,
    rag_pipeline: RAGPipeline
) -> Tuple[str, List[str]]:
    """Runs multiple RAG queries and aggregates context."""
    all_chunks = {}
    
    era_str = _era_label(profile.building_era_approx)
    cond_str = CONDITION_MAP.get(profile.current_condition, "ismeretlen")
    
    for template in ADVISORY_QUERY_TEMPLATES:
        query = template.format(
            building_type=profile.building_type,
            building_era=era_str,
            condition=cond_str
        )
        _, trace = rag_pipeline.query_with_trace(query)
        for chunk in trace.chunks:
            all_chunks[chunk.chunk_id] = chunk
            
    # Assemble context string from unique chunks
    unique_chunks = list(all_chunks.values())
    context_parts = []
    sources = set()
    for i, chunk in enumerate(unique_chunks):
        context_parts.append(f"[FORRÁS {i+1}: {chunk.source_file} | {chunk.section}]\n{chunk.content}\n---")
        sources.add(chunk.source_file)
        
    return "\n\n".join(context_parts), sorted(list(sources))

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=1, max=10),
    retry=retry_if_exception_type(Exception),
)
def _generate_with_retry(client: genai.Client, model: str, prompt: str, config: types.GenerateContentConfig):
    try:
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=config
        )
    except Exception as e:
        err = str(e).lower()
        if "429" in err or "resource_exhausted" in err or "quota" in err:
            logger.warning("Gemini quota exhausted in advisor. Raising immediately (no retry).")
            raise
        if "503" in err or "502" in err:
            logger.warning(f"Gemini transient error ({err[:60]}...). Retrying...")
            raise
        raise

def generate_report(
    profile: ApartmentProfile,
    rag_pipeline: RAGPipeline,
    price_predictor_model_dir: Path,
    price_index: PriceIndex,
    gemini_config: GeminiConfig
) -> AdvisoryReport:
    """Generates the full advisory report."""
    # 1. Gather Context
    rag_context, rag_sources = gather_advisory_context(profile, rag_pipeline)
    
    # 2. Apartment Summary
    era_str = _era_label(profile.building_era_approx)
    cond_str = CONDITION_MAP.get(profile.current_condition, "ismeretlen")
    summary = (
        f"Ingatlan: {profile.floor_area_sqm} m2, {profile.num_rooms} szoba, "
        f"{profile.address_district}. kerület. Típus: {profile.building_type}, "
        f"Korszak: {era_str}, Állapot: {cond_str}. "
        f"Ismert hibák: {', '.join(profile.known_issues)}. "
        f"Személyesen látta: {'Igen' if profile.has_seen_in_person else 'Nem'}."
    )
    
    # 3. Call Gemini for structured checklist
    client = genai.Client(api_key=gemini_config.api_key)
    
    # Ensure model name has 'models/' prefix if it doesn't already
    model_id = gemini_config.model_name
    if not (model_id.startswith("models/") or model_id.startswith("tunedModels/")):
        model_id = f"models/{model_id}"

    prompt = f"""
Te egy tapasztalt magyar ingatlanügynök és felújítási szakértő AI asszisztens vagy.

Egy lakásvásárló a következő ingatlant vizsgálja:
{summary}

Az alábbi felújítási szakmai anyagok és árajánlatok alapján:
{rag_context}

Készíts egy részletes vásárlói tanácsadó jelentést három részben:

## 1. KÉRDÉSEK AZ ELADÓNAK
Listázd a legfontosabb kérdéseket, amelyeket a vásárlónak fel kell tennie az eladónak vagy az ingatlanközvetítőnek. Minden kérdésnél jelöld a prioritást (kritikus/fontos/érdemes_megnézni) és egy mondatban magyarázd el, miért fontos.

## 2. HELYSZÍNI ELLENŐRZÉSI LISTA
Mit kell személyesen megvizsgálni a lakásban? Légy konkrét: melyik helyiségben, mit nézzen meg, mire utal a probléma. Kategóriák: Víz/csatorna, Villany, Fűtés, Szerkezet, Nedvesség/penész, Aljzat, Nyílászárók, Közös területek.

## 3. PIROS ZÁSZLÓK
Melyek azok a jelek, amelyek esetén el kell gondolkodni a vételen, vagy komoly áralku alapját képezik? Különös figyelmet fordíts a rejtett, nem látható problémákra (kohósalak, közös strangok, engedély nélküli átalakítások).

Minden itemhez add meg: [FORRÁS n] hivatkozást ha van rá adat.
Válaszolj kizárólag magyarul.

A végén add meg az eredményt JSON formátumban is, a következő struktúrában (ne használj markdown blokkot a JSON-hoz, csak a nyers szöveget a végén):
{{
  "questions": [{{ "category": "...", "item": "...", "priority": "kritikus/fontos/érdemes_megnézni", "why": "...", "rag_source": "..." }}],
  "inspection": [...],
  "red_flags": [...]
}}

Every item in all three lists MUST include these exact keys: 'category' (string), 'item' (string), 'priority' (one of: kritikus, fontos, érdemes_megnézni), 'why' (one sentence explanation in Hungarian), 'rag_source' (source filename or null). Missing any key is not acceptable.
"""
    
    gen_config = types.GenerateContentConfig(
        temperature=0.2,
    )
    
    # 3b. Call Gemini for structured checklist — with graceful fallback on failure
    try:
        response = _generate_with_retry(client, model_id, prompt, gen_config)
        response_text = response.text
        
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            try:
                report_data = json.loads(json_match.group())
            except json.JSONDecodeError:
                report_data = {"questions": [], "inspection": [], "red_flags": []}
        else:
            report_data = {"questions": [], "inspection": [], "red_flags": []}
            
        q_items = [ChecklistItem(**it) for it in report_data.get("questions", [])]
        i_items = [ChecklistItem(**it) for it in report_data.get("inspection", [])]
        r_items = [ChecklistItem(**it) for it in report_data.get("red_flags", [])]
    except (RetryError, Exception) as e:
        logger.error("Gemini call failed in advisor checklist generation: %s", e)
        report_data = {"questions": [], "inspection": [], "red_flags": []}
        q_items, i_items, r_items = [], [], []
        response_text = ""

    # 4. Price Prediction
    apt_input = ApartmentInput(
        district=profile.address_district,
        total_area_sqm=profile.floor_area_sqm,
        num_rooms=profile.num_rooms,
        building_era=profile.building_era_approx,
        needs_plumbing=True,
        needs_electrical=True,
        needs_flooring=True,
        needs_full_demolition=profile.current_condition == "nagyon_rossz",
        suspected_slag="kohósalak" in "".join(profile.known_issues).lower()
    )
    feat = apartment_input_to_features(apt_input)
    cost_est = predict(feat, price_index, datetime.now().date(), price_predictor_model_dir)
    similar = find_similar_quotes(feat, Path("data/processed/quotes_json/"))
    
    # 5. Risk
    risk = "alacsony"
    if any(it.priority == "kritikus" for it in r_items):
        risk = "magas"
    elif r_items:
        risk = "közepes"

    # Summary
    try:
        summary_prompt = f"Készíts egy 3-5 mondatos magyar nyelvű vezetői összefoglalót ehhez a lakásvásárlási tanácsadóhoz: {response_text[:1000]}"
        summary_resp = _generate_with_retry(client, model_id, summary_prompt, gen_config)
        summary_hu = summary_resp.text.strip()
    except (RetryError, Exception) as e:
        logger.error("Gemini summary call failed: %s", e)
        summary_hu = (
            "A jelentés generálása közben hiba lépett fel. "
            "Kérjük ellenőrizze, hogy a GOOGLE_API_KEY érvényes, "
            "és próbálja újra később."
        )

    return AdvisoryReport(
        apartment_profile=profile,
        generated_at=datetime.now(),
        questions_for_seller=q_items,
        inspection_checklist=i_items,
        red_flags=r_items,
        cost_estimate=cost_est,
        similar_cases=similar,
        rag_sources_used=rag_sources,
        overall_risk=risk,
        summary_hu=summary_hu
    )
