"""Unit tests for the taxonomy LLM helper's provider/error policy.

These tests use fake provider callables and never touch the network or require
an API key.  They pin the exact behaviour required for this task:

* a successful call returns the text and the provider/model provenance;
* a transient error (timeout / 5xx) is retried once, then succeeds;
* a quota / 429 error is NOT retried against the same provider;
* on quota the call fails over to the secondary provider exactly once;
* with no providers configured it fails fast with an actionable message.
"""

from __future__ import annotations

import pytest

from scripts.taxonomy_llm_client import (
    LLMError,
    LLMResult,
    Provider,
    ProviderResponse,
    call_llm,
    classify_error,
    resolve_providers,
)


class FakeHTTPError(Exception):
    """Stand-in for an SDK HTTP error; exposes ``status_code``."""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


def _no_sleep(_seconds: float) -> None:
    return None


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "exc,expected",
    [
        (FakeHTTPError(429), "quota"),
        (FakeHTTPError(503), "transient"),
        (FakeHTTPError(500), "transient"),
        (FakeHTTPError(400), "permanent"),
        (RuntimeError("RESOURCE_EXHAUSTED: quota"), "quota"),
        (RuntimeError("insufficient_quota"), "quota"),
        (TimeoutError("timed out"), "transient"),
        (RuntimeError("unexpected schema"), "permanent"),
    ],
)
def test_classify_error(exc, expected):
    assert classify_error(exc) == expected


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------

def test_success_returns_text_and_provenance():
    provider = Provider(
        "deepseek",
        lambda prompt, response_json: ProviderResponse(text='{"ok": true}', model="deepseek-chat"),
    )
    result = call_llm("prompt", response_json=True, providers=[provider], sleep=_no_sleep)
    assert isinstance(result, LLMResult)
    assert result.text == '{"ok": true}'
    assert result.provider == "deepseek"
    assert result.model == "deepseek-chat"


# ---------------------------------------------------------------------------
# Transient: retry once then succeed
# ---------------------------------------------------------------------------

def test_transient_error_retries_once_then_succeeds():
    calls = {"n": 0}
    sleeps = []

    def flaky(prompt, response_json):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FakeHTTPError(503, "service unavailable")
        return ProviderResponse(text="recovered", model="deepseek-chat")

    result = call_llm(
        "prompt",
        providers=[Provider("deepseek", flaky)],
        max_retries=1,
        sleep=sleeps.append,
    )
    assert result.text == "recovered"
    assert calls["n"] == 2
    assert len(sleeps) == 1  # exactly one backoff sleep


# ---------------------------------------------------------------------------
# Quota: no retry, fail fast
# ---------------------------------------------------------------------------

def test_quota_error_is_not_retried_and_fails_fast():
    calls = {"n": 0}

    def quota(prompt, response_json):
        calls["n"] += 1
        raise FakeHTTPError(429, "RESOURCE_EXHAUSTED")

    with pytest.raises(LLMError) as excinfo:
        call_llm(
            "prompt",
            providers=[Provider("deepseek", quota)],
            max_retries=1,
            sleep=_no_sleep,
        )
    assert calls["n"] == 1, "quota errors must not be retried"
    assert "deepseek" in str(excinfo.value)
    assert "quota" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Quota: fail over to secondary once
# ---------------------------------------------------------------------------

def test_quota_error_fails_over_to_secondary_once():
    primary_calls = {"n": 0}
    secondary_calls = {"n": 0}

    def primary(prompt, response_json):
        primary_calls["n"] += 1
        raise FakeHTTPError(429, "insufficient_quota")

    def secondary(prompt, response_json):
        secondary_calls["n"] += 1
        return ProviderResponse(text="from gemini", model="gemini-2.5-flash")

    result = call_llm(
        "prompt",
        providers=[Provider("deepseek", primary), Provider("gemini", secondary)],
        max_retries=1,
        sleep=_no_sleep,
    )
    assert result.text == "from gemini"
    assert result.provider == "gemini"
    assert primary_calls["n"] == 1
    assert secondary_calls["n"] == 1


# ---------------------------------------------------------------------------
# Transient exhausted: fail over after retries
# ---------------------------------------------------------------------------

def test_transient_exhausted_then_fails_over():
    primary_calls = {"n": 0}

    def primary(prompt, response_json):
        primary_calls["n"] += 1
        raise FakeHTTPError(503, "server error")

    def secondary(prompt, response_json):
        return ProviderResponse(text="secondary ok", model="gemini-2.5-flash")

    result = call_llm(
        "prompt",
        providers=[Provider("deepseek", primary), Provider("gemini", secondary)],
        max_retries=1,
        sleep=_no_sleep,
    )
    assert result.text == "secondary ok"
    assert primary_calls["n"] == 2  # initial + one retry, then failover


# ---------------------------------------------------------------------------
# No providers
# ---------------------------------------------------------------------------

def test_no_providers_raises_actionable_error():
    with pytest.raises(LLMError) as excinfo:
        call_llm("prompt", providers=[], sleep=_no_sleep)
    message = str(excinfo.value)
    assert "DEEPSEEK_API_KEY" in message
    assert "GOOGLE_API_KEY" in message


# ---------------------------------------------------------------------------
# Provider resolution priority
# ---------------------------------------------------------------------------

def test_resolve_providers_prefers_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "goog-test")
    names = [p.name for p in resolve_providers()]
    assert names == ["deepseek", "gemini"]


def test_resolve_providers_gemini_only_when_deepseek_absent(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "goog-test")
    names = [p.name for p in resolve_providers()]
    assert names == ["gemini"]


def test_resolve_providers_empty_without_keys(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert resolve_providers() == []
