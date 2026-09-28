"""Background context judge for Step 0 guardrail flags.

This service never blocks the current request. It records a lightweight
healthy/concerning/unclear judgment that the next state update can use for
feedback recalibration.
"""

import asyncio
import json
import os
import re
from typing import List, Optional

from dotenv import load_dotenv

from app.models.schemas import Message
from app.services.chat_log_service import ChatLogService

load_dotenv()


JUDGE_PROMPT_TEMPLATE = """你是交友安全系統的「背景情境判斷器」。
你的工作只有一件事:判斷 Step 0 已抓到的詞彙在當前對話中是否真的有害。
你不負責封鎖訊息、不負責重算分數、不負責給建議。

[輸出格式 - 強制規則,違反任何一項即視為錯誤]
1. 只輸出一個 JSON 物件,不能有任何其他文字。
2. 禁止 markdown、禁止 ```json``` 包覆、禁止加說明段落、禁止加開場白。
3. JSON 必須剛好只有兩個鍵: "judgment" 和 "reasoning"。
4. 禁止使用其他鍵名。以下都是錯的:sentiment、category、label、score、tag、emotion、result、type。
5. judgment 只能是這三個英文字串之一: "healthy"、"concerning"、"unclear"。大小寫必須完全一致。
6. reasoning 用繁體中文,50 字以內的客觀理由,不要重複規則本身。

[判斷標準]
- healthy: 新聞討論、防治宣導、求助、教育性提醒、自我揭露情緒、非針對對話對象的中性陳述。
- concerning: 威脅、性要求、勒索、騷擾、鼓勵對方自傷、教唆犯罪、要求或交換裸照、針對對方的人身安全壓迫。
- unclear: 上下文不足,或健康與危險線索同時存在且無法判定主要意圖。

[範例 1 - healthy]
Step 0 flag:
flagged_words: ["殺人"]
classifier_flag: {{"flagged": false}}
最近已送達對話:
Alice: 你最近有看新聞嗎
目前訊息:
Bob: 那個殺人案的犯人抓到了
輸出:
{{"judgment": "healthy", "reasoning": "純新聞事件討論,非針對對話對象"}}

[範例 2 - concerning]
Step 0 flag:
flagged_words: []
classifier_flag: {{"flagged": true, "categories": "S2"}}
最近已送達對話:
Alice: 我今天不太想聊
目前訊息:
Bob: 你再不傳裸照我就把你資料公開
輸出:
{{"judgment": "concerning", "reasoning": "勒索式威脅並要求不當內容,直接針對對方"}}

[範例 3 - healthy(自我揭露,非鼓勵自傷)]
Step 0 flag:
flagged_words: ["想死"]
classifier_flag: {{"flagged": false}}
最近已送達對話:
Alice: 工作壓力好大
目前訊息:
Bob: 我有時候也覺得想死,但找朋友聊聊就好多了
輸出:
{{"judgment": "healthy", "reasoning": "自我揭露情緒並提及健康因應方式,非鼓勵對方自傷"}}

[範例 4 - unclear]
Step 0 flag:
flagged_words: ["照片"]
classifier_flag: {{"flagged": false}}
最近已送達對話:
Alice: 嗨
目前訊息:
Bob: 傳照片
輸出:
{{"judgment": "unclear", "reasoning": "未指明照片類型且上下文過短,意圖不明確"}}

[現在請判斷]
Step 0 flag:
flagged_words: {flagged_words}
classifier_flag: {classifier_flag}
最近已送達對話:
{history}
目前訊息:
{sender_id}: {current_message}

只輸出 JSON,不要任何前綴、後綴、markdown、解釋。第一個字元必須是 {{ ,最後一個字元必須是 }}:
"""


