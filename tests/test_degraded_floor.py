"""判斷不可用時的等級下限（2026-08-23，known-issues #17）。

NLP 走 fallback 時 delta 恆為 0，而規則引擎只看行為（連發、字數比、時間），
抓不到語意。於是「你敢離開我 我就殺死你」這種單獨一句、間隔正常的訊息會被判 safe
——一個安全系統在自己壞掉時回答「安全」。

修法只墊**等級**，不動 risk_state 的任何維度。本檔鎖住三件事：

1. 下限確實生效（safe → observation）
2. **risk_state 完全不變**——否則會累積，變成「系統壞得越多次就認為你越危險」
3. 規則判得更高時下限失效——不得壓低既有判斷

（本專案未安裝 pytest-asyncio，沿用既有測試的 asyncio.run 寫法。）
"""

import asyncio
from unittest.mock import patch

import pytest

from app.models.schemas import RiskState


CONFIG = {
    "decay_factor": 0.7,
    "weights": {"decision_logic": {
        "W_MAX": 0.60, "W_SPREAD": 0.30, "W_TREND": 0.10,
        "NOISE_FLOOR": 0.10, "DECREASE_DAMPING": 0.03, "SPREAD_MODE": "effdim",
    }},
}


class FakeLog:
    """所有 IO 都不打真的 Appwrite；記下寫進歷史的狀態供斷言。"""

    def __init__(self):
        self.saved_state = None
        self.saved_level = None

    async def get_latest_risk_state_with_time(self, c, u):
        return RiskState(), None

    async def get_recent_risk_state_history(self, c, u, limit=5):
        return []

    async def get_recent_feedbacks(self, c, u, limit=5):
        return []

    async def get_recent_guardrail_context_reviews(self, c, u, limit=5):
        return []

    async def save_risk_state_history(self, c, u, msg_id, state, level, delta, decay_applied=False):
        self.saved_state, self.saved_level = state, level


def _run(delta: RiskState, degraded: bool):
    from app.core.risk_state import RiskStateMachine
    m = RiskStateMachine()
    log = FakeLog()
    m.chat_log_service = log
    with patch("app.core.risk_state.KBService.get_fusion_config", return_value=CONFIG):
        state, level = asyncio.run(m.update("c", "u", "m", delta, degraded_with_flags=degraded))
    return state, level, m.last_diagnostic, log


QUIET = RiskState()                                   # NLP fallback：全 0
RULES_FOUND = RiskState(harassment=0.55)              # 規則自己就判到 restricted 區間


def test_floor_lifts_safe_to_observation():
    """NLP 不可用 ＋ 命中禁詞 → 至少 observation，不再回答「安全」。"""
    _, level, diag, _ = _run(QUIET, degraded=True)

    assert level == "observation"
    assert diag["reason"] == "degraded_floor"
    # composite 本身仍是低的——下限墊的是等級，不是分數
    assert diag["composite_score"] < 0.10


def test_floor_does_not_touch_risk_state():
    """🔴 最關鍵：五個維度必須完全不動。

    若把下限寫進維度，後續訊息會在此基礎上累積，
    等於「系統壞得越多次就認為你越危險」。
    """
    state, _, _, log = _run(QUIET, degraded=True)

    assert state.model_dump() == RiskState().model_dump()
    assert log.saved_state.model_dump() == RiskState().model_dump()


def test_floor_never_lowers_an_existing_judgement():
    """規則判得比 observation 高時，下限不得把它壓下來。"""
    _, level, diag, _ = _run(RULES_FOUND, degraded=True)

    assert level in ("warning", "restricted", "blocked")
    assert diag["reason"] != "degraded_floor"


def test_no_floor_when_not_degraded():
    """NLP 正常時不套下限——正常的 safe 仍是 safe。"""
    _, level, diag, _ = _run(QUIET, degraded=False)

    assert level == "safe"
    assert diag["reason"] != "degraded_floor"


def test_floor_is_idempotent_across_calls():
    """連續多次降級不會愈墊愈高——下限是持平的，不是累加的。"""
    levels = [_run(QUIET, degraded=True)[1] for _ in range(5)]

    assert levels == ["observation"] * 5
