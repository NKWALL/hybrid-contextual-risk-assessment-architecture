"""
LLM Adapter Layer - 統一不同 provider 的 chat completion / generate 介面

兩種 adapter：
- GeminiAdapter：包 google.generativeai SDK
- OpenAICompatAdapter：包 openai SDK，可指向任何 OpenAI 相容 endpoint
  (Groq / OpenRouter / Together / HuggingFace Inference / 自建 vLLM / Ollama)

工廠 function：
- get_nlp_adapter() 供 NLPEngine.analyze 用
- get_summary_adapter() 供 generate_rolling_summary 用
- get_guardrail_classifier_adapter() 供 GuardrailEngine 的 llm_classifier 模式用
"""

import os
import random
import time
from typing import Callable, Optional, Protocol


class LLMAdapter(Protocol):
    def generate(self, prompt: str, model: str) -> str: ...


# ---------------------------------------------------------------------------
# 重試與逾時（2026-08-05 新增，known-issues #2）
# ---------------------------------------------------------------------------
# 為何需要：呼叫失敗時 `nlp_engine._fallback_result()` 回傳全 0 的 delta，
# 對一則不具行為異常的訊息即等同判 `safe`（known-issues #17）。
# 也就是說**網路抖動會直接變成安全破口**——這不是理論風險：
# 2026-08-05 驗證 temperature 修正時連續發送請求，即因速率限制觸發此路徑。
#
# ⚠️ 重試只對「呼叫失敗」有效，對「答案不好」無效。
#    temperature 已固定為 0，重試拿到的是同一個答案。
#    這是正確的行為，但別誤以為重試能改善判斷品質。
#
# 只重試暫時性錯誤：金鑰錯誤、參數錯誤等永久性失敗應立刻放棄，
# 否則只是把一次失敗變成三次失敗、延遲三倍。

LLM_MAX_ATTEMPTS = int(os.getenv("LLM_MAX_ATTEMPTS", "3"))
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
LLM_BACKOFF_BASE = float(os.getenv("LLM_BACKOFF_BASE", "1.0"))

# Guardrail classifier 使用獨立且更緊的預算（2026-08-15 新增）。
#
# 上面那組預設值是為 NLP 主判斷路徑設計的：NLP 分數是風險判斷的支柱，
# 失敗會使語意通道歸零，值得用時間換成功率。
# 但 guardrail classifier 是「選配」的——它只在 GUARDRAIL_PROVIDER=llm_classifier
# 時啟用，且失敗時規則引擎與 NLP 兩條路徑仍會產生判斷。
#
# 沿用同一組預算的後果（整合端 2026-08-09 實測）：classifier endpoint 連不上時，
# 光退避就吃掉 1.0 + 2.0 = 3.0s（含 jitter 約 3.6s），加上三次連線嘗試本身，
# 使 /detect 從中位數約 3s 拉長到約 9s——一個選配元件的故障拖垮了整條同步路徑。
#
# 因此改為「失敗就快速放棄」：不重試、短逾時。
GUARDRAIL_MAX_ATTEMPTS = int(os.getenv("GUARDRAIL_MAX_ATTEMPTS", "1"))
GUARDRAIL_TIMEOUT_SECONDS = float(os.getenv("GUARDRAIL_TIMEOUT_SECONDS", "3"))

# 依例外類別名稱判斷是否暫時性。跨 SDK 通用，不必逐一 import 各家的例外型別
# （google.api_core、openai、httpx 的類別名稱皆涵蓋於下）。
_TRANSIENT_NAME_HINTS = (
    "ratelimit", "resourceexhausted", "toomanyrequests",
    "serviceunavailable", "unavailable",
    "timeout", "deadlineexceeded",
    "internalservererror", "internalerror", "apiconnection",
    "connectionerror", "remoteprotocol",
)
_TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    if any(h in name for h in _TRANSIENT_NAME_HINTS):
        return True
    for attr in ("status_code", "code", "http_status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int) and val in _TRANSIENT_STATUS:
            return True
    return False


def call_with_retry(fn: Callable[[], str], label: str = "LLM",
                    max_attempts: Optional[int] = None) -> str:
    """執行 fn，對暫時性錯誤做指數退避重試。

    退避加入隨機抖動（jitter），避免多個併發請求在同一時刻一起重試而再次撞上限額。

    max_attempts 未指定時沿用 LLM_MAX_ATTEMPTS。選配元件（如 guardrail classifier）
    應傳入較小值，避免其故障延長整條同步路徑。
    """
    attempts = LLM_MAX_ATTEMPTS if max_attempts is None else max(1, max_attempts)
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — 需依內容分類，故先全捕捉
            last_exc = e
            if not _is_transient(e):
                print(f"   [ {label} ] 永久性錯誤，不重試：{type(e).__name__}: {e}")
                raise
            if attempt >= attempts:
                print(f"   [ {label} ] 重試 {attempts} 次仍失敗：{type(e).__name__}")
                raise
            wait = LLM_BACKOFF_BASE * (2 ** (attempt - 1)) * (1 + random.random() * 0.25)
            print(f"   [ {label} ] 暫時性錯誤（{type(e).__name__}），"
                  f"{wait:.1f}s 後重試（第 {attempt}/{attempts} 次）")
            time.sleep(wait)
    raise last_exc  # pragma: no cover — 迴圈內必定 return 或 raise


class GeminiAdapter:
    """Google Generative AI 包裝"""

    def __init__(self):
        import google.generativeai as genai

        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        self._genai = genai

    def generate(self, prompt: str, model: str) -> str:
        # temperature=0（2026-08-05 新增）：原本未指定，沿用模型預設值。
        # 實測同一則案例連跑三次，sexual_boundary 出現 0.75／0.75／0.65 的擺盪；
        # 整個 test 集重跑一次，22 則中有 15 則（68%）NLP 分數改變，
        # 3 則（14%）連最終等級都翻掉——大於我們想量測的多數效果量，
        # 使「修正前 vs 修正後」的比較無法解讀（見 known-issues #23）。
        # 設為 0 後三次結果完全相同。
        # 這同時也是風險系統該有的性質：同樣的訊息與脈絡，應得到同樣的判斷。
        m = self._genai.GenerativeModel(model, generation_config={"temperature": 0.0})

        def _once() -> str:
            # 顯式逾時：未設定時 SDK 可能長時間等待，讓整條 pipeline 卡住
            response = m.generate_content(
                prompt, request_options={"timeout": LLM_TIMEOUT_SECONDS})
            return response.text

        return call_with_retry(_once, label="Gemini")


class OpenAICompatAdapter:
    """OpenAI 相容 chat completion 包裝（適用 Groq / OpenRouter / Together / 等等）"""

    def __init__(self, base_url: str, api_key: str,
                 timeout: Optional[float] = None,
                 max_attempts: Optional[int] = None):
        from openai import OpenAI

        if not base_url:
            raise ValueError("OpenAICompatAdapter requires base_url")
        if not api_key:
            raise ValueError("OpenAICompatAdapter requires api_key")
        # timeout／max_attempts 未指定時沿用全域預設；選配元件可傳入較緊的預算。
        self._max_attempts = max_attempts
        # max_retries=0：關閉 SDK 內建重試，改由 call_with_retry 統一處理，
        # 以免兩層重試相乘（3 × 2 = 6 次）使失敗情境的延遲不可預期。
        self._client = OpenAI(
            base_url=base_url, api_key=api_key,
            timeout=LLM_TIMEOUT_SECONDS if timeout is None else timeout,
            max_retries=0)

    def generate(self, prompt: str, model: str) -> str:
        def _once() -> str:
            resp = self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                # 與 GeminiAdapter 一致設為 0（2026-08-05）：判斷的可重現性是本系統的設計性質，
                # 不因走哪條 provider 路徑而異。原值 0.1 使 provider 切換會重新引入不確定性。
                # 已實測 llama-guard3:1b 與 gemma4:e2b 在 0.0 下三次輸出完全相同，未見小模型退化。
                temperature=0.0,
            )
            return resp.choices[0].message.content or ""

        return call_with_retry(_once, label="OpenAICompat",
                               max_attempts=self._max_attempts)