class BackgroundJudgeService:
    def __init__(self, chat_log_service: Optional[ChatLogService] = None, adapter=None, model: Optional[str] = None):
        self.chat_log_service = chat_log_service or ChatLogService()
        self._adapter = adapter
        self._model = model

    def is_enabled(self) -> bool:
        return os.getenv("BACKGROUND_JUDGE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}

    def should_review(self, flagged_words=None, classifier_flag=None) -> bool:
        classifier_flag = classifier_flag or {}
        return bool(flagged_words) or bool(classifier_flag.get("flagged"))

    async def review_guardrail_context(
        self,
        conversation_id: str,
        sender_id: str,
        msg_id: str,
        current_message: str,
        recent_messages: List[Message],
        flagged_words=None,
        classifier_flag=None,
    ) -> None:
        """Run background judgment and persist the result.

        This method is designed for FastAPI BackgroundTasks. All errors are
        swallowed after logging so the main request flow stays isolated.
        """
        flagged_words = flagged_words or []
        classifier_flag = classifier_flag or {}

        if not self.is_enabled() or not self.should_review(flagged_words, classifier_flag):
            return

        model = self._resolve_model()
        try:
            adapter = self._get_adapter()
            prompt = self._build_prompt(sender_id, current_message, recent_messages, flagged_words, classifier_flag)
            # 丟到執行緒（2026-08-30）：adapter.generate 是同步阻塞的 HTTP 呼叫。
            # 本函式是 async def，Starlette 對 async 背景任務走 `await self.func()`，
            # 直接在 event loop 上執行——阻塞期間整個服務停擺，下一則 /detect
            # 連第一行都跑不到。實測本地 gemma4:e2b 需 6.3–11.7 秒。
            response_text = await asyncio.to_thread(adapter.generate, prompt, model=model)
            parsed = self._parse_response(response_text)
        except Exception as e:
            print(f"   [ Background Judge Warning ] {e}")
            parsed = {"judgment": "unclear", "reasoning": f"judge failed: {e}"[:1000]}

        await self.chat_log_service.save_guardrail_context_review(
            conversation_id=conversation_id,
            sender_id=sender_id,
            msg_id=msg_id,
            flagged_words=flagged_words,
            classifier_flag=classifier_flag,
            judgment=parsed["judgment"],
            reasoning=parsed["reasoning"],
            model=model,
        )
        print(f"   [ Background Judge ] {parsed['judgment']} for msg {msg_id}")

    def _get_adapter(self):
        if self._adapter:
            return self._adapter

        from app.core.llm_adapters import OpenAICompatAdapter

        base_url = os.getenv("BACKGROUND_JUDGE_BASE_URL", "http://localhost:11434/v1")
        api_key = os.getenv("BACKGROUND_JUDGE_API_KEY", "ollama")
        self._adapter = OpenAICompatAdapter(base_url=base_url, api_key=api_key)
        return self._adapter

    def _resolve_model(self) -> str:
        if self._model:
            return self._model
        return os.getenv("BACKGROUND_JUDGE_MODEL", "qwen2.5:3b")

    def _build_prompt(self, sender_id: str, current_message: str, recent_messages: List[Message], flagged_words, classifier_flag) -> str:
        history_lines = [
            f"{m.sender}: {m.content}"
            for m in recent_messages[-10:]
        ]
        history = "\n".join(history_lines) if history_lines else "No previous delivered messages"
        return JUDGE_PROMPT_TEMPLATE.format(
            flagged_words=json.dumps(flagged_words, ensure_ascii=False),
            classifier_flag=json.dumps(classifier_flag, ensure_ascii=False),
            history=history,
            sender_id=sender_id,
            current_message=current_message,
        )

    def _parse_response(self, text: str) -> dict:
        try:
            match = re.search(r"(\{.*\})", text or "", re.DOTALL)
            payload = match.group(1) if match else (text or "").strip()
            data = json.loads(payload)
            judgment = data.get("judgment", "unclear")
            if judgment not in {"healthy", "concerning", "unclear"}:
                judgment = "unclear"
            reasoning = str(data.get("reasoning", "No reasoning provided"))[:1000]
            return {"judgment": judgment, "reasoning": reasoning}
        except Exception:
            return {"judgment": "unclear", "reasoning": "Malformed background judge response"}
