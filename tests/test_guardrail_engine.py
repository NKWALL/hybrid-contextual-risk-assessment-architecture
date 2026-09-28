"""Step 0 Guardrail trigger_mode + flagged_words tests (Phase 3.1)."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest


FAKE_RECORDS = [
    {"keyword": "炸彈", "reason_label": "violence", "trigger_mode": "flag"},
    {"keyword": "殺人", "reason_label": "violence", "trigger_mode": "flag"},
    {"keyword": "殺死你", "reason_label": "violence_threat", "trigger_mode": "block"},
    {"keyword": "傳裸照給我", "reason_label": "sexual_demand", "trigger_mode": "block"},
]


@pytest.fixture
def guardrail(monkeypatch):
    """Build GuardrailEngine with mocked KBService records and disabled Layer 2."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GUARDRAIL_PROVIDER", raising=False)
    monkeypatch.delenv("GUARDRAIL_API_KEY", raising=False)
    monkeypatch.delenv("GUARDRAIL_BASE_URL", raising=False)

    with patch("app.core.guardrail_engine.KBService.get_hard_block_records", return_value=FAKE_RECORDS):
        from app.core.guardrail_engine import GuardrailEngine

        g = GuardrailEngine()
        g._openai_mod_client = None
        yield g


def test_block_mode_keyword_blocks(guardrail):
    """命中 block mode 詞 -> 立即攔截"""
    result = asyncio.run(guardrail.check("我要殺死你"))
    assert result["is_blocked"] is True
    assert "殺死你" in result["reason"]
    assert result["flagged_words"] == ["殺死你"]


def test_block_mode_phrase_blocks(guardrail):
    """命中 block mode 片語 -> 攔截"""
    result = asyncio.run(guardrail.check("傳裸照給我"))
    assert result["is_blocked"] is True
    assert "傳裸照給我" in result["reason"]


def test_flag_mode_keyword_passes_with_flag(guardrail):
    """命中 flag mode 詞 -> 不攔截，但 flagged_words 包含該詞"""
    result = asyncio.run(guardrail.check("新聞說那個殺人案"))
    assert result["is_blocked"] is False
    assert "殺人" in result.get("flagged_words", [])


def test_multiple_flag_words_collected(guardrail):
    """多個 flag 詞同時命中 -> 全部進 flagged_words"""
    result = asyncio.run(guardrail.check("新聞說那個殺人犯被炸彈炸傷"))
    assert result["is_blocked"] is False
    assert "殺人" in result["flagged_words"]
    assert "炸彈" in result["flagged_words"]


def test_block_takes_precedence_over_flag(guardrail):
    """同時含 block 與 flag -> 走 block 路徑"""
    result = asyncio.run(guardrail.check("殺死你 跟那個殺人犯一樣"))
    assert result["is_blocked"] is True
    assert "殺死你" in result["reason"]


def test_no_match_clean_result(guardrail):
    """完全不含禁詞 -> 走 Layer 2，flagged_words 空陣列"""
    result = asyncio.run(guardrail.check("今天天氣真好我們去公園走走"))
    assert result["is_blocked"] is False
    assert result.get("flagged_words", []) == []


def test_block_preserves_prior_flag_words(guardrail):
    """命中 block 之前累積的 flag 詞應該保留在 flagged_words（audit 完整性）"""
    result = asyncio.run(guardrail.check("那個殺人犯說 殺死你"))
    assert result["is_blocked"] is True
    assert "殺死你" in result["flagged_words"]
    assert "殺人" in result["flagged_words"]


