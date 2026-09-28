"""介入顯示節流的行為測試。

節流的目的是避免同一段對話內連續觸發時重複跳出提示（警示疲勞），
但不得因此在風險升高時保持沉默。

（本專案未安裝 pytest-asyncio，沿用既有測試的 asyncio.run 寫法。）
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.intervention_engine import InterventionEngine


class FakeLogService:
    """模擬 chat_log_service.get_last_displayed_intervention。"""

    def __init__(self, level=None, seconds_ago=None):
        self._level = level
        self._seconds_ago = seconds_ago

    async def get_last_displayed_intervention(self, conv_id, user_id, role):
        if self._level is None:
            return None
        ts = (datetime.now(timezone.utc) - timedelta(seconds=self._seconds_ago)).isoformat()
        return {"risk_level": self._level, "timestamp": ts}


def directive(action="show_reflection_banner", **extra):
    d = {
        "action": action,
        "cooldown_seconds": 0,
        "require_acknowledgment": False,
        "content": {"body": "x"},
    }
    d.update(extra)
    return d


def throttle(d, level, fake, role="sender"):
    engine = InterventionEngine()
    return asyncio.run(engine._apply_throttle(d, level, "conv1", "user1", role, fake))


def test_no_previous_display_shows():
    """沒有前次顯示紀錄時應正常顯示。"""
    r = throttle(directive(), "warning", FakeLogService())
    assert r["action"] == "show_reflection_banner"


def test_within_window_same_level_suppressed():
    """節流窗內且等級未升高 → 抑制。"""
    r = throttle(directive(), "warning", FakeLogService("warning", 60))
    assert r["action"] == "suppressed"
    assert r["content"] is None
    assert r["throttled"]["reason"] == "display_throttle"


def test_outside_window_shows():
    """超出節流窗 → 正常顯示。"""
    r = throttle(directive(), "warning", FakeLogService("warning", 400))
    assert r["action"] == "show_reflection_banner"


def test_escalation_always_shows():
    """等級較上次顯示時更高 → 一律顯示，不得因節流而沉默。"""
    r = throttle(directive(), "warning", FakeLogService("observation", 10))
    assert r["action"] == "show_reflection_banner"


@pytest.mark.parametrize("level,action", [
    ("restricted", "show_modal_warning"),
    ("blocked", "block_message"),
])
def test_high_levels_never_throttled(level, action):
    """restricted / blocked 節流窗為 0，即使剛顯示過也必須再次顯示。"""
    r = throttle(directive(action), level, FakeLogService(level, 5))
    assert r["action"] == action


def test_none_action_untouched():
    """本來就沒有動作時不做任何處理。"""
    r = throttle(directive("none", content=None), "warning", FakeLogService("warning", 10))
    assert r["action"] == "none"


def test_template_override_window():
    """模板設定的 display_throttle_seconds 應覆寫該等級的預設值。"""
    d = directive(display_throttle_seconds=30)
    # 60 秒前顯示過，已超出模板設定的 30 秒窗 → 應顯示
    r = throttle(d, "warning", FakeLogService("warning", 60))
    assert r["action"] == "show_reflection_banner"


def test_malformed_timestamp_fails_open():
    """上次時間無法解析時應保守顯示，而非靜默抑制。"""

    class BadTs:
        async def get_last_displayed_intervention(self, *a, **k):
            return {"risk_level": "warning", "timestamp": "not-a-timestamp"}

    r = throttle(directive(), "warning", BadTs())
    assert r["action"] == "show_reflection_banner"


def test_execute_without_log_service_does_not_throttle():
    """未提供 chat_log_service 時維持既有行為（不節流）。"""
    engine = InterventionEngine()
    cmd = asyncio.run(engine.execute(
        risk_level="safe", risk_state={"harassment": 0.0}, diagnosis={},
        conv_id="c", sender_id="s", receiver_id="r", msg_id="m",
        decision_reason="normal",
    ))
    assert cmd["risk_level"] == "safe"


def test_ui_behavior_keys_pass_through_to_directive():
    """ui_behavior 的旗標必須原樣送到前端（2026-08-15 修）。

    原本是白名單，只讀 cooldown / require_ack / display_throttle_seconds。
    後果是 show_options 明明存在於四個收件方模板中卻從未送達前端，
    前端只能自行用 risk_level 硬判。
    """
    templates = [{
        "template_id": "restrict_receiver_options", "primary_risk_type": "any",
        "action_type": "show_safety_info_card",
        "message_template": {"title": "已加強保護", "body": "…"},
        "ui_behavior": {"cooldown": 0, "require_ack": False, "show_options": True,
                        "mascot": "heart", "show_feedback_buttons": True,
                        "allow_report_text": True, "display_throttle_seconds": 120},
    }]
    d = InterventionEngine()._get_specific_directive(templates, "any", "receiver")

    assert d["show_options"] is True
    assert d["mascot"] == "heart"
    assert d["show_feedback_buttons"] is True
    assert d["allow_report_text"] is True
    assert d["display_throttle_seconds"] == 120
    # 已改名的兩個鍵不重複輸出原名
    assert "cooldown" not in d and "require_ack" not in d
    assert d["cooldown_seconds"] == 0 and d["require_acknowledgment"] is False
