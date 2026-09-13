"""Reusable three-tier line-item category classifier (Phase 2).

Resolves a raw Hungarian ``name_hu`` line-item string to one canonical category
key (see ``docs/category_taxonomy.md``).  Resolution order:

1. **Exact lookup** against the Phase-1 machine-readable mapping
   (``data/reports/category_taxonomy_clusters.json``).  No API call.
2. **Groq** (primary LLM) for strings not in the lookup.
3. **DeepSeek** (secondary LLM) if Groq fails for a non-trivial reason.

Error policy (shared with ``scripts/taxonomy_llm_client.py``): retry at most
once per provider on genuinely transient errors (timeouts / 5xx); on a
rate-limit or quota error (HTTP 429, ``RESOURCE_EXHAUSTED``,
``insufficient_quota``) do **not** retry that provider — move to the next tier
immediately.  Every non-lookup classification records which tier/provider/model
served it.

The LLM is used for **text classification only**; it is never asked to compute,
sum or estimate a price.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from scripts.taxonomy_llm_client import classify_error

logger = logging.getLogger("renovai.line_item_classifier")

PHASE1_JSON_DEFAULT = Path("data/reports/category_taxonomy_clusters.json")

GROQ_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
# Verified against the live Groq /models endpoint at implementation time
# (llama-3.3-70b-versatile is no longer offered).  qwen/qwen3.8-27b supports
# response_format=json_object and is strong on Hungarian.
GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"

DEFAULT_MAX_RETRIES = 1
DEFAULT_BACKOFF_SECONDS = 2.0

REVIEW = "needs_human_review"


#: Canonical categories (ASCII keys) and their bilingual labels.
CANONICAL_LABELS: Dict[str, str] = {
    "bontas": "Bontás / Demolition",
    "viz_futes": "Víz és fűtés / Plumbing & heating",
    "villany": "Villanyszerelés / Electrical",
    "klima": "Klíma / Air conditioning",
    "burkolas": "Burkolás / Tiling & surface covering",
    "parketta": "Parketta / Parquet flooring",
    "nyilaszaro": "Nyílászáró csere / Windows & doors",
    "szigeteles": "Szigetelés / Insulation",
    "vakolas": "Vakolás / Plastering",
    "gletteles_festes": "Glettelés és festés / Painting & finishing",
    "egyeb_komuves": "Egyéb kőműves / Misc masonry",
    "szallitas": "Szállítás/segédmunka / Logistics & assistance",
    "konyha": "Konyhabútor / Kitchen cabinetry",
    "furdo": "Fürdőszoba / Bathroom",
    "futes_rendszer": "Fűtésrendszer / Heating system",
    "gipszkarton": "Gipszkarton/álmennyezet / Drywall & suspended ceilings",
    "egyeb": "Egyéb / Other",
}
CANONICAL_KEYS = tuple(CANONICAL_LABELS)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def fold(text: str) -> str:
    """Lower-case and strip Hungarian accents (ő→o, ű→u, é→e, ...)."""
    text = (text or "").lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def ascii_key(key: Optional[str]) -> Optional[str]:
    """Normalise any category key to ASCII snake_case (``szigetelés``→``szigeteles``)."""
    if not key:
        return None
    folded = re.sub(r"[^a-z0-9]+", "_", fold(key)).strip("_")
    return folded or None


def normalize_name(name: Optional[str]) -> str:
    """Normalise a raw ``name_hu`` for lookup (accent/case/whitespace insensitive)."""
    return re.sub(r"\s+", " ", fold(name)).strip()


# ---------------------------------------------------------------------------
# Tier 1 — lookup
# ---------------------------------------------------------------------------

def load_lookup(path: str | Path = PHASE1_JSON_DEFAULT) -> Dict[str, str]:
    """Build a ``{normalised name_hu: canonical key}`` lookup from Phase 1 output."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    lookup: Dict[str, str] = {}
    for category, members in (data.get("members") or {}).items():
        canon = ascii_key(category)
        for member in members:
            name = member.get("name_hu")
            if name:
                lookup[normalize_name(name)] = canon
    return lookup


