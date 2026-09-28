"""已處置豁免（2026-08-15）的行為測試。

【基本映射不變】risk_level → 介入動作。累積到 blocked 就是要攔、要鎖。
【例外】同一則違規服完刑之後，不因殘留的累積狀態被重複處罰。

實測背景（threshold_v2_rule_heavy：decay=0.7、NOISE_FLOOR=0.10、effdim）：
修正前 blocked 之後的正常訊息會再攔再鎖一次，最糟連鎖 2 次共 60 分鐘，
期間 1 則無辜訊息被靜默扣住。幅度不是重點——為一則本身沒問題的訊息
施加處置，發生一次就已經是錯的。

四個條件缺一不可，本檔每條各鎖一個測試——任何一條被放寬，
豁免就會從「不重複處罰」變成「累積機制的保護傘」。

（本專案未安裝 pytest-asyncio，沿用既有測試的 asyncio.run 寫法。）
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import patch

from app.core.intervention_engine import InterventionEngine


TEMPLATES = [
    {"template_id": "block_sender_final", "primary_risk_type": "any",
     "action_type": "block_message",
     "message_template": {"title": "訊息未送出", "body": "…"},
     "ui_behavior": {"cooldown": 1800, "require_ack": True}},
    {"template_id": "block_receiver_notice", "primary_risk_type": "any",
     "action_type": "show_blocked_notice",
     "message_template": {"body": "系統攔截了一則…"},
     "ui_behavior": {"show_options": True}},
    {"template_id": "sender_state_notice", "primary_risk_type": "any",
     "action_type": "show_reflection_banner",
     "message_template": {"body": "這段對話仍在觀察中"},
     "ui_behavior": {"display_throttle_seconds": 300}},
    {"template_id": "receiver_state_notice", "primary_risk_type": "any",
     "action_type": "show_safety_info_card",
     "message_template": {"body": "這段對話仍在加強保護中"},
     "ui_behavior": {"show_options": True, "allow_report_text": True,
                     "display_throttle_seconds": 300}},
]

QUIET_DELTA = {"sexual_boundary": 0.0, "coercion": 0.01, "manipulation": 0.0,
               "harassment": 0.02, "emotional_pressure": 0.0}
RISKY_DELTA = {"sexual_boundary": 0.4, "coercion": 0.0, "manipulation": 0.0,
               "harassment": 0.0, "emotional_pressure": 0.0}
STATE = {"sexual_boundary": 0.9, "coercion": 0.1, "manipulation": 0.0,
         "harassment": 0.0, "emotional_pressure": 0.0}


class FakeLog:
    def __init__(self, last_level="blocked", remaining=0, seconds_ago=1800):
        self._level, self._remaining, self._ago = last_level, remaining, seconds_ago

    async def get_last_displayed_intervention(self, conv_id, user_id, role):
        if self._level is None:
            return None
        ts = (datetime.now(timezone.utc) - timedelta(seconds=self._ago)).isoformat()
        return {"risk_level": self._level, "timestamp": ts}

    async def get_remaining_cooldown(self, conv_id, user_id):
        return self._remaining


def _by_level(level):
    """模擬 kb_service.get_interventions_by_level 的 WHERE risk_level = %s。

    狀態式模板存於偽等級 'exempt'，不會出現在真實等級的查詢結果中——
    這正是 2026-08-15 實作時差點漏掉的接線點。
    """
    if level == "exempt":
        return [t for t in TEMPLATES if t["template_id"].endswith("_state_notice")]
    return [t for t in TEMPLATES if not t["template_id"].endswith("_state_notice")]


def run(level="blocked", delta=QUIET_DELTA, log=None):
    with patch("app.core.intervention_engine.KBService.get_interventions_by_level",
               side_effect=_by_level):
        return asyncio.run(InterventionEngine().execute(
            risk_level=level, risk_state=STATE, diagnosis={},
            conv_id="c1", sender_id="s1", receiver_id="r1", msg_id="m1",
            decision_reason="normal",
            chat_log_service=log if log is not None else FakeLog(),
            message_delta=delta,
        ))


def test_exemption_releases_sanction_after_serving():
    """服完刑後發正常訊息：不攔、不鎖、不強制確認。"""
    cmd = run()
    assert cmd["sanction_exempted"] is True
    assert cmd["sender_directive"]["cooldown_seconds"] == 0
    assert cmd["sender_directive"]["require_acknowledgment"] is False
    assert cmd["sender_directive"]["action"] == "show_reflection_banner"   # banner 不是跳窗


def test_exemption_suppresses_admin_review():
    """豁免時不通報人工覆核——本則沒有新的違規事實。"""
    assert run()["admin_directive"] is None


def test_condition_1_first_time_is_never_exempted():
    """① 首次累積到 blocked 一律照攔——這是累積機制的核心，不得鬆動。"""
    cmd = run(log=FakeLog(last_level=None))
    assert cmd["sanction_exempted"] is False
    assert cmd["sender_directive"]["cooldown_seconds"] == 1800
    assert cmd["sender_directive"]["action"] == "block_message"


def test_condition_2_escalation_breaks_exemption():
    """② 上次 restricted、本次 blocked：風險升高，豁免失效。"""
    cmd = run(level="blocked", log=FakeLog(last_level="restricted"))
    assert cmd["sanction_exempted"] is False
    assert cmd["sender_directive"]["cooldown_seconds"] == 1800


def test_condition_3_cooldown_not_served_breaks_exemption():
    """③ 還在冷卻期內硬發：不給豁免。

    冷卻無伺服器端強制，此條同時擋下繞過前端直接打 API 的情形。
    """
    cmd = run(log=FakeLog(remaining=900))
    assert cmd["sanction_exempted"] is False
    assert cmd["sender_directive"]["cooldown_seconds"] == 1800


def test_condition_4_new_risk_breaks_exemption():
    """④ 服完刑又發違規訊息：照攔照鎖。"""
    cmd = run(delta=RISKY_DELTA)
    assert cmd["sanction_exempted"] is False
    assert cmd["sender_directive"]["action"] == "block_message"


# ── 條件④的邊界（2026-08-31 新增）─────────────────────────────
#
# 舊值 ε=0.05 是對齊 `threshold_v1` 的 NOISE_FLOOR，但實際運行的是
# `threshold_v2_rule_heavy`（NOISE_FLOOR 0.10）——錨錯 config。
#
# 後果：ε 被設在「系統做得到的最小值」以下。`nlp_engine` 的
# NLP_NOISE_FLOOR 會把低於 0.10 的 NLP 分數歸零，所以最小非零 NLP 分數
# 就是 0.10，經融合後 delta 約 0.055。ε=0.05 落在它下面，等於要求
# 「NLP 給了字面上的 0」。
#
# 實測（blocked → 服完刑 → 第一則中性訊息，跑 5 次）：
#     ε=0.05 → 1/5 豁免      ε=0.10 → 5/5 豁免
#
# 舊測試用 0.02／0.4 兩個離邊界很遠的值，所以這個錯誤從未被測到——
# 以下三條把邊界本身鎖住。

MIN_NONZERO_DELTA = {"sexual_boundary": 0.0, "coercion": 0.0, "manipulation": 0.0,
                     "harassment": 0.055, "emotional_pressure": 0.0}


def test_minimum_nonzero_delta_is_still_exempt():
    """0.055 是系統實測能產生的最小非零 delta（NLP 給 0.10 經融合後）。

    這個值必須算「沒有新風險」——否則 #26 要修的那個情境
    （服完 30 分鐘刑、發一則正常訊息）大多數時候仍會被重複處罰。
    """
    cmd = run(delta=MIN_NONZERO_DELTA)
    assert cmd["sanction_exempted"] is True
    assert cmd["sender_directive"]["cooldown_seconds"] == 0


def test_epsilon_is_anchored_to_the_noise_floor():
    """ε 必須 ≥ NLP 的雜訊底線，不是一個調出來的數字。

    低於 NLP_NOISE_FLOOR 就等於要求「NLP 輸出字面上的 0」；
    這條在有人把 ε 調回 0.05（或把 noise floor 調高）時會失敗。
    """
    from app.core.intervention_engine import EXEMPT_DELTA_EPSILON
    from app.core.nlp_engine import NLPEngine  # noqa: F401  確保模組可載入
    import inspect
    import re

    src = inspect.getsource(NLPEngine._parse_response)
    m = re.search(r"NLP_NOISE_FLOOR\s*=\s*([\d.]+)", src)
    assert m, "找不到 NLP_NOISE_FLOOR，請確認 _parse_response 是否改寫過"
    assert EXEMPT_DELTA_EPSILON >= float(m.group(1))


def test_just_above_epsilon_is_not_exempt():
    """ε 之上一點點仍照罰——放寬的是雜訊，不是真的訊號。"""
    from app.core.intervention_engine import EXEMPT_DELTA_EPSILON
    delta = dict(MIN_NONZERO_DELTA, harassment=EXEMPT_DELTA_EPSILON + 0.01)
    cmd = run(delta=delta)
    assert cmd["sanction_exempted"] is False
    assert cmd["sender_directive"]["action"] == "block_message"


def test_exempt_receiver_keeps_protection_but_drops_feedback_buttons():
    """收件方仍受保護（行動選項、回報輸入框保留），但沒有新事件可評價。"""
    r = run()["receiver_directive"]
    assert r["show_options"] is True
    assert r["allow_report_text"] is True
    assert r["show_feedback_buttons"] is False
    assert r["display_throttle_seconds"] == 300


def test_exempt_does_not_leak_dimension_to_sender():
    """豁免文案不揭露維度——不給被觀察者校準邊界的資訊。"""
    assert run()["sender_directive"]["content"]["primary_risk_type"] == "any"


def test_no_delta_supplied_keeps_legacy_behaviour():
    """未傳 message_delta 時不啟用豁免，維持既有行為。"""
    with patch("app.core.intervention_engine.KBService.get_interventions_by_level",
               side_effect=_by_level):
        cmd = asyncio.run(InterventionEngine().execute(
            risk_level="blocked", risk_state=STATE, diagnosis={},
            conv_id="c1", sender_id="s1", receiver_id="r1", msg_id="m1",
            decision_reason="normal", chat_log_service=FakeLog()))
    assert cmd["sanction_exempted"] is False
    assert cmd["sender_directive"]["cooldown_seconds"] == 1800
