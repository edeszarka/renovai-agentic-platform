"""Dedicated, single-purpose LLM client for the category-taxonomy task.

This module is intentionally **separate** from ``renovai/rag/gemini_client.py``
and ``renovai/rag/embedder.py`` (out of scope; their retry logic is not reused
or copied).  It exists only to make text-clustering calls for
``scripts/build_category_taxonomy.py``.

Provider strategy (per task requirements):

1. **DeepSeek is primary** (``DEEPSEEK_API_KEY``).
2. **Gemini is an optional secondary** (``GOOGLE_API_KEY``), used only when
   DeepSeek is not configured, or as a one-shot failover on a quota error.

Error policy — deliberately simple:

* Retry a provider **at most once** on a genuinely transient error (timeout,
  connection reset, HTTP 5xx) with a short fixed/exponential backoff.
* On a rate-limit / quota error (HTTP 429, ``RESOURCE_EXHAUSTED``,
  ``insufficient_quota``) do **not** retry that provider at all — fail over to
  the next configured provider once, or raise a clear, actionable error naming
  the provider and the reason.
* No long list of model names is cycled through.
* Every served call logs the provider and model that actually answered.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

logger = logging.getLogger("renovai.taxonomy_llm")


DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"

#: One retry after the first failure, for transient errors only.
DEFAULT_MAX_RETRIES = 1
DEFAULT_BACKOFF_SECONDS = 2.0

_QUOTA_MARKERS = (
    "429",
    "resource_exhausted",
    "resource exhausted",
    "insufficient_quota",
    "insufficient quota",
    "quota",
    "rate limit",
    "rate_limit",
    "too many requests",
)
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "deadline exceeded",
    "connection",
    "temporarily unavailable",
    "service unavailable",
    "server error",
    "500",
    "502",
    "503",
    "504",
)


class LLMError(RuntimeError):
    """Raised when no configured provider could serve the call."""


class LLMQuotaError(LLMError):
    """Raised when a provider reports rate-limit / quota exhaustion."""


class LLMTransientError(LLMError):
    """Raised after transient errors exhaust their (single) retry."""


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    model: str


@dataclass(frozen=True)
class LLMResult:
    """The text returned plus provenance (which provider/model served it)."""

    text: str
    provider: str
    model: str


@dataclass(frozen=True)
class Provider:
    """A named provider callable: ``(prompt, response_json) -> ProviderResponse``."""

    name: str
    call: Callable[[str, bool], ProviderResponse]


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def classify_error(exc: BaseException) -> str:
    """Return ``"quota"``, ``"transient"`` or ``"permanent"`` for an exception.

    Checks the exception's ``status_code`` (OpenAI-compatible SDKs set this)
    first, then falls back to message markers.  This works for both the real
    SDK exceptions and lightweight test doubles that expose ``status_code``.
    """
    status = getattr(exc, "status_code", None)
    if status == 429:
        return "quota"
    if isinstance(status, int) and status >= 500:
        return "transient"

    message = str(exc).lower()
    if any(marker in message for marker in _QUOTA_MARKERS):
        return "quota"
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "transient"
    if any(marker in message for marker in _TRANSIENT_MARKERS):
        return "transient"
    return "permanent"


# ---------------------------------------------------------------------------
# Provider callables
# ---------------------------------------------------------------------------

def _deepseek_call(prompt: str, response_json: bool) -> ProviderResponse:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise LLMError("DEEPSEEK_API_KEY is not set")
    from openai import OpenAI

    base_url = os.getenv("DEEPSEEK_BASE_URL", DEEPSEEK_DEFAULT_BASE_URL)
    model = os.getenv("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_MODEL)
    client = OpenAI(api_key=api_key, base_url=base_url)
    kwargs = {}
    if response_json:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=8192,
        **kwargs,
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise LLMError(f"DeepSeek ({model}) returned empty content")
    return ProviderResponse(text=text, model=model)


def _gemini_call(prompt: str, response_json: bool) -> ProviderResponse:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise LLMError("GOOGLE_API_KEY is not set")
    from google import genai
    from google.genai import types

    model = os.getenv("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=8192,
        response_mime_type="application/json" if response_json else "text/plain",
    )
    response = client.models.generate_content(model=model, contents=prompt, config=config)
    text = (response.text or "").strip()
    if not text:
        raise LLMError(f"Gemini ({model}) returned empty content")
    return ProviderResponse(text=text, model=model)


def resolve_providers() -> List[Provider]:
    """Return configured providers in priority order (DeepSeek, then Gemini)."""
    providers: List[Provider] = []
    if os.getenv("DEEPSEEK_API_KEY"):
        providers.append(Provider("deepseek", _deepseek_call))
    if os.getenv("GOOGLE_API_KEY"):
        providers.append(Provider("gemini", _gemini_call))
    return providers


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def call_llm(
    prompt: str,
    *,
    response_json: bool = False,
    providers: Optional[Sequence[Provider]] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> LLMResult:
    """Call the first provider that succeeds, with the error policy above.

    ``providers`` and ``sleep`` are injectable for tests.  Raises
    :class:`LLMError` (or a subclass) if every provider fails.
    """
    resolved = list(providers) if providers is not None else resolve_providers()
    if not resolved:
        raise LLMError(
            "No LLM provider configured. Set DEEPSEEK_API_KEY (preferred) or "
            "GOOGLE_API_KEY in the environment/.env."
        )

    failures: List[str] = []
    for provider in resolved:
        for attempt in range(max_retries + 1):
            try:
                response = provider.call(prompt, response_json)
                logger.info(
                    "LLM call served by provider=%s model=%s", provider.name, response.model
                )
                return LLMResult(text=response.text, provider=provider.name, model=response.model)
            except Exception as exc:  # noqa: BLE001 - policy decides what to do
                kind = classify_error(exc)
                failures.append(f"{provider.name} ({kind}): {exc}")
                if kind == "quota":
                    logger.warning(
                        "Provider %s hit a quota/rate-limit error; not retrying it: %s",
                        provider.name,
                        exc,
                    )
                    break  # fail over to next provider (or raise below)
                if kind == "transient" and attempt < max_retries:
                    delay = backoff_seconds * (2 ** attempt)
                    logger.warning(
                        "Provider %s transient error; retry %d/%d in %.1fs: %s",
                        provider.name,
                        attempt + 1,
                        max_retries,
                        delay,
                        exc,
                    )
                    sleep(delay)
                    continue
                logger.warning("Provider %s failed (%s); not retrying", provider.name, kind)
                break  # exhausted retries / permanent -> next provider

    message = "All LLM providers failed: " + " | ".join(failures)
    raise LLMError(message)
