from unittest.mock import patch

import pytest

from app.core.risk_fusion import RiskFusionLayer
from app.models.schemas import RiskState


FAKE_CONFIG = {
    "weights": {
        "tiers": [
            {"min": 0.0, "max": 0.1, "beta": 0.05, "label": "Lv.1"},
            {"min": 0.1, "max": 0.2, "beta": 0.10, "label": "Lv.2"},
            {"min": 0.2, "max": 0.3, "beta": 0.20, "label": "Lv.3"},
            {"min": 0.3, "max": 0.4, "beta": 0.25, "label": "Lv.4"},
            {"min": 0.4, "max": 0.5, "beta": 0.30, "label": "Lv.5"},
            {"min": 0.5, "max": 0.6, "beta": 0.40, "label": "Lv.6"},
            {"min": 0.6, "max": 0.7, "beta": 0.45, "label": "Lv.7"},
            {"min": 0.7, "max": 0.8, "beta": 0.50, "label": "Lv.8"},
            {"min": 0.8, "max": 0.9, "beta": 0.55, "label": "Lv.9"},
            {"min": 0.9, "max": 1.0, "beta": 0.60, "label": "Lv.10"},
        ]
    }
}


def test_fuse_low_confidence_rule_heavy():
    with patch("app.core.risk_fusion.KBService.get_fusion_config", return_value=FAKE_CONFIG):
        result = RiskFusionLayer().fuse(
            RiskState(harassment=0.4),
            RiskState(harassment=1.0),
            nlp_confidence=0.05,
        )

    assert result.harassment == pytest.approx(0.43)


def test_fuse_high_confidence_at_max_tier():
    with patch("app.core.risk_fusion.KBService.get_fusion_config", return_value=FAKE_CONFIG):
        result = RiskFusionLayer().fuse(
            RiskState(harassment=0.0),
            RiskState(harassment=1.0),
            nlp_confidence=0.95,
        )

    assert result.harassment == pytest.approx(0.60)


def test_fuse_mid_tier_match():
    with patch("app.core.risk_fusion.KBService.get_fusion_config", return_value=FAKE_CONFIG):
        result = RiskFusionLayer().fuse(
            RiskState(coercion=0.5),
            RiskState(coercion=0.5),
            nlp_confidence=0.55,
        )

    assert result.coercion == pytest.approx(0.5)


def test_fuse_no_config_fallback():
    with patch("app.core.risk_fusion.KBService.get_fusion_config", return_value=None):
        result = RiskFusionLayer().fuse(
            RiskState(harassment=1.0),
            RiskState(harassment=1.0),
            nlp_confidence=0.5,
        )

    assert result.harassment == pytest.approx(1.0)


def test_apply_scenario_bonus_caps_at_one():
    result = RiskFusionLayer().apply_scenario_bonus(
        RiskState(harassment=0.8),
        RiskState(harassment=0.4),
    )

    assert result.harassment == pytest.approx(1.0)


def test_apply_scenario_bonus_floors_at_zero():
    """抑制型情境規則可把 delta 降到 0，但不得使其為負。

    2026-08-05 修改：原測試名為 `..._preserves_negative`，斷言結果為 −0.2。
    負值語意上等同「這則訊息降低了關係的風險」，對死亡威脅之類的內容不成立；
    且負值會壓低 composite 中的 spread（活躍維度比例），連帶拉低等級——
    holdout 的 H08 即因此只判到 warning（見 known-issues #21）。
    抑制的設計意圖是「提高容忍度」而非「反向抵銷」，故下限夾在 0。
    """
    result = RiskFusionLayer().apply_scenario_bonus(
        RiskState(harassment=0.1),
        RiskState(harassment=-0.3),
    )

    assert result.harassment == pytest.approx(0.0)


def test_apply_scenario_bonus_still_suppresses_within_range():
    """夾下限不得使抑制失效：抑制幅度小於原分數時仍應正常扣分。"""
    result = RiskFusionLayer().apply_scenario_bonus(
        RiskState(harassment=0.5),
        RiskState(harassment=-0.3),
    )

    assert result.harassment == pytest.approx(0.2)
