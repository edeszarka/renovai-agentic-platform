"""
Gateway Agent — Intent Classification & Parameter Extraction for RenovAI 2.0.

The Gateway is the sole entry point for all user queries. Its responsibilities:
  1. Classify the user's Hungarian question into one or more intents
  2. Extract structured parameters from the question
  3. Assign a confidence score to the routing decision
  4. If confidence < 0.7, return a clarification request instead of routing

All routing decisions are logged with a trace_id for observability.
"""

import os
import re
import json
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class Intent(str):
    """Canonical intent names."""
    COST_ESTIMATION = "cost_estimation"
    MARKET_QUERY = "market_query"
    DUE_DILIGENCE = "due_diligence"
    INGESTION = "ingestion"
    EXPERT_INTERVIEW = "expert_interview"
    CONSTRUCTION_PLANNING = "construction_planning"
    COMBINED = "combined"
    UNKNOWN = "unknown"


@dataclass
class RoutingDecision:
    """
    Structured output from the Gateway's classification step.

    Serialises to JSON for structured LLM output mode.
    """
    primary_intent: str = Intent.UNKNOWN
    secondary_intents: list[str] = field(default_factory=list)
    confidence: float = 0.0
    parameters: dict[str, Any] = field(default_factory=dict)
    clarification_needed: bool = False
    clarification_question: str = ""
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_intent": self.primary_intent,
            "secondary_intents": self.secondary_intents,
            "confidence": self.confidence,
            "parameters": self.parameters,
            "clarification_needed": self.clarification_needed,
            "clarification_question": self.clarification_question,
            "trace_id": self.trace_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoutingDecision":
        return cls(
            primary_intent=data.get("primary_intent", Intent.UNKNOWN),
            secondary_intents=data.get("secondary_intents", []),
            confidence=data.get("confidence", 0.0),
            parameters=data.get("parameters", {}),
            clarification_needed=data.get("clarification_needed", False),
            clarification_question=data.get("clarification_question", ""),
            trace_id=data.get("trace_id", ""),
        )


# ---------------------------------------------------------------------------
# Keyword-based routing (fast path, no LLM call)
# ---------------------------------------------------------------------------

KEYWORD_INTENT_MAP: list[tuple[str, str, list[str]]] = [
    # (intent, sub_key, keywords)
    (Intent.COST_ESTIMATION, "cost",
     ["mennyibe kerül", "mennyit költsek", "költség", "költségek",
      "ár", "ára", "forint", "nm ára", "kerül", "mennyi"]),
    (Intent.MARKET_QUERY, "market",
     ["átlag", "átlagosan", "statisztika", "tendencia",
      "összehasonlítás", "melyik kerület", "hány idézet"]),
    (Intent.DUE_DILIGENCE, "due_diligence",
     ["mire figyeljek", "ellenőrzés", "kockázat", "piros zászló",
      "átvilágítás", "kérdés az eladóhoz", "red flag",
      "mit nézzek", "mire érdemes figyelni"]),
    (Intent.INGESTION, "ingestion",
     ["feltöltök", "árajánlat", "xlsx", "excel", "beszúrok",
      "import", "parse"]),
    (Intent.EXPERT_INTERVIEW, "expert_interview",
     ["mire figyeljek", "épületfizikai", "vörös zászló", "red flag",
      "kockázat", "átvilágítás", "salak", "kohósalak",
      "teherhordó fal", "építési korszak", "alapozás",
      "mit nézzek meg vásárlás előtt", "milyen állapotban van",
      "boltíves", "födém", "betontálcás", "vevői felkészítő",
      "szakvélemény", "mit nézzek", "tégla boltíves"]),
    (Intent.CONSTRUCTION_PLANNING, "construction_planning",
     ["sorrend", "ütemezés", "előbb csinálni", "lépés",
      "milyen sorrendben", "építési sorrend", "technológiai sorrend",
      "bontás után", "kőműves", "burkolás előtt",
      "teljes felújítás terv", "lépésről lépésre",
      "részleges felújítás", "felújítási tervező",
      "tervezés", "ütemterv", "fázis"]),
]


def _keyword_classify(question_hu: str) -> tuple[str, list[str], float]:
    """
    Fast keyword-based intent classification.

    Returns (primary_intent, secondary_intents, confidence).
    """
    q_lower = question_hu.lower()
    matched_intents: dict[str, int] = {}

    for intent, sub_key, keywords in KEYWORD_INTENT_MAP:
        for kw in keywords:
            if kw in q_lower:
                matched_intents[intent] = matched_intents.get(intent, 0) + 1
                break  # One match per sub_key per intent

    if not matched_intents:
        return Intent.UNKNOWN, [], 0.0

    # Sort by match count descending
    sorted_intents = sorted(matched_intents.items(), key=lambda x: -x[1])
    primary = sorted_intents[0][0]
    secondary = [i for i, _ in sorted_intents[1:]]

    # Confidence proportional to how many distinct groups matched
    total_keyword_groups = len(KEYWORD_INTENT_MAP)
    confidence = min(len(matched_intents) / total_keyword_groups + 0.3, 0.95)

    # If multiple intents matched strongly, it's combined
    if len(matched_intents) >= 2:
        primary = Intent.COMBINED

    return primary, secondary, confidence