def _make_openai_compat(base_url: Optional[str], api_key: Optional[str],
                        timeout: Optional[float] = None,
                        max_attempts: Optional[int] = None) -> OpenAICompatAdapter:
    if not base_url or not api_key:
        raise RuntimeError("openai_compat provider 需要 base_url 與 api_key 環境變數")
    return OpenAICompatAdapter(base_url=base_url, api_key=api_key,
                               timeout=timeout, max_attempts=max_attempts)


def get_nlp_adapter() -> LLMAdapter:
    provider = os.getenv("NLP_PROVIDER", "gemini").lower()
    if provider == "openai_compat":
        return _make_openai_compat(
            os.getenv("NLP_OPENAI_BASE_URL"),
            os.getenv("NLP_OPENAI_API_KEY"),
        )
    return GeminiAdapter()


def get_nlp_model_name(kb_model_hint: Optional[str]) -> str:
    """Resolve NLP model name from provider-specific source."""

    provider = os.getenv("NLP_PROVIDER", "gemini").lower()
    if provider == "openai_compat":
        model = os.getenv("NLP_MODEL")
        if not model:
            raise RuntimeError("openai_compat 需要 NLP_MODEL 環境變數")
        return model
    return kb_model_hint or "gemini-2.5-flash"


def get_summary_adapter() -> LLMAdapter:
    provider = (os.getenv("SUMMARY_PROVIDER") or os.getenv("NLP_PROVIDER", "gemini")).lower()
    if provider == "openai_compat":
        return _make_openai_compat(
            os.getenv("SUMMARY_OPENAI_BASE_URL") or os.getenv("NLP_OPENAI_BASE_URL"),
            os.getenv("SUMMARY_OPENAI_API_KEY") or os.getenv("NLP_OPENAI_API_KEY"),
        )
    return GeminiAdapter()


def get_summary_model_name(kb_model_hint: Optional[str]) -> str:
    provider = (os.getenv("SUMMARY_PROVIDER") or os.getenv("NLP_PROVIDER", "gemini")).lower()
    if provider == "openai_compat":
        model = os.getenv("SUMMARY_MODEL") or os.getenv("NLP_MODEL")
        if not model:
            raise RuntimeError("openai_compat summary 需要 SUMMARY_MODEL 或 NLP_MODEL 環境變數")
        return model
    return kb_model_hint or "gemini-2.5-flash"


def get_guardrail_classifier_adapter() -> LLMAdapter:
    """只在 GUARDRAIL_PROVIDER=llm_classifier 時被呼叫

    使用 GUARDRAIL_* 的獨立預算（預設 1 次嘗試、3 秒逾時），不沿用 NLP 的
    3 次／30 秒。理由見檔案上方 GUARDRAIL_MAX_ATTEMPTS 的註解。
    """

    return _make_openai_compat(
        os.getenv("GUARDRAIL_BASE_URL"),
        os.getenv("GUARDRAIL_API_KEY"),
        timeout=GUARDRAIL_TIMEOUT_SECONDS,
        max_attempts=GUARDRAIL_MAX_ATTEMPTS,
    )


def get_guardrail_classifier_model() -> str:
    model = os.getenv("GUARDRAIL_MODEL")
    if not model:
        raise RuntimeError("llm_classifier 需要 GUARDRAIL_MODEL 環境變數")
    return model
