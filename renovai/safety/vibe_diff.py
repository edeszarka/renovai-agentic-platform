import os
import json
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VibeDiff:
    """Plain-English summary of why an agent reached its conclusion.

    Stored separately from the main report (context hygiene) and bound
    to the originating trace_id.
    """

    vibe_id: str
    trace_id: str
    source: str                 # "due_diligence" | "cost_estimator"
    explanation_hu: str         # Hungarian explanation of reasoning
    explanation_en: str         # English fallback for internal review
    key_drivers: list[str]      # Bullet-point list of what drove the delta
    before_snapshot: dict[str, Any]
    after_snapshot: dict[str, Any]
    model: str = "gemini-2.0-flash-lite"

    def to_dict(self) -> dict[str, Any]:
        return {
            "vibe_id": self.vibe_id,
            "trace_id": self.trace_id,
            "source": self.source,
            "explanation_hu": self.explanation_hu,
            "explanation_en": self.explanation_en,
            "key_drivers": self.key_drivers,
            "before_snapshot": self.before_snapshot,
            "after_snapshot": self.after_snapshot,
            "model": self.model,
        }


VIBE_DIFF_SYSTEM_PROMPT = """You are a senior renovation advisor explaining an agent's reasoning to a human reviewer.

You will receive:
- "before": the raw input parameters (user question + extracted params)
- "after": the agent's proposed output (estimate numbers, recommendations, flags)

Your task is to produce a plain-English "Vibe Diff" that explains WHY the agent
arrived at this conclusion. Focus on the causal chain — e.g. "the price increased
by 20% because wall conditions triggered the 'sawdust wallpaper surcharge' rule."

Write in Hungarian (for the end customer) and English (for the internal human-in-the-loop reviewer).

Respond with JSON only:
{
  "explanation_hu": "string",
  "explanation_en": "string",
  "key_drivers": ["driver 1", "driver 2", ...]
}

Temperature: 0.15 — stick to the facts, no embellishment."""


class VibeDiffEngine:
    """Intercepts agent outputs and generates a Vibe Diff for human review.

    Usage:
        engine = VibeDiffEngine()
        diff = await engine.generate(
            trace_id="gw-abc123",
            source="cost_estimator",
            before={"district": 7, "area_sqm": 55},
            after={"estimate_low_huf": 8_000_000, "estimate_mid_huf": 9_500_000},
        )
    """

    def __init__(self, model: str = "gemini-2.0-flash-lite", temperature: float = 0.15):
        self._model = model
        self._temperature = temperature

    async def generate(
        self,
        trace_id: str,
        source: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> VibeDiff:
        """Produce a Vibe Diff for the given before/after snapshots."""
        vibe_id = f"vd-{uuid.uuid4().hex[:12]}"

        try:
            explanation = await self._llm_vibe_diff(before, after)
        except Exception as exc:
            logger.warning("[%s] VibeDiff LLM call failed: %s", trace_id, exc)
            explanation = {
                "explanation_hu": "A rendszer nem tudott automatikus magyarázatot generálni. Kérjük, ellenőrizze a nyers adatokat.",
                "explanation_en": "Could not generate automatic explanation. Please review raw data.",
                "key_drivers": [],
            }

        return VibeDiff(
            vibe_id=vibe_id,
            trace_id=trace_id,
            source=source,
            explanation_hu=explanation.get("explanation_hu", ""),
            explanation_en=explanation.get("explanation_en", ""),
            key_drivers=explanation.get("key_drivers", []),
            before_snapshot=before,
            after_snapshot=after,
            model=self._model,
        )

    async def _llm_vibe_diff(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a low-temperature LLM to produce the Vibe Diff."""
        from google import genai

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY not set")

        client = genai.Client(api_key=api_key)

        prompt = (
            "Before (input parameters):\n"
            f"{json.dumps(before, ensure_ascii=False, indent=2)}\n\n"
            "After (agent output):\n"
            f"{json.dumps(after, ensure_ascii=False, indent=2)}"
        )

        response = client.models.generate_content(
            model=self._model,
            contents=prompt,
            config={
                "system_instruction": VIBE_DIFF_SYSTEM_PROMPT,
                "response_mime_type": "application/json",
                "temperature": self._temperature,
            },
        )

        return json.loads(response.text)
