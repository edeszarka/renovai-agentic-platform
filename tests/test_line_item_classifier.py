"""Unit tests for the three-tier line-item classifier and the szigeteles rule.

All provider calls are mocked — no API key is needed to run this suite.
"""

from __future__ import annotations

import pytest

from scripts.line_item_classifier import (
    REVIEW,
    LLMProvider,
    SzigetelesItem,
    classify_line_item,
    enforce_szigeteles_rule,
    normalize_name,
    reexamine_szigeteles_items,
)


class FakeHTTPError(Exception):
    """Stand-in for an SDK HTTP error; exposes ``status_code``."""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


class FakeProvider:
    """A provider whose handler receives ``(call_number, prompt)``."""

    def __init__(self, name: str, handler):
        self.name = name
        self.model = f"{name}-model"
        self.calls = 0
        self._handler = handler

    def as_provider(self) -> LLMProvider:
        def call(prompt: str) -> str:
            self.calls += 1
            return self._handler(self.calls, prompt)

        return LLMProvider(self.name, self.model, call)


def _no_sleep(_seconds: float) -> None:
    return None


# ---------------------------------------------------------------------------
# Tier 1 — lookup skips the LLM entirely
# ---------------------------------------------------------------------------


def test_lookup_hit_skips_providers():
    groq = FakeProvider("groq", lambda n, p: '{"category_key": "villany"}')
    lookup = {normalize_name("Bontás"): "bontas"}

    result = classify_line_item("Bontás", lookup=lookup, providers=[groq.as_provider()])

    assert result.category_key == "bontas"
    assert result.tier == "lookup"
    assert groq.calls == 0, "a lookup hit must not call any provider"


def test_lookup_is_accent_and_case_insensitive():
    lookup = {normalize_name("Kéménybélelés"): "futes_rendszer"}
    result = classify_line_item("  KEMENYBELELES  ", lookup=lookup, providers=[])
    assert result.tier == "lookup"
    assert result.category_key == "futes_rendszer"


# ---------------------------------------------------------------------------
# Tier 2/3 — lookup miss → Groq → DeepSeek
# ---------------------------------------------------------------------------


def test_lookup_miss_uses_groq_first():
    groq = FakeProvider("groq", lambda n, p: '{"category_key": "villany"}')
    deepseek = FakeProvider("deepseek", lambda n, p: '{"category_key": "bontas"}')

    result = classify_line_item(
        "Some new line item",
        lookup={},
        providers=[groq.as_provider(), deepseek.as_provider()],
        sleep=_no_sleep,
    )
    assert result.tier == "groq"
    assert result.category_key == "villany"
    assert groq.calls == 1
    assert deepseek.calls == 0


def test_groq_failure_falls_through_to_deepseek():
    def groq_handler(n, p):
        raise RuntimeError("boom")

    groq = FakeProvider("groq", groq_handler)
    deepseek = FakeProvider("deepseek", lambda n, p: '{"category_key": "furdo"}')

    result = classify_line_item(
        "Some new line item",
        lookup={},
        providers=[groq.as_provider(), deepseek.as_provider()],
        sleep=_no_sleep,
    )
    assert result.tier == "deepseek"
    assert result.category_key == "furdo"
    assert groq.calls == 1
    assert deepseek.calls == 1


def test_invalid_json_falls_through_to_next_provider():
    groq = FakeProvider("groq", lambda n, p: "not json at all")
    deepseek = FakeProvider("deepseek", lambda n, p: '{"category_key": "klima"}')

    result = classify_line_item(
        "Some new line item",
        lookup={},
        providers=[groq.as_provider(), deepseek.as_provider()],
        sleep=_no_sleep,
    )
    assert result.tier == "deepseek"
    assert result.category_key == "klima"


# ---------------------------------------------------------------------------
# Error policy
# ---------------------------------------------------------------------------


def test_quota_error_is_not_retried_and_fails_over():
    groq = FakeProvider(
        "groq",
        lambda n, p: (_ for _ in ()).throw(FakeHTTPError(429, "RESOURCE_EXHAUSTED")),
    )
    deepseek = FakeProvider("deepseek", lambda n, p: '{"category_key": "egyeb"}')

    result = classify_line_item(
        "Some new line item",
        lookup={},
        providers=[groq.as_provider(), deepseek.as_provider()],
        sleep=_no_sleep,
    )
    assert result.tier == "deepseek"
    assert groq.calls == 1, "quota errors must not be retried on the same provider"


def test_quota_error_with_no_secondary_is_unresolved_without_retry():
    groq = FakeProvider(
        "groq", lambda n, p: (_ for _ in ()).throw(FakeHTTPError(429, "quota"))
    )

    result = classify_line_item(
        "Some new line item", lookup={}, providers=[groq.as_provider()], sleep=_no_sleep
    )
    assert result.category_key is None
    assert result.tier == "unresolved"
    assert groq.calls == 1


