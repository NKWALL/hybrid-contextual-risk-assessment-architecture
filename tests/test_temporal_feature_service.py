from datetime import datetime, timedelta, timezone

from app.models.schemas import Message
from app.services.temporal_feature_service import TemporalFeatureService


def _message(sender: str, content: str, seconds_ago: int) -> Message:
    timestamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    return Message(sender=sender, content=content, timestamp=timestamp)


def test_reply_latency_only_when_last_is_opposite_party():
    history = [_message("Bob", "嗨", 300)]

    result = TemporalFeatureService.calculate(
        current_content="嗨你好",
        current_sender="Alice",
        history=history,
    )

    assert 295 <= result.reply_latency_seconds <= 305
    assert 295 <= result.idle_time_seconds <= 305
    assert result.unreplied_count == 1
    assert result.consecutive_char_count == len("嗨你好")


def test_reply_latency_none_when_last_is_self():
    history = [_message("Alice", "嗨", 600)]

    result = TemporalFeatureService.calculate(
        current_content="嗨嗨",
        current_sender="Alice",
        history=history,
    )

    assert result.reply_latency_seconds is None
    assert 595 <= result.idle_time_seconds <= 605
    assert result.unreplied_count == 2
    assert result.consecutive_char_count == len("嗨") + len("嗨嗨")


def test_empty_history_returns_none_seconds():
    result = TemporalFeatureService.calculate(
        current_content="hi",
        current_sender="Alice",
        history=[],
    )

    assert result.reply_latency_seconds is None
    assert result.idle_time_seconds is None
    assert result.unreplied_count == 1
    assert result.message_burst_count == 1


def test_short_split_messages_accumulate():
    history = [
        _message("Alice", "我", 10),
        _message("Alice", "剛剛", 5),
        _message("Alice", "到", 2),
    ]

    result = TemporalFeatureService.calculate(
        current_content="家",
        current_sender="Alice",
        history=history,
    )

    assert result.unreplied_count == 4
    assert result.consecutive_char_count == len("我") + len("剛剛") + len("到") + len("家")
    assert result.message_burst_count >= 2


def test_unreplied_resets_after_opponent_reply():
    history = [
        _message("Alice", "我", 60),
        _message("Bob", "好", 30),
        _message("Alice", "到家了", 5),
    ]

    result = TemporalFeatureService.calculate(
        current_content="嗯",
        current_sender="Alice",
        history=history,
    )

    assert result.unreplied_count == 2
    assert result.consecutive_char_count == len("到家了") + len("嗯")


def test_volume_ratio_dominated_by_sender():
    history = [
        _message("Bob", "嗨", 100),
        _message("Alice", "x" * 20, 50),
    ]

    result = TemporalFeatureService.calculate(
        current_content="y" * 30,
        current_sender="Alice",
        history=history,
    )

    assert result.volume_ratio > 0.95
