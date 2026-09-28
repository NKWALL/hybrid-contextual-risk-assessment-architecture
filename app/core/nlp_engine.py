"""
NLP 檢測引擎 - 支援多 provider adapter
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

from dotenv import load_dotenv

from app.models.schemas import Message, RiskState, TemporalFeatures

load_dotenv()


class NLPEngine:
    def __init__(self):
        from app.core.llm_adapters import get_nlp_adapter

        try:
            self._adapter = get_nlp_adapter()
            self._adapter_error = None
        except Exception as e:
            self._adapter = None
            self._adapter_error = str(e)

    def _build_feature_reference(self) -> str:
        """把 kb_features 整理成給 LLM 的參考清單（依維度分組、依 delta 給高/中/低）。"""
        try:
            features = KBService.get_features()
        except Exception as e:
            print(f"   [ NLP feature_ref error ] {e}")
            return "（無可用特徵參考）"

        if not features:
            return "（無可用特徵參考）"

        dim_label = {
            "sexual_boundary": "性界線 sexual_boundary",
            "coercion": "脅迫 coercion",
            "manipulation": "操控 manipulation",
            "harassment": "騷擾 harassment",
            "emotional_pressure": "情緒壓力 emotional_pressure",
        }

        def band(delta):
            try:
                delta = float(delta)
            except (TypeError, ValueError):
                delta = 0.0
            if delta >= 0.2:
                return "高"
            if delta >= 0.1:
                return "中"
            return "低"

        grouped = {}
        for feature in features:
            category = feature.get("category")
            if category in dim_label:
                grouped.setdefault(category, []).append(feature)

        lines = []
        for category, label in dim_label.items():
            items = grouped.get(category)
            if not items:
                continue
            items.sort(key=lambda item: float(item.get("risk_delta") or 0), reverse=True)
            feature_text = "、".join(
                f"{item.get('feature_name')}（{band(item.get('risk_delta'))}）" for item in items
            )
            lines.append(f"【{label}】{feature_text}")

        return "\n".join(lines) if lines else "（無可用特徵參考）"

    def analyze(
        self,
        current_message: str,
        recent_messages: List[Message],
        temporal_features: TemporalFeatures,
        sender_id: str = "User",
        prior_risk_state: Optional[RiskState] = None,
        relationship_memory: Optional[Dict] = None,
        last_summary: Optional[Dict] = None,
    ) -> Dict:
        """
        執行 LLM 語意分析 (採用安全的 dict.get 存取記憶上下文)
        """
        if self._adapter is None:
            return self._fallback_result(f"Adapter init error: {self._adapter_error}")

        prompt_record = KBService.get_prompt_by_id("risk_analysis_v2")
        if not prompt_record:
            return self._fallback_result("KB Prompt record not found")

        from app.core.llm_adapters import get_nlp_model_name

        try:
            model_name = get_nlp_model_name(prompt_record.get("model"))
        except Exception as e:
            return self._fallback_result(f"Model resolve error: {e}")

        history_list = [f"[MSG {i+1}] {m.sender}: {m.content}" for i, m in enumerate(recent_messages)]
        history_str = "\n".join(history_list) if history_list else "No previous delivered messages"

        prior_str = json.dumps(prior_risk_state.model_dump()) if prior_risk_state else "Initial State"

        if last_summary:
            summary_info = (
                f"Content: {last_summary.get('summary_content', 'N/A')}\n"
                f"Intimacy Level: {last_summary.get('intimacy_level', 0.0)}\n"
                f"Main Topics: {last_summary.get('main_topics', '[]')}\n"
                f"Tone Shift: {last_summary.get('tone_shift', 'stable')}"
            )
        else:
            summary_info = "No prior summary"

        full_prompt = prompt_record["template"].format(
            sender_id=sender_id,
            current_message=current_message,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            recent_messages=history_str,
            prior_risk_state=prior_str,
            reply_latency_seconds=temporal_features.reply_latency_seconds
            if temporal_features.reply_latency_seconds is not None
            else "N/A",
            idle_time_seconds=temporal_features.idle_time_seconds
            if temporal_features.idle_time_seconds is not None
            else "N/A",
            unreplied_count=temporal_features.unreplied_count,
            consecutive_char_count=temporal_features.consecutive_char_count,
            message_ratio=temporal_features.message_ratio,
            volume_ratio=temporal_features.volume_ratio,
            avg_chars_per_message=temporal_features.avg_chars_per_message,
            frequency=temporal_features.frequency,
            message_burst_count=temporal_features.message_burst_count,
            conversation_turns=len(recent_messages),
            interaction_days=relationship_memory.get("interaction_days", 1) if relationship_memory else 1,
            total_messages=relationship_memory.get("total_messages", 0) if relationship_memory else 0,
            familiarity_score=relationship_memory.get("familiarity_score", 0.0) if relationship_memory else 0.0,
            conversation_balance=relationship_memory.get("conversation_balance", 0.5) if relationship_memory else 0.5,
            intimacy_progression_rate=relationship_memory.get("intimacy_progression_rate", 0.0)
            if relationship_memory
            else 0.0,
            latest_summary=summary_info,
            feature_reference=self._build_feature_reference(),
        )

        try:
            response_text = self._adapter.generate(full_prompt, model=model_name)
            return self._parse_response(response_text)
        except Exception as e:
            print(f"   [ NLP Error ] {e}")
            return self._fallback_result(str(e))

    async def get_raw_llm_response(self, prompt: str, model_name: str = "gemini-2.5-flash") -> str:
        """
        Memory summary 用。Caller 應透過 get_summary_model_name + adapter 取得 model。
        為向後相容仍保留 default model_name。
        """
        from app.core.llm_adapters import get_summary_adapter

        try:
            adapter = get_summary_adapter()
        except Exception as e:
            print(f"   [ NLP raw_res adapter error ] {e}")
            return "{}"

        try:
            # 丟到執行緒（2026-08-30）：本函式為 async def，由 rolling summary
            # 的背景任務呼叫。同步的 adapter.generate 會佔住 event loop。
            text = await asyncio.to_thread(adapter.generate, prompt, model=model_name)
            match = re.search(r"(\{.*\})", text, re.DOTALL)
            return match.group(1) if match else text.strip()
        except Exception as e:
            print(f"   [ NLP raw_res Error ] {e}")
            return "{}"

    def _parse_response(self, text: str) -> Dict:
        try:
            match = re.search(r"(\{.*\})", text, re.DOTALL)
            clean_json = match.group(1) if match else text.strip()
            data = json.loads(clean_json)
            scores = data.get("risk_scores", {})
            detected_features = data.get("detected_features", [])
            if not isinstance(detected_features, list):
                detected_features = []

            # === NLP noise filter ===
            # LLM 在分析「明顯安全」訊息時仍會給 0.02-0.05 的保守雜訊分數，
            # 這些低於 NLP_NOISE_FLOOR 的分數視為「模型其實沒把握」，
            # 不計入 risk delta，避免雜訊累積誤觸發 OBSERVATION。
            NLP_NOISE_FLOOR = 0.10

            raw = {
                "sexual_boundary": float(scores.get("sexual_boundary", {}).get("score", 0)),
                "coercion": float(scores.get("coercion", {}).get("score", 0)),
                "manipulation": float(scores.get("manipulation", {}).get("score", 0)),
                "harassment": float(scores.get("harassment", {}).get("score", 0)),
                "emotional_pressure": float(scores.get("emotional_pressure", {}).get("score", 0)),
            }
            delta = {k: (v if v >= NLP_NOISE_FLOOR else 0.0) for k, v in raw.items()}
            evidence = {
                dimension: scores.get(dimension, {}).get("evidence", "")
                for dimension in raw.keys()
            }

            return {
                "delta": RiskState(**delta),
                "reasoning": data.get("reasoning", "No reasoning provided"),
                "confidence": float(data.get("overall_assessment", {}).get("average_confidence", 0.5)),
                "detected_features": detected_features,
                "evidence": evidence,
            }
        except Exception as e:
            print(f"   [ NLP Parsing Error ] {e}")
            return self._fallback_result("Parsing failed")

    def _fallback_result(self, error_msg: str) -> Dict:
        return {
            "delta": RiskState(),
            "reasoning": f"Fallback: {error_msg}",
            "confidence": 0.0,
            "detected_features": [],
            "evidence": {},
        }


from app.services.kb_service import KBService