def test_transient_error_is_retried_once_then_succeeds():
    def handler(n, p):
        if n == 1:
            raise FakeHTTPError(503, "service unavailable")
        return '{"category_key": "bontas"}'

    groq = FakeProvider("groq", handler)
    result = classify_line_item(
        "Some new line item", lookup={}, providers=[groq.as_provider()], sleep=_no_sleep
    )
    assert result.tier == "groq"
    assert result.category_key == "bontas"
    assert groq.calls == 2, "one transient retry is allowed"


# ---------------------------------------------------------------------------
# Task B — szigeteles rule (deterministic guards)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,notes,llm,expected",
    [
        # chimney lining -> futes_rendszer regardless of the LLM
        ("Kéménybélelés", None, "szigeteles", "futes_rendszer"),
        ("Kéménybélelés, engedélyekkel", None, None, "futes_rendszer"),
        # bathroom / wet-room waterproofing -> furdo
        ("Fürdő vízszigetelése", None, "szigeteles", "furdo"),
        ("Mindkét fürdő vízszigetelése", None, "furdo", "furdo"),
        ("Fürdők vízsszigetelése", None, None, "furdo"),
        # ambiguous / non-bathroom waterproofing -> needs review, never forced
        ("Vízszigetelés, hajlaterősítéssel", None, "furdo", REVIEW),
        ("Vízszigetelés", None, "szigeteles", REVIEW),
        ("Erkély vízszigetelés", None, "furdo", REVIEW),
        # genuine interior insulation -> stays szigeteles
        (
            "Multipor hő-hang szigetelés folyósóval közös falra",
            None,
            "szigeteles",
            "szigeteles",
        ),
        # no rule + invalid/absent LLM decision -> review (never guess)
        ("Valami ismeretlen", None, None, REVIEW),
        ("Valami ismeretlen", None, "nem_letezo_kategoria", REVIEW),
    ],
)
def test_enforce_szigeteles_rule(name, notes, llm, expected):
    assert enforce_szigeteles_rule(name, notes, llm) == expected


# ---------------------------------------------------------------------------
# Task B — batched re-examination pipeline
# ---------------------------------------------------------------------------


def _reexam_response():
    return (
        '{"assignments": ['
        '{"item_id": 0, "category_key": "futes_rendszer", "reason": "chimney"},'
        '{"item_id": 1, "category_key": "furdo", "reason": "bathroom"},'
        '{"item_id": 2, "category_key": "furdo", "reason": "guessed bathroom"},'
        '{"item_id": 3, "category_key": "szigeteles", "reason": "interior"}'
        "]}"
    )


def test_reexamine_pipeline_enforces_rules():
    items = [
        SzigetelesItem("id0", "Kéménybélelés"),
        SzigetelesItem("id1", "Fürdő vízszigetelése"),
        SzigetelesItem("id2", "Vízszigetelés, hajlaterősítéssel"),
        SzigetelesItem("id3", "Multipor hő-hang szigetelés folyósóval közös falra"),
    ]
    groq = FakeProvider("groq", lambda n, p: _reexam_response())

    results = reexamine_szigeteles_items(
        items, providers=[groq.as_provider()], sleep=_no_sleep
    )
    finals = {r.item_id: r.final_category for r in results}

    assert finals["id0"] == "futes_rendszer"
    assert finals["id1"] == "furdo"
    # LLM guessed furdo, but the non-bathroom waterproofing rule forces review
    assert finals["id2"] is None
    assert finals["id3"] == "szigeteles"
    assert groq.calls == 1


def test_reexamine_quota_fails_over_to_deepseek():
    items = [
        SzigetelesItem("id0", "Multipor hő-hang szigetelés folyósóval közös falra")
    ]
    groq = FakeProvider(
        "groq", lambda n, p: (_ for _ in ()).throw(FakeHTTPError(429, "quota"))
    )
    deepseek = FakeProvider("deepseek", lambda n, p: _reexam_response())

    results = reexamine_szigeteles_items(
        items, providers=[groq.as_provider(), deepseek.as_provider()], sleep=_no_sleep
    )
    assert results[0].tier == "deepseek"
    assert groq.calls == 1
    assert deepseek.calls == 1


def test_reexamine_without_providers_still_applies_deterministic_rules():
    items = [
        SzigetelesItem("id0", "Kéménybélelés"),
        SzigetelesItem("id1", "Fürdő vízszigetelése"),
        SzigetelesItem("id2", "Utcafront hőszigetelése"),
    ]
    results = reexamine_szigeteles_items(items, providers=[], sleep=_no_sleep)
    finals = {r.item_id: r.final_category for r in results}
    assert finals["id0"] == "futes_rendszer"
    assert finals["id1"] == "furdo"
    # no deterministic rule and no LLM -> review, never guessed
    assert finals["id2"] is None