# ---------------------------------------------------------------------------
# LLM-based routing (accurate path, uses Gemini structured output)
# ---------------------------------------------------------------------------

CLASSIFICATION_SYSTEM_PROMPT = """You are the routing agent for RenovAI, a Hungarian renovation cost estimation and due-diligence system.

Classify the user's Hungarian question into exactly one of these intents:

- cost_estimation: User asks about renovation costs, prices, budgets, or HUF amounts. Includes specific work items like electrical, plumbing, tiling, demolition.
- market_query: User asks about market statistics, averages, trends, or comparisons across the quote corpus.
- due_diligence: User asks about pre-purchase inspection, red flags, questions for the seller, building-specific risks.
- ingestion: User uploads or references an XLSX/Excel file containing a contractor quote for parsing.
- expert_interview: User asks about building-physics risks, structural condition, historical-era-specific problems (kohósalak, alumínium vezeték), építési korszak, or what to inspect before buying. This is Tab 1: Vevői Felkészítő / Buyer Preparation.
- construction_planning: User asks about the step-by-step renovation sequence (sorrend, ütemezés), construction order, technical feasibility, teljes/részleges felújítás terv. This is Tab 2: Felújítási Tervező / Renovation Planner.
- combined: The question clearly spans multiple intents (e.g., both cost AND due-diligence, or interview AND planning).
- unknown: None of the above match with confidence.

Extract any parameters you can identify from the question:
- district (kerület): integer 1-23
- area_sqm (nm): number
- num_rooms: integer
- building_type: "panel", "tégla", "újépítés", or null
- building_era: string or null (e.g. "1920 előtt", "1920-1965", "1965-1990", "1990 után")
- floor_construction: "acél gerendás", "betontálcás", or null
- wall_condition: object with "wallpaper": bool if fűrészporos tapéta mentioned
- scope_flags: object with boolean flags for plumbing, electrical, flooring, demolition, slag, built_in_shower
- renovation_scope: "full" or "partial" if mentioned

Respond with JSON only. No markdown, no explanation. All parameters in Hungarian terms."""


def _build_classification_prompt(question_hu: str) -> str:
    return f"User question:\n{question_hu}\n\nRespond with JSON."


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------

