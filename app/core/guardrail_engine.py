"""
Guardrail 檢測引擎 - 支援多 provider
- openai_moderation：OpenAI 官方 /moderations endpoint（原行為）
- llm_classifier：用 Llama Guard 風格 prompt 餵任何 OpenAI 相容 endpoint
"""

import asyncio
import os

from dotenv import load_dotenv
from openai import OpenAI

from app.services.kb_service import KBService

load_dotenv()

LLAMA_GUARD_TEMPLATE = """Task: Check if there is unsafe content in the User message according to our safety policy.

<BEGIN UNSAFE CONTENT CATEGORIES>
S1: Violent Crimes (武器、攻擊、傷害他人)
S2: Sexual Content (露骨性內容、性剝削)
S3: Self-Harm (自殺、自殘)
S4: Illegal Drugs (毒品、非法藥物)
S5: Harassment (持續騷擾、辱罵、人身攻擊)
S6: Sexual Exploitation (性勒索、未成年性誘導)
<END UNSAFE CONTENT CATEGORIES>

<BEGIN CONVERSATION>
User: {text}
<END CONVERSATION>

Provide your safety assessment for the User message above.
- First line MUST be exactly 'safe' or 'unsafe'.
- If unsafe, second line MUST list violated category codes comma-separated (e.g., S2,S5)."""


class GuardrailEngine:
    def __init__(self):
        self.HARD_BLOCK_RECORDS = KBService.get_hard_block_records()
        print(f"   [ Guardrail ] Initialized with {len(self.HARD_BLOCK_RECORDS)} keyword records.")

        self._provider = os.getenv("GUARDRAIL_PROVIDER", "openai_moderation").lower()
        self._openai_mod_client = None
        self._classifier_adapter = None
        self._classifier_model = None

        if self._provider == "openai_moderation":
            api_key = os.getenv("OPENAI_API_KEY")
            self._openai_mod_client = OpenAI(api_key=api_key) if api_key else None
        elif self._provider == "llm_classifier":
            try:
                from app.core.llm_adapters import (
                    get_guardrail_classifier_adapter,
                    get_guardrail_classifier_model,
                )

                self._classifier_adapter = get_guardrail_classifier_adapter()
                self._classifier_model = get_guardrail_classifier_model()
            except Exception as e:
                print(f"   [ Guardrail Warning ] LLM classifier init failed: {e}")

    async def check(self, text: str) -> dict:
        flagged_words = []

        # 1. MySQL 禁詞（中文最穩，永遠先跑）
        for record in self.HARD_BLOCK_RECORDS:
            keyword = record.get("keyword", "")
            if keyword and keyword in text:
                mode = record.get("trigger_mode") or "flag"
                flagged_words.append(keyword)
                if mode == "block":
                    return {
                        "is_blocked": True,
                        "reason": f"Hard-Block: {keyword}",
                        "flagged_words": flagged_words,
                        "degraded": False,   # 禁詞比對是本地的，不會降級
                    }

        # 2. 走 provider 對應的第二道
        # 兩條分支內都是同步阻塞的 HTTP 呼叫，本函式卻是 async def——
        # 不丟執行緒的話，await 不會讓出 event loop，併發請求會被序列化。
        # （2026-08-30）
        if self._provider == "llm_classifier" and self._classifier_adapter:
            result = await asyncio.to_thread(self._check_via_classifier, text)
        else:
            # provider 為 llm_classifier 但 adapter 為 None 時也會走到這裡
            # （初始化失敗，見 __init__）。此時 moderation 多半也沒設定，
            # 會由「no client」分支標記 degraded。
            result = await asyncio.to_thread(self._check_via_openai_moderation, text)

        result["flagged_words"] = flagged_words
        result.setdefault("degraded", False)
        return result

    def _check_via_openai_moderation(self, text: str) -> dict:
        if not self._openai_mod_client:
            # 未設定憑證、初始化失敗、或金鑰在本次連線中被 401 停用。
            # 三種情況都代表第二層根本沒跑，必須向呼叫端揭露
            # （integration-review §2 列為 P0：兩層 Guardrail 可能根本沒啟用）。
            return {
                "is_blocked": False,
                "reason": "Degraded (moderation skipped: no client)",
                "classifier_flagged": False,
                "degraded": True,
            }
        try:
            response = self._openai_mod_client.moderations.create(input=text)
            result = response.results[0]
            if result.flagged:
                categories = [k for k, v in result.categories.model_dump().items() if v]
                return {
                    "is_blocked": False,
                    "reason": f"Passed (openai mod flagged: {categories})",
                    "classifier_flagged": True,
                    "classifier_categories": str(categories),
                }
        except Exception as e:
            err_str = str(e)
            if "401" in err_str or "invalid_api_key" in err_str or "Incorrect API key" in err_str:
                print("   [ Guardrail ] OpenAI key invalid, disabling moderation for this session.")
                self._openai_mod_client = None
            else:
                print(f"   [ Guardrail Warning ] OpenAI Mod Error: {e}")
            return {
                "is_blocked": False,
                "reason": f"Degraded (moderation error: {type(e).__name__})",
                "classifier_flagged": False,
                "degraded": True,
            }
        return {
            "is_blocked": False,
            "reason": "Passed (openai_moderation)",
            "classifier_flagged": False,
        }

    def _check_via_classifier(self, text: str) -> dict:
        prompt = LLAMA_GUARD_TEMPLATE.format(text=text)
        try:
            resp = self._classifier_adapter.generate(prompt, model=self._classifier_model)
            lines = [ln.strip() for ln in (resp or "").splitlines() if ln.strip()]
            if not lines:
                # 空回應等同沒檢查，與呼叫失敗同級
                return {
                    "is_blocked": False,
                    "reason": "Degraded (classifier empty response)",
                    "classifier_flagged": False,
                    "degraded": True,
                }
            verdict = lines[0].lower()
            if verdict.startswith("unsafe"):
                categories = lines[1] if len(lines) > 1 else "unknown"
                return {
                    "is_blocked": False,
                    "reason": f"Passed (classifier flagged: {categories})",
                    "classifier_flagged": True,
                    "classifier_categories": categories,
                }
        except Exception as e:
            # 2026-08-15：呼叫失敗時必須與「檢查過、沒問題」區分開來。
            # 原本兩者回傳完全相同的 dict，下游無從分辨——一個連不上的
            # classifier 看起來就像一個判定安全的 classifier。
            print(f"   [ Guardrail Warning ] Classifier Error: {e}")
            return {
                "is_blocked": False,
                "reason": f"Degraded (classifier error: {type(e).__name__})",
                "classifier_flagged": False,
                "degraded": True,
            }
        return {
            "is_blocked": False,
            "reason": "Passed (llm_classifier)",
            "classifier_flagged": False,
        }