def test_null_trigger_mode_defaults_to_flag(monkeypatch):
    """trigger_mode 為 None / 缺欄位時應視為 flag（防 DB 漏填）"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GUARDRAIL_PROVIDER", raising=False)
    monkeypatch.delenv("GUARDRAIL_API_KEY", raising=False)
    monkeypatch.delenv("GUARDRAIL_BASE_URL", raising=False)

    null_records = [
        {"keyword": "測試詞A", "reason_label": "test", "trigger_mode": None},
        {"keyword": "測試詞B", "reason_label": "test"},
    ]
    with patch("app.core.guardrail_engine.KBService.get_hard_block_records", return_value=null_records):
        from app.core.guardrail_engine import GuardrailEngine

        g = GuardrailEngine()
        g._openai_mod_client = None
        result = asyncio.run(g.check("這是測試詞A 跟 測試詞B"))

    assert result["is_blocked"] is False
    assert "測試詞A" in result.get("flagged_words", [])
    assert "測試詞B" in result.get("flagged_words", [])


def test_classifier_unsafe_no_longer_blocks(monkeypatch):
    """Llama Guard 判 unsafe 不再直接 block，只標 classifier_flagged"""
    monkeypatch.setenv("GUARDRAIL_PROVIDER", "llm_classifier")
    monkeypatch.setenv("GUARDRAIL_BASE_URL", "http://test/v1")
    monkeypatch.setenv("GUARDRAIL_API_KEY", "test")
    monkeypatch.setenv("GUARDRAIL_MODEL", "test-model")

    mock_adapter = MagicMock()
    mock_adapter.generate.return_value = "unsafe\nS1"

    with patch("app.core.guardrail_engine.KBService.get_hard_block_records", return_value=[]):
        from app.core.guardrail_engine import GuardrailEngine

        g = GuardrailEngine()
        g._classifier_adapter = mock_adapter
        g._classifier_model = "test"
        result = asyncio.run(g.check("HIIIIIII!!!"))

    assert result["is_blocked"] is False
    assert result["classifier_flagged"] is True
    assert "S1" in result["classifier_categories"]


def test_classifier_safe_no_flag(monkeypatch):
    """Llama Guard 判 safe -> classifier_flagged=False"""
    monkeypatch.setenv("GUARDRAIL_PROVIDER", "llm_classifier")
    monkeypatch.setenv("GUARDRAIL_BASE_URL", "http://test/v1")
    monkeypatch.setenv("GUARDRAIL_API_KEY", "test")
    monkeypatch.setenv("GUARDRAIL_MODEL", "test-model")

    mock_adapter = MagicMock()
    mock_adapter.generate.return_value = "safe"

    with patch("app.core.guardrail_engine.KBService.get_hard_block_records", return_value=[]):
        from app.core.guardrail_engine import GuardrailEngine

        g = GuardrailEngine()
        g._classifier_adapter = mock_adapter
        g._classifier_model = "test"
        result = asyncio.run(g.check("普通閒聊"))

    assert result["is_blocked"] is False
    assert result["classifier_flagged"] is False


def test_mysql_block_still_blocks_even_if_classifier_safe(guardrail):
    """MySQL block mode 仍立即 blocked，Llama Guard 不會被諮詢"""
    result = asyncio.run(guardrail.check("我要殺死你"))
    assert result["is_blocked"] is True
    assert "殺死你" in result["reason"]
    assert "classifier_flagged" not in result or result.get("classifier_flagged") is None


# --- Guardrail 降級揭露（2026-08-15，整合端事故後補）---
#
# 事故背景：classifier endpoint 連不上時，_check_via_classifier 只印一行 warning，
# 回傳的 dict 與「檢查過、沒問題」完全相同。下游無從分辨——一個連不上的
# classifier 看起來就像一個判定安全的 classifier。
# 這與 nlp_degraded（known-issues #17）是同一類缺陷。

def _clean_engine(monkeypatch, provider="llm_classifier"):
    monkeypatch.setenv("GUARDRAIL_PROVIDER", provider)
    monkeypatch.setenv("GUARDRAIL_BASE_URL", "http://test/v1")
    monkeypatch.setenv("GUARDRAIL_API_KEY", "test")
    monkeypatch.setenv("GUARDRAIL_MODEL", "test-model")
    with patch("app.core.guardrail_engine.KBService.get_hard_block_records", return_value=[]):
        from app.core.guardrail_engine import GuardrailEngine
        return GuardrailEngine()


def test_classifier_error_marks_degraded(monkeypatch):
    """呼叫失敗必須標記 degraded，且不得偽裝成 blocked。"""
    g = _clean_engine(monkeypatch)
    mock_adapter = MagicMock()
    mock_adapter.generate.side_effect = ConnectionError("endpoint unreachable")
    g._classifier_adapter = mock_adapter
    g._classifier_model = "test"

    result = asyncio.run(g.check("普通閒聊"))

    assert result["degraded"] is True
    assert result["classifier_flagged"] is False
    # 失敗不得升級為攔截——guardrail 是 flag-only 層
    assert result["is_blocked"] is False


def test_classifier_success_not_degraded(monkeypatch):
    """正常判 safe 時 degraded 必須為 False，否則這個旗標沒有鑑別力。"""
    g = _clean_engine(monkeypatch)
    mock_adapter = MagicMock()
    mock_adapter.generate.return_value = "safe"
    g._classifier_adapter = mock_adapter
    g._classifier_model = "test"

    result = asyncio.run(g.check("普通閒聊"))

    assert result["degraded"] is False
    assert result["classifier_flagged"] is False


def test_classifier_empty_response_marks_degraded(monkeypatch):
    """空回應等同沒檢查。"""
    g = _clean_engine(monkeypatch)
    mock_adapter = MagicMock()
    mock_adapter.generate.return_value = ""
    g._classifier_adapter = mock_adapter
    g._classifier_model = "test"

    assert asyncio.run(g.check("普通閒聊"))["degraded"] is True


def test_moderation_no_client_marks_degraded(monkeypatch):
    """未設定憑證時第二層根本沒跑（integration-review §2 的 P0）。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    g = _clean_engine(monkeypatch, provider="openai_moderation")
    g._openai_mod_client = None

    assert asyncio.run(g.check("普通閒聊"))["degraded"] is True


def test_hard_block_not_degraded(monkeypatch):
    """禁詞比對是本地的，攔截路徑不得被標成降級。"""
    monkeypatch.setenv("GUARDRAIL_PROVIDER", "llm_classifier")
    monkeypatch.setenv("GUARDRAIL_BASE_URL", "http://test/v1")
    monkeypatch.setenv("GUARDRAIL_API_KEY", "test")
    monkeypatch.setenv("GUARDRAIL_MODEL", "test-model")
    records = [{"keyword": "傳裸照給我", "trigger_mode": "block"}]

    with patch("app.core.guardrail_engine.KBService.get_hard_block_records", return_value=records):
        from app.core.guardrail_engine import GuardrailEngine
        g = GuardrailEngine()
        result = asyncio.run(g.check("傳裸照給我"))

    assert result["is_blocked"] is True
    assert result["degraded"] is False
