import pytest
from unittest.mock import AsyncMock, MagicMock

from orchestrator.handlers import resolve_handler, handle_combined, HANDLER_MAP


class TestResolveHandler:
    def test_known_intents_resolve(self):
        for intent in HANDLER_MAP:
            handler = resolve_handler(intent)
            assert handler is not None

    def test_combined_resolves(self):
        handler = resolve_handler("combined")
        assert handler is handle_combined

    def test_unknown_intent_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown intent"):
            resolve_handler("nonexistent_intent")

    def test_valueerror_lists_available_intents(self):
        with pytest.raises(ValueError, match="cost_estimation"):
            resolve_handler("nope")


class TestHandleCombined:
    @pytest.mark.asyncio
    async def test_returns_status_combined(self):
        policy = MagicMock()
        registry = MagicMock()
        result = await handle_combined(
            {"secondary_intents": ["cost_estimation", "due_diligence"]},
            policy, registry, "test-trace-1",
        )
        assert result["status"] == "combined"
        assert result["trace_id"] == "test-trace-1"

    @pytest.mark.asyncio
    async def test_includes_secondary_intents(self):
        policy = MagicMock()
        registry = MagicMock()
        result = await handle_combined(
            {"secondary_intents": ["market_analysis"]},
            policy, registry, "test-trace-2",
        )
        assert result["data"]["secondary_intents"] == ["market_analysis"]

    @pytest.mark.asyncio
    async def test_empty_secondary_intents(self):
        policy = MagicMock()
        registry = MagicMock()
        result = await handle_combined(
            {},
            policy, registry, "test-trace-3",
        )
        assert result["status"] == "combined"
        assert result["data"]["secondary_intents"] == []

    @pytest.mark.asyncio
    async def test_user_facing_message_present(self):
        policy = MagicMock()
        registry = MagicMock()
        result = await handle_combined(
            {}, policy, registry, "test-trace-4",
        )
        assert "message" in result["data"]
        assert len(result["data"]["message"]) > 0


class TestKeywordClassifyCombined:
    def test_multi_intent_classifies_as_combined(self):
        from orchestrator.gateway import _keyword_classify
        question = "mennyibe kerül a felújítás és mire figyeljek vásárláskor?"
        primary, secondary, confidence = _keyword_classify(question)
        assert primary == "combined"
        assert len(secondary) >= 2
