"""NLP Engine noise filter test (pure parser tests, no network)."""

import json

import pytest


def _make_response(scores: dict, confidence: float = 0.9) -> str:
    """Build a fake LLM JSON response with given risk_scores."""
    payload = {
        "analysis_steps": {
            "behavioral_signals": "test",
            "semantic_risks": "test",
            "contextual_judgment": "test",
        },
        "risk_scores": {
            dim: {"score": s, "confidence": 0.9, "evidence": "test"}
            for dim, s in scores.items()
        },
        "overall_assessment": {
            "average_confidence": confidence,
            "primary_concerns": [],
            "uncertainty_factors": [],
            "recommended_action": "observe",
        },
        "reasoning": "test reasoning",
    }
    return json.dumps(payload)


def test_noise_filter_zeroes_low_scores():
    """All dimensions below 0.10 should be zeroed."""
    from app.core.nlp_engine import NLPEngine

    engine = NLPEngine()
    text = _make_response({
        "sexual_boundary": 0.02,
        "coercion": 0.0,
        "manipulation": 0.05,
        "harassment": 0.03,
        "emotional_pressure": 0.09,
    })
    result = engine._parse_response(text)
    delta = result["delta"]

    assert delta.sexual_boundary == 0.0
    assert delta.coercion == 0.0
    assert delta.manipulation == 0.0
    assert delta.harassment == 0.0
    assert delta.emotional_pressure == 0.0


def test_noise_filter_keeps_high_scores():
    """>= 0.10 dimensions should keep their original values."""
    from app.core.nlp_engine import NLPEngine

    engine = NLPEngine()
    text = _make_response({
        "sexual_boundary": 0.0,
        "coercion": 0.0,
        "manipulation": 0.5,
        "harassment": 0.25,
        "emotional_pressure": 0.0,
    })
    result = engine._parse_response(text)
    delta = result["delta"]

    assert delta.manipulation == pytest.approx(0.5)
    assert delta.harassment == pytest.approx(0.25)
    assert delta.sexual_boundary == 0.0


def test_noise_filter_threshold_boundary():
    """Exactly 0.10 should be kept; 0.099 should be zeroed."""
    from app.core.nlp_engine import NLPEngine

    engine = NLPEngine()

    text_at = _make_response({
        "sexual_boundary": 0.10,
        "coercion": 0.0,
        "manipulation": 0.0,
        "harassment": 0.0,
        "emotional_pressure": 0.0,
    })
    result = engine._parse_response(text_at)
    assert result["delta"].sexual_boundary == pytest.approx(0.10)

    text_below = _make_response({
        "sexual_boundary": 0.099,
        "coercion": 0.0,
        "manipulation": 0.0,
        "harassment": 0.0,
        "emotional_pressure": 0.0,
    })
    result = engine._parse_response(text_below)
    assert result["delta"].sexual_boundary == 0.0


def test_noise_filter_mixed_scores():
    """Mixed high and low scores should keep signals and zero noise."""
    from app.core.nlp_engine import NLPEngine

    engine = NLPEngine()
    text = _make_response({
        "sexual_boundary": 0.03,
        "coercion": 0.0,
        "manipulation": 0.4,
        "harassment": 0.08,
        "emotional_pressure": 0.15,
    })
    result = engine._parse_response(text)
    delta = result["delta"]

    assert delta.sexual_boundary == 0.0
    assert delta.manipulation == pytest.approx(0.4)
    assert delta.harassment == 0.0
    assert delta.emotional_pressure == pytest.approx(0.15)
