"""
LLM Adapter factory 測試，只驗證 provider 選擇邏輯。
不打網路、不打真實 LLM。
"""

import pytest


def test_default_provider_returns_gemini(monkeypatch):
    monkeypatch.delenv("NLP_PROVIDER", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "fake")
    from app.core.llm_adapters import GeminiAdapter, get_nlp_adapter

    adapter = get_nlp_adapter()

    assert isinstance(adapter, GeminiAdapter)


def test_openai_compat_provider_returns_compat(monkeypatch):
    monkeypatch.setenv("NLP_PROVIDER", "openai_compat")
    monkeypatch.setenv("NLP_OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("NLP_OPENAI_API_KEY", "fake-key")
    from app.core.llm_adapters import OpenAICompatAdapter, get_nlp_adapter

    adapter = get_nlp_adapter()

    assert isinstance(adapter, OpenAICompatAdapter)


def test_openai_compat_missing_credentials_raises(monkeypatch):
    monkeypatch.setenv("NLP_PROVIDER", "openai_compat")
    monkeypatch.delenv("NLP_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("NLP_OPENAI_API_KEY", raising=False)
    from app.core.llm_adapters import get_nlp_adapter

    with pytest.raises(RuntimeError):
        get_nlp_adapter()


def test_summary_provider_falls_back_to_nlp(monkeypatch):
    monkeypatch.setenv("NLP_PROVIDER", "openai_compat")
    monkeypatch.setenv("NLP_OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("NLP_OPENAI_API_KEY", "fake-key")
    monkeypatch.delenv("SUMMARY_PROVIDER", raising=False)
    from app.core.llm_adapters import OpenAICompatAdapter, get_summary_adapter

    adapter = get_summary_adapter()

    assert isinstance(adapter, OpenAICompatAdapter)


def test_summary_provider_can_override_to_gemini(monkeypatch):
    monkeypatch.setenv("NLP_PROVIDER", "openai_compat")
    monkeypatch.setenv("NLP_OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("NLP_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("SUMMARY_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake")
    from app.core.llm_adapters import GeminiAdapter, get_summary_adapter

    adapter = get_summary_adapter()

    assert isinstance(adapter, GeminiAdapter)


def test_nlp_model_name_openai_compat_requires_env(monkeypatch):
    monkeypatch.setenv("NLP_PROVIDER", "openai_compat")
    monkeypatch.delenv("NLP_MODEL", raising=False)
    from app.core.llm_adapters import get_nlp_model_name

    with pytest.raises(RuntimeError):
        get_nlp_model_name("gemini-2.5-flash")


def test_nlp_model_name_openai_compat_uses_env(monkeypatch):
    monkeypatch.setenv("NLP_PROVIDER", "openai_compat")
    monkeypatch.setenv("NLP_MODEL", "openai/gpt-oss-20b")
    from app.core.llm_adapters import get_nlp_model_name

    assert get_nlp_model_name("gemini-2.5-flash") == "openai/gpt-oss-20b"


def test_nlp_model_name_gemini_uses_kb_hint(monkeypatch):
    monkeypatch.delenv("NLP_PROVIDER", raising=False)
    from app.core.llm_adapters import get_nlp_model_name

    assert get_nlp_model_name("gemini-2.5-flash") == "gemini-2.5-flash"


def test_guardrail_classifier_model_required(monkeypatch):
    monkeypatch.delenv("GUARDRAIL_MODEL", raising=False)
    from app.core.llm_adapters import get_guardrail_classifier_model

    with pytest.raises(RuntimeError):
        get_guardrail_classifier_model()


# --- 重試與逾時（known-issues #2）---

def _transient(name):
    """造一個「名稱看起來像暫時性錯誤」的例外類別。"""
    return type(name, (Exception,), {})


def test_retry_gives_up_immediately_on_permanent_error():
    """金鑰／參數錯誤屬永久性，重試只會把一次失敗變三次、延遲三倍。"""
    from app.core import llm_adapters as la

    calls = []

    def fn():
        calls.append(1)
        raise ValueError("invalid api key")

    with pytest.raises(ValueError):
        la.call_with_retry(fn)
    assert len(calls) == 1


def test_retry_retries_transient_then_succeeds(monkeypatch):
    from app.core import llm_adapters as la
    monkeypatch.setattr(la, "LLM_BACKOFF_BASE", 0.0)   # 測試不實際等待

    RateLimitError = _transient("RateLimitError")
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise RateLimitError("429")
        return "ok"

    assert la.call_with_retry(fn) == "ok"
    assert len(calls) == 3


def test_retry_raises_after_max_attempts(monkeypatch):
    from app.core import llm_adapters as la
    monkeypatch.setattr(la, "LLM_BACKOFF_BASE", 0.0)
    monkeypatch.setattr(la, "LLM_MAX_ATTEMPTS", 3)

    ServiceUnavailable = _transient("ServiceUnavailable")
    calls = []

    def fn():
        calls.append(1)
        raise ServiceUnavailable("503")

    with pytest.raises(ServiceUnavailable):
        la.call_with_retry(fn)
    assert len(calls) == 3


def test_retry_honours_per_call_max_attempts(monkeypatch):
    """呼叫端可傳入較小的重試預算，不受全域 LLM_MAX_ATTEMPTS 影響。"""
    from app.core import llm_adapters as la
    monkeypatch.setattr(la, "LLM_BACKOFF_BASE", 0.0)
    monkeypatch.setattr(la, "LLM_MAX_ATTEMPTS", 3)

    ServiceUnavailable = _transient("ServiceUnavailable")
    calls = []

    def fn():
        calls.append(1)
        raise ServiceUnavailable("503")

    with pytest.raises(ServiceUnavailable):
        la.call_with_retry(fn, max_attempts=1)
    assert len(calls) == 1


def test_guardrail_classifier_uses_tight_budget(monkeypatch):
    """Guardrail classifier 是選配元件，故障時必須快速放棄。

    2026-08-09 整合端事故：它沿用 NLP 的 3 次／30 秒預算，endpoint 連不上時
    光退避就多花約 3.6 秒，把 /detect 從約 3 秒拉長到約 9 秒，
    超過上游的等待上限。此測試防止該預算被改回全域值。
    """
    from app.core import llm_adapters as la

    monkeypatch.setenv("GUARDRAIL_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("GUARDRAIL_API_KEY", "dummy")

    adapter = la.get_guardrail_classifier_adapter()
    assert adapter._max_attempts == 1
    assert adapter._client.timeout == la.GUARDRAIL_TIMEOUT_SECONDS
    assert la.GUARDRAIL_TIMEOUT_SECONDS < la.LLM_TIMEOUT_SECONDS


def test_transient_detected_by_status_code():
    """類別名稱看不出來時，改以 status_code 判斷。"""
    from app.core import llm_adapters as la

    class WeirdError(Exception):
        status_code = 503

    class AuthError(Exception):
        status_code = 401

    assert la._is_transient(WeirdError()) is True
    assert la._is_transient(AuthError()) is False


def test_transient_covers_common_sdk_exception_names():
    """涵蓋 google.api_core 與 openai 兩家 SDK 的常見暫時性例外名稱。"""
    from app.core import llm_adapters as la

    for name in ("RateLimitError", "ResourceExhausted", "ServiceUnavailable",
                 "APITimeoutError", "DeadlineExceeded", "InternalServerError",
                 "APIConnectionError"):
        assert la._is_transient(_transient(name)()) is True, name
    for name in ("PermissionDenied", "InvalidArgument", "NotFound"):
        assert la._is_transient(_transient(name)()) is False, name