# ---------------------------------------------------------------------------
# LLM providers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMProvider:
    """A named provider with a JSON-returning callable."""

    name: str
    model: str
    call: Callable[[str], str]


class ProviderError(RuntimeError):
    """Raised when a provider cannot serve a request under the error policy."""

    def __init__(self, provider: str, kind: str, cause: BaseException):
        super().__init__(f"provider {provider} failed ({kind}): {cause}")
        self.provider = provider
        self.kind = kind
        self.cause = cause


def call_provider(
    provider: LLMProvider,
    prompt: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Call a provider, retrying transient errors once and never retrying quota."""
    last_error: Optional[BaseException] = None
    for attempt in range(max_retries + 1):
        try:
            return provider.call(prompt)
        except Exception as exc:  # noqa: BLE001 - policy below decides
            last_error = exc
            kind = classify_error(exc)
            if kind == "quota":
                logger.warning("Provider %s hit quota/rate limit; not retrying", provider.name)
                raise ProviderError(provider.name, kind, exc) from exc
            if kind == "transient" and attempt < max_retries:
                delay = backoff_seconds * (2 ** attempt)
                logger.warning(
                    "Provider %s transient error; retry %d/%d in %.1fs",
                    provider.name, attempt + 1, max_retries, delay,
                )
                sleep(delay)
                continue
            raise ProviderError(provider.name, kind, exc) from exc
    raise ProviderError(provider.name, "unknown", last_error or RuntimeError("no attempt"))


def _openai_json_call(
    base_url: str, api_key_env: str, model: str, max_tokens: int
) -> Callable[[str], str]:
    def _call(prompt: str) -> str:
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise RuntimeError(f"{api_key_env} is not set")
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    return _call


# Groq's on-demand free tier enforces a 1000 output-tokens-per-minute cap, so
# keep its max_tokens below that; DeepSeek has more headroom.  Requests that
# still exceed the per-minute budget are 429s and correctly fail over.
GROQ_MAX_TOKENS = 800
DEEPSEEK_MAX_TOKENS = 4096


def make_groq_provider(model: Optional[str] = None) -> LLMProvider:
    resolved = model or os.getenv("GROQ_MODEL", GROQ_DEFAULT_MODEL)
    base_url = os.getenv("GROQ_BASE_URL", GROQ_DEFAULT_BASE_URL)
    return LLMProvider(
        "groq", resolved,
        _openai_json_call(base_url, "GROQ_API_KEY", resolved, GROQ_MAX_TOKENS),
    )


def make_deepseek_provider(model: Optional[str] = None) -> LLMProvider:
    resolved = model or os.getenv("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_MODEL)
    base_url = os.getenv("DEEPSEEK_BASE_URL", DEEPSEEK_DEFAULT_BASE_URL)
    return LLMProvider(
        "deepseek", resolved,
        _openai_json_call(base_url, "DEEPSEEK_API_KEY", resolved, DEEPSEEK_MAX_TOKENS),
    )


def default_providers() -> List[LLMProvider]:
    """Groq primary, DeepSeek secondary, in that order (only if configured)."""
    providers: List[LLMProvider] = []
    if os.getenv("GROQ_API_KEY"):
        providers.append(make_groq_provider())
    if os.getenv("DEEPSEEK_API_KEY"):
        providers.append(make_deepseek_provider())
    return providers


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def loads_json(text: str) -> object:
    """Parse JSON, tolerating markdown fences / surrounding prose."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def parse_category(text: str) -> Optional[str]:
    """Extract a valid canonical ``category_key`` from an LLM JSON response."""
    try:
        data = loads_json(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    key = ascii_key(data.get("category_key"))
    return key if key in CANONICAL_KEYS else None


# ---------------------------------------------------------------------------
# Tiered classification
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    category_key: Optional[str]
    tier: str                      # "lookup" | "groq" | "deepseek" | "unresolved"
    provider: Optional[str] = None
    model: Optional[str] = None


def build_classification_prompt(name_hu: str, notes_hu: Optional[str] = None) -> str:
    taxonomy = "\n".join(f"- {k} | {v}" for k, v in CANONICAL_LABELS.items())
    context = f"\nnotes_hu: {notes_hu}" if notes_hu else ""
    return (
        "Classify the Hungarian renovation line item below into exactly one "
        "category_key from the CANONICAL CATEGORIES list. This is a text "
        "classification task; do not compute or mention any price.\n\n"
        f"CANONICAL CATEGORIES:\n{taxonomy}\n\n"
        'Return JSON only: {"category_key": "<key>"}\n\n'
        f"ITEM:\nname_hu: {name_hu}{context}"
    )


def classify_line_item(
    name_hu: str,
    *,
    lookup: Optional[Dict[str, str]] = None,
    providers: Optional[Sequence[LLMProvider]] = None,
    notes_hu: Optional[str] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> ClassificationResult:
    """Resolve one line item via lookup → Groq → DeepSeek."""
    lookup = lookup or {}
    key = lookup.get(normalize_name(name_hu))
    if key:
        return ClassificationResult(category_key=key, tier="lookup")

    resolved = list(providers) if providers is not None else default_providers()
    prompt = build_classification_prompt(name_hu, notes_hu)
    for provider in resolved:
        try:
            text = call_provider(provider, prompt, max_retries=max_retries, sleep=sleep)
        except ProviderError as exc:
            logger.warning("Tier %s failed for %r: %s", provider.name, name_hu, exc)
            continue
        category = parse_category(text)
        if category:
            return ClassificationResult(
                category_key=category, tier=provider.name,
                provider=provider.name, model=provider.model,
            )
        logger.warning("Tier %s returned an invalid category for %r", provider.name, name_hu)
    return ClassificationResult(category_key=None, tier="unresolved")


# ---------------------------------------------------------------------------
# Task B — "szigeteles" re-examination rule
# ---------------------------------------------------------------------------

def _is_chimney_lining(text: str) -> bool:
    return "kemeny" in text and "belel" in text


def _is_waterproofing(text: str) -> bool:
    # covers vízszigetelés / víszigetelése (typo) / vízsszigetelése (typo)
    return "szigetel" in text and ("viz" in text or "visz" in text)


def _is_bathroom_context(text: str) -> bool:
    return any(k in text for k in ("furd", "wc", "zuhany", "kad", "mosdo"))


def mentions_chimney_lining(name_hu: str, notes_hu: Optional[str] = None) -> bool:
    """True if the raw text clearly refers to chimney lining (kéménybélelés)."""
    return _is_chimney_lining(fold(f"{name_hu} {notes_hu or ''}"))


def enforce_szigeteles_rule(
    name_hu: str,
    notes_hu: Optional[str],
    llm_category: Optional[str],
) -> str:
    """Apply the repo-owner's explicit szigeteles rule deterministically.

    Returns a canonical key or :data:`REVIEW`.  The deterministic guards take
    precedence over the LLM's suggestion for the two unambiguous cases (chimney
    lining, waterproofing); otherwise the LLM's valid canonical key is used.
    """
    text = fold(f"{name_hu} {notes_hu or ''}")
    if _is_chimney_lining(text):
        return "futes_rendszer"
    if _is_waterproofing(text):
        return "furdo" if _is_bathroom_context(text) else REVIEW
    category = ascii_key(llm_category)
    return category if category in CANONICAL_KEYS else REVIEW


@dataclass
class SzigetelesItem:
    item_id: str
    name_hu: str
    notes_hu: Optional[str] = None


@dataclass
class SzigetelesReexamResult:
    item_id: str
    name_hu: str
    previous_category: str
    final_category: Optional[str]     # None == needs_human_review
    llm_category: Optional[str]
    reason: str
    tier: str                         # "groq" | "deepseek" | "rule" | "unresolved"
    provider: Optional[str] = None
    model: Optional[str] = None

    @property
    def needs_human_review(self) -> bool:
        return self.final_category is None


def _build_reexam_prompt(items: Sequence[SzigetelesItem]) -> str:
    taxonomy = ", ".join(CANONICAL_KEYS)
    # Use small batch-local integer ids (not UUIDs) so the model can echo them
    # back reliably.
    payload = [
        {"item_id": idx, "name_hu": it.name_hu, "notes_hu": it.notes_hu or ""}
        for idx, it in enumerate(items)
    ]
    return (
        "You are re-examining Hungarian renovation line items that were "
        "provisionally classified as 'szigeteles' (insulation). Apply these "
        "rules EXACTLY; do not compute or mention any price.\n"
        "1. Chimney lining ('kéménybélelés' or clear synonyms) -> 'futes_rendszer'.\n"
        "2. Waterproofing work ('vízszigetelés' or equivalent), regardless of "
        "location: if the text/context indicates a bathroom or wet room -> "
        "'furdo'; otherwise (ambiguous or clearly non-bathroom, e.g. balcony / "
        "terrace / roof) -> 'needs_human_review'.\n"
        "3. Keep 'szigeteles' ONLY for unambiguous INTERIOR thermal or acoustic "
        "insulation (e.g. wall/ceiling sound insulation between rooms or "
        "apartments, named materials such as multipor, ásványgyapot, EPS). NOT "
        "exterior building-envelope insulation: individual apartment quotes "
        "essentially never include facade/exterior insulation, so be skeptical "
        "of generic 'hőszigetelés' without a clear interior context and return "
        "'needs_human_review' for those.\n"
        "4. If an item clearly belongs to another canonical category, return "
        "that key.\n\n"
        f"Allowed outputs: one of [{taxonomy}] or 'needs_human_review'.\n\n"
        "Return JSON only: "
        '{"assignments": [{"item_id": <int>, "category_key": "<...>", '
        '"reason": "<short>"}]}\n\n'
        "ITEMS:\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _parse_reexam_response(text: str, n_items: int) -> Dict[int, tuple]:
    """Return ``{batch_local_id: (llm_category, reason)}`` from an LLM response."""
    data = loads_json(text)
    assignments = data.get("assignments") if isinstance(data, dict) else data
    result: Dict[int, tuple] = {}
    if not isinstance(assignments, list):
        return result
    for entry in assignments:
        if not isinstance(entry, dict):
            continue
        try:
            item_id = int(entry.get("item_id"))
        except (TypeError, ValueError):
            continue
        if not 0 <= item_id < n_items:
            continue
        raw = entry.get("category_key")
        category = ascii_key(raw) if raw else None
        if category not in CANONICAL_KEYS and category != REVIEW:
            category = None
        result[item_id] = (category, str(entry.get("reason", "") or ""))
    return result


def reexamine_szigeteles_items(
    items: Sequence[SzigetelesItem],
    *,
    providers: Optional[Sequence[LLMProvider]] = None,
    previous_category: str = "szigeteles",
    batch_size: int = 15,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> List[SzigetelesReexamResult]:
    """Re-examine szigeteles items via Groq → DeepSeek, then enforce the rule."""
    resolved = list(providers) if providers is not None else default_providers()
    results: List[SzigetelesReexamResult] = []
    items = list(items)
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        prompt = _build_reexam_prompt(batch)
        decisions: Dict[int, tuple] = {}
        tier, provider_name, model_name = "rule", None, None
        for provider in resolved:
            try:
                text = call_provider(provider, prompt, max_retries=max_retries, sleep=sleep)
            except ProviderError as exc:
                logger.warning("Re-exam tier %s failed: %s", provider.name, exc)
                continue
            try:
                decisions = _parse_reexam_response(text, len(batch))
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("Re-exam tier %s returned invalid JSON: %s", provider.name, exc)
                decisions = {}
            if decisions:
                tier, provider_name, model_name = provider.name, provider.name, provider.model
                break
        for local_id, item in enumerate(batch):
            llm_category, reason = decisions.get(local_id, (None, ""))
            final = enforce_szigeteles_rule(item.name_hu, item.notes_hu, llm_category)
            results.append(
                SzigetelesReexamResult(
                    item_id=item.item_id,
                    name_hu=item.name_hu,
                    previous_category=previous_category,
                    final_category=None if final == REVIEW else final,
                    llm_category=llm_category,
                    reason=reason,
                    tier=tier,
                    provider=provider_name,
                    model=model_name,
                )
            )
    return results