class Gateway:
    """
    Gateway Agent — entry point for all user queries.

    Usage:
        gateway = Gateway()
        decision = await gateway.classify("mennyibe kerül egy 55 nm-es lakás felújítása?")

        if decision.clarification_needed:
            return decision.clarification_question
        else:
            handler = resolve_handler(decision.primary_intent)
            result = await handler(decision.parameters, ...)
    """

    def __init__(self):
        self._trace_id_prefix = "gw"

    async def classify(self, question_hu: str) -> RoutingDecision:
        """
        Classify the user's question and extract parameters.

        Uses a two-tier approach:
          1. Fast keyword matching (no LLM cost)
          2. If keyword confidence < 0.7, uses Gemini structured JSON output
        """
        trace_id = f"{self._trace_id_prefix}-{uuid.uuid4().hex[:12]}"

        # Tier 1: keyword matching
        primary_intent, secondary_intents, kw_confidence = _keyword_classify(question_hu)

        if kw_confidence >= 0.7:
            return RoutingDecision(
                primary_intent=primary_intent,
                secondary_intents=secondary_intents,
                confidence=kw_confidence,
                parameters=self._extract_params_keyword(question_hu),
                trace_id=trace_id,
            )

        # Tier 2: LLM classification for low-confidence or complex queries
        return await self._llm_classify(question_hu, trace_id)

    async def _llm_classify(
        self,
        question_hu: str,
        trace_id: str,
    ) -> RoutingDecision:
        """Use Gemini structured output for accurate classification."""
        try:
            from google import genai
        except ImportError:
            # Fallback to keyword-only result even if low confidence
            primary, secondary, confidence = _keyword_classify(question_hu)
            if confidence == 0.0:
                return RoutingDecision(
                    primary_intent=Intent.UNKNOWN,
                    confidence=0.0,
                    clarification_needed=True,
                    clarification_question=(
                        "Nem teljesen értem a kérdést. Kérlek pontosítsd: "
                        "(1) felújítási költségbecslés, (2) elővásárlási "
                         "tanácsadás, (3) piaci statisztikák, "
                         "(4) árajánlat feltöltése, (5) épületfizikai "
                         "szakvélemény, vagy (6) felújítási ütemterv?"
                    ),
                    trace_id=trace_id,
                )
            return RoutingDecision(
                primary_intent=primary,
                secondary_intents=secondary,
                confidence=confidence,
                parameters=self._extract_params_keyword(question_hu),
                trace_id=trace_id,
            )

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            # No API key; use keyword fallback
            primary, secondary, confidence = _keyword_classify(question_hu)
            return RoutingDecision(
                primary_intent=primary or Intent.UNKNOWN,
                secondary_intents=secondary,
                confidence=confidence or 0.3,
                parameters=self._extract_params_keyword(question_hu),
                trace_id=trace_id,
            )

        client = genai.Client(api_key=api_key)

        try:
            response = client.models.generate_content(
                model=os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash"),
                contents=_build_classification_prompt(question_hu),
                config={
                    "system_instruction": CLASSIFICATION_SYSTEM_PROMPT,
                    "response_mime_type": "application/json",
                    "temperature": 0.1,
                },
            )

            raw = json.loads(response.text)
            decision = RoutingDecision.from_dict(raw)
            decision.trace_id = trace_id

            if decision.confidence < 0.7 and not decision.clarification_needed:
                decision.clarification_needed = True
                decision.clarification_question = (
                    "Nem teljesen biztos a kérdés típusában. Kérlek pontosítsd: "
                    "költségbecslést, elővásárlási tanácsadást, piaci adatokat, "
                         "árajánlat feltöltést, épületfizikai szakvéleményt, "
                         "vagy felújítási ütemtervet szeretnél?"
                )

            # Merge keyword-extracted params as fallback
            kw_params = self._extract_params_keyword(question_hu)
            for k, v in kw_params.items():
                decision.parameters.setdefault(k, v)

            logger.info(
                "[%s] LLM classify: intent=%s confidence=%.2f",
                trace_id, decision.primary_intent, decision.confidence,
            )
            return decision

        except Exception as exc:
            logger.warning("[%s] LLM classification failed: %s", trace_id, exc)
            # Fallback to keyword
            primary, secondary, confidence = _keyword_classify(question_hu)
            return RoutingDecision(
                primary_intent=primary or Intent.UNKNOWN,
                secondary_intents=secondary,
                confidence=confidence or 0.3,
                parameters=self._extract_params_keyword(question_hu),
                trace_id=trace_id,
            )

    def _extract_params_keyword(self, question_hu: str) -> dict[str, Any]:
        """Extract basic parameters using regex and keyword heuristics."""
        params: dict[str, Any] = {}

        # District
        district_match = re.search(r'(\d+)\.\s*kerület', question_hu)
        if district_match:
            try:
                params["district"] = int(district_match.group(1))
            except ValueError:
                pass

        # Area
        area_match = re.search(r'(\d+)\s*nm', question_hu)
        if area_match:
            try:
                params["area_sqm"] = float(area_match.group(1))
            except ValueError:
                pass

        # Rooms
        rooms_match = re.search(r'(\d+)\s*szob', question_hu)
        if rooms_match:
            try:
                params["num_rooms"] = int(rooms_match.group(1))
            except ValueError:
                pass

        # Building type
        if "panel" in question_hu.lower():
            params["building_type"] = "panel"
        elif "tégla" in question_hu.lower():
            params["building_type"] = "tégla"
        elif "újépítés" in question_hu.lower():
            params["building_type"] = "újépítés"

        # Building era detection
        era_match = re.search(r'(19\d\d|20\d\d)', question_hu)
        if era_match:
            params["building_era"] = era_match.group(1)

        # Condition keywords
        if any(kw in question_hu.lower() for kw in ["fűrészporos tapéta", "tapéta", "rossz állapot"]):
            params.setdefault("wall_condition", {})["wallpaper"] = True
        if any(kw in question_hu.lower() for kw in ["épített zuhany", "beépített zuhany"]):
            params.setdefault("scope_flags", {})["built_in_shower"] = True
        if any(kw in question_hu.lower() for kw in ["salak", "kohósalak"]):
            params.setdefault("scope_flags", {})["slag"] = True

        # Floor construction
        if any(kw in question_hu.lower() for kw in ["acél gerendás", "boltíves", "födém"]):
            params["floor_construction"] = "acél gerendás"

        # Sequencing keywords
        if any(kw in question_hu.lower() for kw in ["sorrend", "ütemezés", "lépés", "hány lépés"]):
            params["want_sequence"] = True

        # Scope flags
        scope_flags = params.get("scope_flags", {})
        if any(kw in question_hu.lower() for kw in ["villany", "elektromos"]):
            scope_flags["electrical"] = True
        if any(kw in question_hu.lower() for kw in ["víz", "vízvezeték", "plumbing"]):
            scope_flags["plumbing"] = True
        if any(kw in question_hu.lower() for kw in ["burkol", "járólap", "csempe"]):
            scope_flags["flooring"] = True
        if any(kw in question_hu.lower() for kw in ["bontás", "bont"]):
            scope_flags["demolition"] = True
        if any(kw in question_hu.lower() for kw in ["salak", "kohósalak"]):
            scope_flags["slag"] = True
        if scope_flags:
            params["scope_flags"] = scope_flags

        return params
