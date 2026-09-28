"""BackgroundJudgeService tests (no network, no Appwrite)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.models.schemas import Message
from app.services.background_judge_service import BackgroundJudgeService


def test_should_review_requires_guardrail_signal():
    service = BackgroundJudgeService(chat_log_service=MagicMock(), adapter=MagicMock(), model="test-model")

    assert service.should_review([], {"flagged": False}) is False
    assert service.should_review(["殺人"], {"flagged": False}) is True
    assert service.should_review([], {"flagged": True, "categories": "S1"}) is True


def test_review_saves_concerning_judgment(monkeypatch):
    monkeypatch.setenv("BACKGROUND_JUDGE_ENABLED", "true")
    chat_service = MagicMock()
    chat_service.save_guardrail_context_review = AsyncMock(return_value=True)
    adapter = MagicMock()
    adapter.generate.return_value = '{"judgment":"concerning","reasoning":"含有針對對方的性要求"}'
    service = BackgroundJudgeService(chat_log_service=chat_service, adapter=adapter, model="test-model")

    asyncio.run(service.review_guardrail_context(
        conversation_id="conv1",
        sender_id="Alice",
        msg_id="msg1",
        current_message="傳裸照給我",
        recent_messages=[Message(sender="Bob", content="嗨", timestamp="2026-01-01T00:00:00Z")],
        flagged_words=[],
        classifier_flag={"flagged": True, "categories": "S2"},
    ))

    chat_service.save_guardrail_context_review.assert_awaited_once()
    kwargs = chat_service.save_guardrail_context_review.call_args.kwargs
    assert kwargs["judgment"] == "concerning"
    assert kwargs["model"] == "test-model"
    assert kwargs["classifier_flag"]["flagged"] is True


def test_review_malformed_response_saves_unclear(monkeypatch):
    monkeypatch.setenv("BACKGROUND_JUDGE_ENABLED", "true")
    chat_service = MagicMock()
    chat_service.save_guardrail_context_review = AsyncMock(return_value=True)
    adapter = MagicMock()
    adapter.generate.return_value = "not json"
    service = BackgroundJudgeService(chat_log_service=chat_service, adapter=adapter, model="test-model")

    asyncio.run(service.review_guardrail_context(
        conversation_id="conv1",
        sender_id="Alice",
        msg_id="msg1",
        current_message="新聞說那個殺人案",
        recent_messages=[],
        flagged_words=["殺人"],
        classifier_flag={"flagged": False, "categories": ""},
    ))

    kwargs = chat_service.save_guardrail_context_review.call_args.kwargs
    assert kwargs["judgment"] == "unclear"
    assert "Malformed" in kwargs["reasoning"]


def test_disabled_service_skips_adapter_and_save(monkeypatch):
    monkeypatch.setenv("BACKGROUND_JUDGE_ENABLED", "false")
    chat_service = MagicMock()
    chat_service.save_guardrail_context_review = AsyncMock(return_value=True)
    adapter = MagicMock()
    service = BackgroundJudgeService(chat_log_service=chat_service, adapter=adapter, model="test-model")

    asyncio.run(service.review_guardrail_context(
        conversation_id="conv1",
        sender_id="Alice",
        msg_id="msg1",
        current_message="新聞說那個殺人案",
        recent_messages=[],
        flagged_words=["殺人"],
        classifier_flag={"flagged": False, "categories": ""},
    ))

    adapter.generate.assert_not_called()
    chat_service.save_guardrail_context_review.assert_not_awaited()
