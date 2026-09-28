import os
import json
import math
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.query import Query
from appwrite.id import ID
from app.models.schemas import RelationshipMetrics, ConversationSummary, Message
from app.services.kb_service import KBService
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

class RelationshipService:
    def __init__(self):
        self.client = Client()
        self.client.set_endpoint(os.getenv('APPWRITE_ENDPOINT'))
        self.client.set_project(os.getenv('APPWRITE_PROJECT_ID'))
        self.client.set_key(os.getenv('APPWRITE_API_KEY'))
        self.db = Databases(self.client)
        self.db_id = os.getenv('APPWRITE_DB_ID')
        self.metrics_coll = "relationship_metrics"
        self.summary_coll = "conversation_summaries"

    async def update_metrics(self, conv_id: str, sender_id: str, receiver_id: str):
        """每則訊息觸發：更新 L2 指標 (精準計數與角色修復版)"""
        try:
            # 1. 取得對話參會者 (Source of Truth: conversations collection)
            participants = await self._get_conversation_participants(conv_id, sender_id, receiver_id)
            ua_id = participants['user_a_id']
            ub_id = participants['user_b_id']

            response = self.db.list_documents(
                self.db_id, self.metrics_coll,
                queries=[Query.equal("conversation_id", conv_id)]
            )

            now = datetime.now(timezone.utc)
            
            if response.documents:
                doc = response.documents[0]
                data = doc.data if hasattr(doc, 'data') else doc
                doc_id = doc.id if hasattr(doc, 'id') else doc['$id']
                
                # 檢查角色一致性
                if data.get('user_a_id') != ua_id or data.get('user_b_id') != ub_id:
                    print(f"   [ Warning ] Relationship roles mismatch for {conv_id}. Recalculating counts from history...")
                    # 依據 delivered messages 重新計算計數
                    counts = await self._recalculate_message_counts(conv_id, ua_id, ub_id)
                    count_a = counts['user_a_message_count']
                    count_b = counts['user_b_message_count']
                    
                    # 避免重複計算：檢查「目前這則訊息」是否已在 delivered history 中 (通常剛標記為 delivered)
                    # 這裡簡化處理：如果重新計算後的總數不含此訊息，手動加回
                    # 實務上 _recalculate_message_counts 會抓到剛被 update_message_status 的這一則
                else:
                    # 角色一致，正常累加
                    is_user_a = (sender_id == ua_id)
                    count_a = (data.get('user_a_message_count') or 0) + (1 if is_user_a else 0)
                    count_b = (data.get('user_b_message_count') or 0) + (0 if is_user_a else 1)
                
                total_msgs = count_a + count_b
                new_balance = count_b / total_msgs if total_msgs > 0 else 0.5
                
                first_contact_str = data.get('first_contact_at')
                first_contact = datetime.fromisoformat(first_contact_str.replace('Z', '+00:00')) if first_contact_str else now
                interaction_days = (now - first_contact).days + 1
                
                new_familiarity = self.compute_familiarity_score(total_msgs, interaction_days, new_balance)

                update_data = {
                    "user_a_id": ua_id,
                    "user_b_id": ub_id,
                    "total_messages": total_msgs,
                    "user_a_message_count": count_a,
                    "user_b_message_count": count_b,
                    "conversation_balance": round(new_balance, 4),
                    "interaction_days": interaction_days,
                    "familiarity_score": new_familiarity,
                    "last_contact_at": now.isoformat(),
                    "updated_at": now.isoformat()
                }
                self.db.update_document(self.db_id, self.metrics_coll, doc_id, update_data)
                return total_msgs
            else:
                # 初始化 (依據 conversations collection 的角色)
                is_user_a = (sender_id == ua_id)
                count_a = 1 if is_user_a else 0
                count_b = 0 if is_user_a else 1
                new_balance = count_b / 1.0
                interaction_days = 1
                
                new_familiarity = self.compute_familiarity_score(1, interaction_days, new_balance)
                
                new_data = {
                    "conversation_id": conv_id,
                    "user_a_id": ua_id,
                    "user_b_id": ub_id,
                    "total_messages": 1,
                    "user_a_message_count": count_a,
                    "user_b_message_count": count_b,
                    "familiarity_score": new_familiarity,
                    "conversation_balance": round(new_balance, 4),
                    "interaction_days": interaction_days,
                    "first_contact_at": now.isoformat(),
                    "last_contact_at": now.isoformat(),
                    "updated_at": now.isoformat()
                }
                self.db.create_document(self.db_id, self.metrics_coll, ID.unique(), new_data)
                return 1
        except Exception as e:
            print(f"Update relationship metrics failed: {e}")
            return 0

    async def _recalculate_message_counts(self, conv_id: str, user_a_id: str, user_b_id: str) -> Dict[str, Any]:
        """從 Appwrite messages collection 重新掃描並統計計數 (分頁處理)"""
        count_a = 0
        count_b = 0
        limit = 100
        offset = 0
        
        while True:
            res = self.db.list_documents(
                self.db_id, "messages",
                queries=[
                    Query.equal("conversation_id", conv_id),
                    Query.equal("delivery_status", "delivered"),
                    Query.limit(limit),
                    Query.offset(offset)
                ]
            )
            
            if not res.documents:
                break
                
            for doc in res.documents:
                data = doc.data if hasattr(doc, 'data') else doc
                sender = data.get('sender_id')
                if sender == user_a_id:
                    count_a += 1
                elif sender == user_b_id:
                    count_b += 1
            
            if len(res.documents) < limit:
                break
            offset += limit

        total = count_a + count_b
        return {
            "user_a_message_count": count_a,
            "user_b_message_count": count_b,
            "total_messages": total,
            "conversation_balance": count_b / total if total > 0 else 0.5
        }

    async def _get_conversation_participants(self, conv_id: str, sender_id: str, receiver_id: str) -> Dict[str, str]:
        """從 conversations 獲取正統的角色分配；缺失時自動 create"""
        try:
            res = self.db.get_document(self.db_id, "conversations", conv_id)
            data = res.data if hasattr(res, 'data') else res
            ua = data.get('user_a_id')
            ub = data.get('user_b_id')
            
            if not ua or not ub:
                print(f"   [ Warning ] Conversation {conv_id} missing user_a/b_id, fallback to current roles.")
                return {"user_a_id": sender_id, "user_b_id": receiver_id}
                
            return {"user_a_id": ua, "user_b_id": ub}
        except Exception as e:
            try:
                now_iso = datetime.now(timezone.utc).isoformat()
                self.db.create_document(self.db_id, "conversations", conv_id, {
                    "user_a_id": sender_id,
                    "user_b_id": receiver_id,
                    "last_activity": now_iso,
                })
                print(f"   [ Info ] Auto-created conversations doc {conv_id} (user_a={sender_id}, user_b={receiver_id})")
                return {"user_a_id": sender_id, "user_b_id": receiver_id}
            except Exception as create_err:
                print(f"   [ Warning ] Could not auto-create conversation {conv_id}: {create_err}. Fallback to current roles.")
                return {"user_a_id": sender_id, "user_b_id": receiver_id}

    def compute_familiarity_score(self, total_messages: int, interaction_days: int, balance: float) -> float:
        """對數飽和公式：互動量與時間跨度決定熟悉度，對話越單向則整體打折。

        2026-08-05 修正：balance 由加法項改為乘法項。

        原式為 `0.40*msg + 0.40*days + 0.20*balance`，其中 balance_factor 在雙方
        發言平衡時恆為 1.0，等於**無條件贈送 0.20 的地板分**。後果有二：

        1. 全新對話（1 則訊息、第 1 天、發言平衡）的熟悉度即為 0.3059，而
           `abnormal_acceleration_grooming` 的條件是 `familiarity_max: 0.3`
           ——**該規則在平衡對話下永遠無法觸發**，掠奪者加速關係的偵測形同失效。
        2. 「熟人」（100 則／30 天）即達 0.80，使 `stable_familiar_relationship`
           的降權門檻 0.7 過早成立，風險抑制套用得太寬。

        改為乘法後，「對話單向就打折」才真的是打折，而非「平衡就送分」。
        錨點（balance=0.5）：剛認識 0.22／初步認識 0.51／熟人 0.75／熟識 0.91／深交 1.00。

        ⚠️ 此改動會改變所有讀取 familiarity_score 的判斷，相關規則門檻已同步調整
        （見 kb_scenario_rules 的熟識降權三層與 abnormal_acceleration_grooming）。
        """
        msg_factor = min(1.0, math.log(1 + total_messages) / math.log(1 + 501))
        days_factor = min(1.0, math.log(1 + interaction_days) / math.log(1 + 91))
        balance_factor = 1 - 2 * abs(balance - 0.5)
        score = (0.50 * msg_factor + 0.50 * days_factor) * balance_factor
        return round(score, 4)

    async def get_memory_context(self, conv_id: str) -> dict:
        """獲取 Step 1 所需的記憶上下文"""
        try:
            metrics_res = self.db.list_documents(
                self.db_id, self.metrics_coll,
                queries=[Query.equal("conversation_id", conv_id)]
            )
            
            summary_res = self.db.list_documents(
                self.db_id, self.summary_coll,
                queries=[Query.equal("conversation_id", conv_id), Query.order_desc("version"), Query.limit(1)]
            )

            metrics = metrics_res.documents[0].data if metrics_res.documents else None
            summary = summary_res.documents[0].data if summary_res.documents else None

            return {"metrics": metrics, "summary": summary}
        except Exception as e:
            print(f"Get memory context failed: {e}")
            return {"metrics": None, "summary": None}

    async def generate_rolling_summary(self, conv_id: str, metrics: dict):
        """觸發 LLM 生成 L1 摘要 (區段 Chunk 版，只吃 delivered 訊息)"""
        try:
            prev_res = self.db.list_documents(
                self.db_id, self.summary_coll,
                queries=[Query.equal("conversation_id", conv_id), Query.order_desc("version"), Query.limit(1)]
            )
            
            last_msg_id = None
            prev_summary_text = "無"
            next_version = 1
            
            if prev_res.documents:
                p_data = prev_res.documents[0].data if hasattr(prev_res.documents[0], 'data') else prev_res.documents[0]
                last_msg_id = p_data.get('last_processed_msg_id')
                prev_summary_text = p_data.get('summary_content', "無")
                next_version = p_data.get('version', 0) + 1

            # 抓取尚未處理的 delivered 訊息
            queries = [
                Query.equal("conversation_id", conv_id),
                Query.equal("delivery_status", "delivered"),
                Query.order_asc("timestamp")
            ]
            
            if last_msg_id:
                anchor_msg = self.db.get_document(self.db_id, "messages", last_msg_id)
                anchor_data = anchor_msg.data if hasattr(anchor_msg, 'data') else anchor_msg
                anchor_ts = anchor_data.get('timestamp')
                queries.append(Query.greater_than("timestamp", anchor_ts))
            
            chunk_res = self.db.list_documents(self.db_id, "messages", queries=queries)
            if not chunk_res.documents: return

            msg_list = [f"{m.data.get('sender_id')}: {m.data.get('content')}" for m in chunk_res.documents]
            msg_chunk_text = "\n".join(msg_list)

            prompt_data = KBService.get_prompt_by_id("memory_summary_v1")
            if not prompt_data: return

            prompt = prompt_data['template'].format(
                interaction_days=metrics.get('interaction_days', 1),
                total_messages=metrics.get('total_messages', 0),
                conversation_balance=metrics.get('conversation_balance', 0.5),
                initiator_id=metrics.get('user_a_id', 'Unknown'),
                previous_summary=prev_summary_text,
                messages_chunk=msg_chunk_text
            )

            from app.core.nlp_engine import NLPEngine
            nlp = NLPEngine()
            from app.core.llm_adapters import get_summary_model_name
            try:
                summary_model = get_summary_model_name(prompt_data.get('model'))
            except Exception as e:
                print(f"Summary model resolve failed: {e}")
                return
            raw_res = await nlp.get_raw_llm_response(prompt, model_name=summary_model)
            analysis = json.loads(raw_res)

            scores = analysis.get('scores', {})
            intimacy_level = (
                0.40 * float(scores.get('self_disclosure_depth', 0)) +
                0.25 * float(scores.get('emotional_intensity', 0)) +
                0.20 * float(scores.get('exclusivity_framing', 0)) +
                0.15 * float(scores.get('physical_intimacy_reference', 0))
            )

            summary_data = {
                "conversation_id": conv_id,
                "summary_content": analysis.get('summary_content', ''),
                "intimacy_level": round(intimacy_level, 4),
                "version": next_version,
                "main_topics": json.dumps(analysis.get('main_topics', [])),
                "tone_shift": analysis.get('tone_shift', 'stable'),
                "msg_count_snapshot": metrics.get('total_messages', 0),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "first_processed_msg_id": chunk_res.documents[0].id if hasattr(chunk_res.documents[0], 'id') else chunk_res.documents[0]['$id'],
                "last_processed_msg_id": chunk_res.documents[-1].id if hasattr(chunk_res.documents[-1], 'id') else chunk_res.documents[-1]['$id'],
                "conversation_summaries_reasoning": json.dumps(analysis.get('reasoning', {})),
                "self_disclosure_depth": float(scores.get('self_disclosure_depth', 0)),
                "emotional_intensity": float(scores.get('emotional_intensity', 0)),
                "exclusivity_framing": float(scores.get('exclusivity_framing', 0)),
                "physical_intimacy_reference": float(scores.get('physical_intimacy_reference', 0))
            }
            self.db.create_document(self.db_id, self.summary_coll, ID.unique(), summary_data)

            await self.update_progression_rate(conv_id)
        except Exception as e:
            print(f"Rolling summary failed: {e}")

    async def update_progression_rate(self, conv_id: str):
        """計算最近 3-5 版摘要的進展速度 (符合 3-5 版視窗規格)"""
        try:
            res_sums = self.db.list_documents(
                self.db_id, self.summary_coll,
                queries=[Query.equal("conversation_id", conv_id), Query.order_desc("version"), Query.limit(5)]
            )
            
            # 規格要求：優先使用 3-5 版。若少於 3 版，先不更新（維持 0.0）
            if len(res_sums.documents) < 3: return

            summaries = res_sums.documents
            latest = summaries[0].data if hasattr(summaries[0], 'data') else summaries[0]
            earliest = summaries[-1].data if hasattr(summaries[-1], 'data') else summaries[-1]
            
            intimacy_delta = latest.get('intimacy_level', 0) - earliest.get('intimacy_level', 0)
            
            time_latest = datetime.fromisoformat(latest['updated_at'].replace('Z', '+00:00'))
            time_earliest = datetime.fromisoformat(earliest['updated_at'].replace('Z', '+00:00'))
            
            days_elapsed = max(0.5, (time_latest - time_earliest).total_seconds() / 86400)
            rate = intimacy_delta / days_elapsed
            
            res_metrics = self.db.list_documents(self.db_id, self.metrics_coll, [Query.equal("conversation_id", conv_id)])
            if res_metrics.documents:
                doc = res_metrics.documents[0]
                doc_id = doc.id if hasattr(doc, 'id') else doc['$id']
                self.db.update_document(self.db_id, self.metrics_coll, doc_id, {
                    "intimacy_progression_rate": round(max(-1.0, min(1.0, rate)), 4)
                })
        except Exception as e:
            print(f"Update progression rate failed: {e}")
