"""ScenarioRiskLayer total_messages_min support test."""

from unittest.mock import patch

import pytest

from app.core.scenario_risk_layer import ScenarioRiskLayer
from app.models.schemas import RiskState, TemporalFeatures


def _fake_rule():
    return {
        "rule_name": "one_way_persistence_harassment",
        "condition_logic": {
            "imbalance_min": 0.6,
            "intimacy_max": 0.3,
            "total_messages_min": 10,
        },
        "bonus_actions": {"harassment": 0.3, "emotional_pressure": 0.2},
    }


def test_total_messages_min_skips_when_below_threshold():
    """early conversation (total=1) should not trigger."""
    with patch(
        "app.core.scenario_risk_layer.KBService.get_scenario_rules",
        return_value=[_fake_rule()],
    ):
        layer = ScenarioRiskLayer()
        bonus, triggered = layer.evaluate(
            rule_result={"delta": RiskState()},
            nlp_result={"delta": RiskState()},
            temporal_features=TemporalFeatures(),
            memory_metrics={"conversation_balance": 0.0, "total_messages": 1},
            last_summary=None,
        )

    assert "one_way_persistence_harassment" not in triggered
    assert bonus.harassment == 0.0


def test_total_messages_min_fires_when_above_threshold():
    """accumulated >= 10 messages and matching conditions should trigger."""
    with patch(
        "app.core.scenario_risk_layer.KBService.get_scenario_rules",
        return_value=[_fake_rule()],
    ):
        layer = ScenarioRiskLayer()
        bonus, triggered = layer.evaluate(
            rule_result={"delta": RiskState()},
            nlp_result={"delta": RiskState()},
            temporal_features=TemporalFeatures(),
            memory_metrics={"conversation_balance": 0.0, "total_messages": 20},
            last_summary=None,
        )

    assert "one_way_persistence_harassment" in triggered
    assert bonus.harassment == pytest.approx(0.3)
