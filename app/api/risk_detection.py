"""
風險檢測 API 路由 - Phase 2 修正版 (Guardrail 審計強化)
"""

import asyncio
import time
import warnings
import os
from fastapi import APIRouter, HTTPException, BackgroundTasks
from app.models.schemas import (
    BlockUserRequest, FeedbackRequest, ReceiverReportRequest, ReportUserRequest,
    RiskDetectionRequest, RiskDetectionResponse, RiskState, SenderAppealRequest,
    UnblockUserRequest,
)
from app.core.rule_engine import RuleBasedEngine
from app.core.nlp_engine import NLPEngine
from app.core.risk_fusion import RiskFusionLayer
from app.core.risk_state import RiskStateMachine
from app.core.scenario_risk_layer import ScenarioRiskLayer
from app.core.intervention_engine import InterventionEngine
from app.core.guardrail_engine import GuardrailEngine
from app.services.chat_log_service import ChatLogService
from app.services.background_judge_service import BackgroundJudgeService
from app.services.temporal_feature_service import TemporalFeatureService
from appwrite.id import ID
from appwrite.query import Query
import datetime
import json

# 徹底靜音所有過時警告
warnings.simplefilter('ignore')
os.environ['PYTHONWARNINGS'] = 'ignore'

def _pretty_format_risk(label: str, state_dict: dict) -> str:
    """美化風險狀態輸出，確保條列對齊"""
    lines = [f"   [ {label} ]"]
    for k in sorted(state_dict.keys()):
        v = state_dict[k]
        lines.append(f"      |-- {k.ljust(18)} : {v:.4f}")
    return "\n".join(lines)

router = APIRouter()

# 初始化引擎
rule_engine = RuleBasedEngine()
nlp_engine = NLPEngine()
fusion = RiskFusionLayer()
state_machine = RiskStateMachine()
scenario_risk_layer = ScenarioRiskLayer()
intervention_engine = InterventionEngine()
guardrail_engine = GuardrailEngine()
chat_log_service = ChatLogService()
background_judge_service = BackgroundJudgeService(chat_log_service)

async def handle_relationship_update(conv_id, sender_id, receiver_id):
    """內部輔助：處理關係指標更新與摘要觸發"""
    try:
        total_msgs = await chat_log_service.rel_service.update_metrics(conv_id, sender_id, receiver_id)
        memory_ctx = await chat_log_service.rel_service.get_memory_context(conv_id)
        metrics = memory_ctx['metrics']
        last_summary = memory_ctx['summary']

        should_trigger = False
        last_snapshot = (last_summary.get('msg_count_snapshot') or 0) if last_summary else 0
        if total_msgs - last_snapshot >= 20:
            should_trigger = True
        elif last_summary:
            last_sum_time = datetime.datetime.fromisoformat(last_summary['updated_at'].replace('Z', '+00:00'))
            now = datetime.datetime.now(datetime.timezone.utc)
            if (now - last_sum_time).total_seconds() / 3600 >= 6 and total_msgs > last_snapshot:
                should_trigger = True

        if should_trigger and metrics:
            await chat_log_service.rel_service.generate_rolling_summary(conv_id, metrics)
    except Exception as e:
        print(f"Relationship background update failed: {e}")

@router.post("/detect", response_model=RiskDetectionResponse)
async def detect_risk(req: RiskDetectionRequest, background_tasks: BackgroundTasks):
    """
    執行風險檢測 (整合 Guardrail 完整審計)
    """
    # 各階段耗時（2026-08-31）。整合時「使用者等 7 秒」很難歸因——
    # 究竟是本服務、gateway、寫入還是推送，光看總時間分不出來。
    # 這裡把本服務內部拆開量，呼叫端再自己量一次總時間，兩者相減即為中間層開銷。
    T = {}
    _t0 = time.perf_counter()
    _mark = _t0

    def _lap(name):
        """記下距上一個檢查點的毫秒數。"""
        nonlocal _mark
        now = time.perf_counter()
        T[name] = round((now - _mark) * 1000, 1)
        _mark = now

    def _finish():
        """補上總計並印進 log。回傳給回應的 timings_ms。"""
        T["_total"] = round((time.perf_counter() - _t0) * 1000, 1)
        top = sorted(((v, k) for k, v in T.items() if k != "_total"), reverse=True)[:3]
        print("   [ Timing ] " + "  ".join(f"{k}={v:.0f}ms" for k, v in T.items()))
        if top:
            print(f"      |-- 最久的三段          : "
                  + ", ".join(f"{k} {v:.0f}ms" for v, k in top))
        return T

    try:
        real_msg_id = ID.unique()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("\n" + "="*70)
        print(f"   [ REQUEST ] {now_str}")
        print(f"   Sender: {req.sender_id} | Msg: {req.current_message}")
        print("-" * 50)

        # ---------------------------------------------------------
        # STEP 0: Semantic Guardrail
        # ---------------------------------------------------------
        gr_result = await guardrail_engine.check(req.current_message)
        _lap("step0_guardrail")
        if gr_result["is_blocked"]:
            print(f"   [ Step 0 ] Guardrail TRIGGERED: {gr_result['reason']}")
            if gr_result.get("flagged_words"):
                print(f"      |-- Flagged Words       : {gr_result['flagged_words']}")
            
            # 1. 建立 Blocked 狀態與診斷資訊 (統一使用 critical_override)
            fake_state = RiskState(sexual_boundary=1.0) 
            diag = {
                "reason": "critical_override", 
                "composite_score": 1.0, 
                "max_score": 1.0,
                "spread_score": 0.2,
                "trend_score": 0.0
            }
            
            # 2. 呼叫介入引擎
            intervention_cmd = await intervention_engine.execute(
                risk_level="blocked", risk_state=fake_state.model_dump(),
                diagnosis=diag, conv_id=req.conversation_id, sender_id=req.sender_id,
                receiver_id=req.receiver_id, msg_id=real_msg_id, decision_reason="critical_override",
                chat_log_service=chat_log_service
            )
            
            # 3. 完整審計日誌寫入 (補齊紀錄，但不更新 relationship memory)
            # A. 寫入 messages
            await chat_log_service.log_message(req, msg_id=real_msg_id, is_blocked=True, delivery_status="blocked")
            # B. 寫入風險歷史
            await chat_log_service.save_risk_state_history(
                req.conversation_id, req.sender_id, real_msg_id, 
                fake_state, "blocked", fake_state, decay_applied=False
            )
            # C. 寫入介入日誌
            await chat_log_service.log_intervention(
                req.conversation_id, real_msg_id, req.sender_id, req.receiver_id,
                "blocked", fake_state, diag, "critical_override", "sexual_boundary",
                intervention_cmd["sender_directive"]["action"], intervention_cmd["receiver_directive"]["action"],
                cooldown_seconds=intervention_cmd["sender_directive"].get("cooldown_seconds", 0)
            )
            
            response = RiskDetectionResponse(
                conversation_id=req.conversation_id, risk_level="blocked", should_intervene=True,
                risk_delta_rule=RiskState(), risk_delta_nlp=RiskState(), risk_delta_total=fake_state,
                new_risk_state=fake_state, intervention_command=intervention_cmd,
                triggered_rules=[gr_result['reason']],
                intervention_message=f"訊息因違反安全政策已被攔截 ({gr_result['reason']})",
                diagnostic_signals={
                    "composite": diag["composite_score"],
                    "max": diag["max_score"],
                    "spread": diag["spread_score"],
                    "trend": diag["trend_score"]
                },
                timings_ms=_finish(),
            )
            print("="*70 + "\n")
            return response

        if gr_result.get("flagged_words"):
            print(f"   [ Step 0 ] Flagged (not blocked): {gr_result['flagged_words']}")
        if gr_result.get("classifier_flagged"):
            cats = gr_result.get("classifier_categories", "unknown")
            print(f"   [ Step 0 ] Classifier Flagged (not blocked): {cats}")

        # ---------------------------------------------------------
        # 分析前置：建立 Pending 訊息 (貫穿 ID)
        # ---------------------------------------------------------
        await chat_log_service.log_message(req, msg_id=real_msg_id, is_blocked=False, delivery_status="pending_review")
        _lap("write_pending_message")

        # ---------------------------------------------------------
        # STEP 1: Context 讀取
        # ---------------------------------------------------------
        memory_ctx = await chat_log_service.rel_service.get_memory_context(req.conversation_id)
        relationship_memory = memory_ctx['metrics']
        last_summary = memory_ctx['summary']

        prior_state, _ = await state_machine.get_user_state(req.conversation_id, req.sender_id)
        
        # 雙歷史來源修正：
        # A. delivered_history: 給 LLM 語意分析與摘要 (確保不含未審核內容)
        delivered_history = await chat_log_service.get_recent_messages(req.conversation_id, limit=20, exclude_msg_id=real_msg_id)
        
        # B. behavior_history: 給 TemporalFeatureService 計算行為特徵 (包含 pending_review)
        behavior_history = await chat_log_service.get_recent_behavior_messages(req.conversation_id, limit=20, exclude_msg_id=real_msg_id)

        _lap("step1_context_reads")
        print(f"   [ Step 1 ] Context Loaded")
        print(f"      |-- History (delivered) : {len(delivered_history)} msgs")
        print(f"      |-- History (behavior)  : {len(behavior_history)} msgs")
        if relationship_memory:
            print(f"      |-- Familiarity         : {relationship_memory.get('familiarity_score', 0):.3f}")
            print(f"      |-- Balance             : {relationship_memory.get('conversation_balance', 0.5):.3f}")
            print(f"      |-- Total Messages      : {relationship_memory.get('total_messages', 0)}")
            print(f"      |-- Progression Rate    : {relationship_memory.get('intimacy_progression_rate', 0):.4f}")
        if last_summary:
            print(f"      |-- Last Intimacy Level : {last_summary.get('intimacy_level', 0):.3f}")
        
        # 後端主導計算行為特徵 (使用 behavior_history)
        computed_features = TemporalFeatureService.calculate(
            current_content=req.current_message, current_sender=req.sender_id, history=behavior_history
        )

        # ---------------------------------------------------------
        # STEP 2 ~ 8: 核心分析
        # ---------------------------------------------------------
        rule_result = rule_engine.calculate(req.current_message, computed_features)
        _lap("step2_rule")
        print(_pretty_format_risk("Step 2: Rule Engine Delta", rule_result['delta'].model_dump()))
        if rule_result.get('triggered_rules'):
            print(f"      |-- Triggered           : {rule_result['triggered_rules']}")

        # 丟到執行緒（2026-08-30）：analyze() 是同步函式，內含阻塞的 LLM 呼叫
        # （實測中位 3.4 秒）。在 async handler 裡直接呼叫會佔住 event loop，
        # 使併發請求完全序列化——四人同時傳訊息，最後一人等四倍。
        # 單一請求不會因此變快，變的是併發時彼此不再互相卡住。
        nlp_result = await asyncio.to_thread(
            nlp_engine.analyze,
            req.current_message, delivered_history, computed_features,
            sender_id=req.sender_id, prior_risk_state=prior_state,
            relationship_memory=relationship_memory, last_summary=last_summary
        )
        _lap("step3_nlp")
        print(_pretty_format_risk("Step 3: NLP Engine Delta", nlp_result['delta'].model_dump()))
        print(f"      |-- NLP Confidence      : {nlp_result.get('confidence', 0):.3f}")
        print(f"      |-- NLP Reasoning       : {str(nlp_result.get('reasoning', ''))[:100]}")
        print(f"      |-- NLP Detected Feats  : {nlp_result.get('detected_features', [])}")

        initial_delta = fusion.fuse(rule_result['delta'], nlp_result['delta'], nlp_confidence=nlp_result.get('confidence', 0.0))
        # 時段相關的情境規則以「訊息發送時間」為準；未帶則退回處理當下
        msg_time = None
        if req.message_timestamp:
            try:
                msg_time = datetime.datetime.fromisoformat(
                    req.message_timestamp.replace('Z', '+00:00')
                )
            except ValueError:
                print(f"   [ Warning ] 無法解析 message_timestamp: {req.message_timestamp}")

        bonus_delta, scenarios = scenario_risk_layer.evaluate(
            rule_result, nlp_result, computed_features,
            memory_metrics=relationship_memory, last_summary=last_summary,
            message_time=msg_time
        )
        print(_pretty_format_risk("Step 5: Scenario Bonus Delta", bonus_delta.model_dump()))
        print(f"      |-- Triggered Scenarios : {scenarios if scenarios else 'None'}")
        
        final_delta = fusion.apply_scenario_bonus(initial_delta, bonus_delta)
        _lap("step4to6_fusion_scenario")
        print(_pretty_format_risk("Step 6: Total Message Delta", final_delta.model_dump()))

        # NLP 走 fallback 且 guardrail 命中禁詞時，本次顯示的等級不低於 observation。
        # 只墊等級、不動 risk_state 的任何維度——理由見 risk_state.update() 的說明
        # 與 known-issues #17。
        nlp_is_degraded = str(nlp_result.get('reasoning', '')).startswith('Fallback:')
        degraded_with_flags = nlp_is_degraded and bool(gr_result.get('flagged_words'))
        if degraded_with_flags:
            print(f"   [ Degraded Floor ] NLP 不可用且命中禁詞 {gr_result['flagged_words']}"
                  f" → 等級下限 observation（風險狀態不變）")

        new_state, risk_level = await state_machine.update(
            req.conversation_id, req.sender_id, real_msg_id, final_delta,
            degraded_with_flags=degraded_with_flags)
        _lap("step7to8_state_update")
        diag = getattr(state_machine, 'last_diagnostic', {})
        print(_pretty_format_risk("Step 7: Updated Cumulative State", new_state.model_dump()))
        recal = diag.get('feedback_signal', 'neutral')
        if recal != 'neutral':
            print(f"      |-- Feedback Recalibration : {recal}")
        print(f"   [ Step 8 ] Decision: {risk_level.upper()}")
        print(f"      |-- Composite Score     : {diag.get('composite_score', 0):.4f}")
        print(f"      |-- Max Intensity       : {diag.get('max_score', 0):.4f}")
        print(f"      |-- Risk Spread Signal  : {diag.get('spread_score', 0):.4f}")
        print(f"      |-- Risk Trend Signal   : {diag.get('trend_score', 0):.4f}")
        print(f"      |-- Decision Reason     : {diag.get('reason', 'normal')}")

        # ---------------------------------------------------------
        # STEP 9: 產生介入指令
        # ---------------------------------------------------------
        intervention_cmd = await intervention_engine.execute(
            risk_level=risk_level, risk_state=new_state.model_dump(), diagnosis=diag,
            conv_id=req.conversation_id, sender_id=req.sender_id, receiver_id=req.receiver_id,
            msg_id=real_msg_id, decision_reason=diag.get('reason', 'normal'),
            chat_log_service=chat_log_service,
            message_delta=final_delta.model_dump()
        )

        _lap("step9_intervention")

        # 扣留訊息的判定維持以 risk_level 為準（基本映射不變），
        # 僅在「已處置豁免」成立時放行——同一則違規不重複處罰。
        # 豁免的四個條件見 intervention_engine 上方說明；首次累積到 blocked 一律不豁免。
        is_msg_blocked = (risk_level == "blocked"
                          and not intervention_cmd.get("sanction_exempted", False))
        final_delivery_status = "blocked" if is_msg_blocked else "delivered"

        # ---------------------------------------------------------
        # 效能優化：背景執行更新
        # ---------------------------------------------------------
        background_tasks.add_task(chat_log_service.update_message_status, real_msg_id, is_msg_blocked, final_delivery_status)
        background_tasks.add_task(chat_log_service.update_temporal_features, req.conversation_id, req.sender_id, computed_features)
        background_tasks.add_task(
            chat_log_service.log_analysis_detail,
            real_msg_id, req.conversation_id, rule_result, nlp_result,
            final_delta, scenarios, diag, gr_result.get("flagged_words", []),
            {
                "flagged": gr_result.get("classifier_flagged", False),
                "categories": gr_result.get("classifier_categories", ""),
            }
        )

        classifier_flag = {
            "flagged": gr_result.get("classifier_flagged", False),
            "categories": gr_result.get("classifier_categories", ""),
        }
        flagged_words = gr_result.get("flagged_words", [])
        if background_judge_service.should_review(flagged_words, classifier_flag):
            background_tasks.add_task(
                background_judge_service.review_guardrail_context,
                req.conversation_id, req.sender_id, real_msg_id,
                req.current_message, delivered_history, flagged_words, classifier_flag
            )
        
        if final_delivery_status == "delivered":
            background_tasks.add_task(handle_relationship_update, req.conversation_id, req.sender_id, req.receiver_id)

        if risk_level != "safe":
            primary_risk_type = max(new_state.model_dump(), key=new_state.model_dump().get)
            background_tasks.add_task(
                chat_log_service.log_intervention,
                req.conversation_id, real_msg_id, req.sender_id, req.receiver_id,
                risk_level, new_state, diag, diag.get('reason', 'normal'), primary_risk_type,
                intervention_cmd["sender_directive"]["action"], intervention_cmd["receiver_directive"]["action"],
                intervention_cmd["sender_directive"].get("cooldown_seconds", 0)
            )
            print(f"   [ Step 9 ] Sender Action  : {intervention_cmd['sender_directive']['action']}")
            print(f"      |-- Receiver Action     : {intervention_cmd['receiver_directive']['action']}")
            print(f"      |-- Delivery Status     : {final_delivery_status}")

        response = RiskDetectionResponse(
            conversation_id=req.conversation_id,
            risk_delta_rule=rule_result['delta'],
            risk_delta_nlp=nlp_result['delta'],
            risk_delta_total=final_delta,
            new_risk_state=new_state,
            risk_level=risk_level,
            should_intervene=(risk_level != "safe"),
            triggered_rules=rule_result['triggered_rules'] + scenarios,
            intervention_command=intervention_cmd,
            intervention_message=intervention_cmd["sender_directive"]["content"]["body"] if intervention_cmd["sender_directive"]["content"] else None,
            diagnostic_signals={
                "max": diag.get("max_score", 0.0), "spread": diag.get("spread_score", 0.0),
                "trend": diag.get("trend_score", 0.0), "composite": diag.get("composite_score", 0.0)
            },
            nlp_confidence=nlp_result.get('confidence', 0.0),
            # NLP 失敗時 _fallback_result() 回傳全 0 的 delta，其結果與「模型判定無風險」
            # 完全無法區分。必須向呼叫端揭露，否則降級判斷會被當成正常的 safe。
            nlp_degraded=nlp_is_degraded,
            # 同理：guardrail 第二層失敗時 classifier_flagged 也是 False，
            # 與「檢查過、沒問題」無法區分。不影響計分，僅供呼叫端觀測。
            guardrail_degraded=gr_result.get('degraded', False),
            # 各階段耗時，供呼叫端定位瓶頸。`_total` 不含網路往返——
            # 呼叫端自己量到的時間減去它，就是 gateway 與序列化的開銷。
            timings_ms=_finish(),
        )
        print("="*70 + "\n")
        return response

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reset")
async def reset_risk_state(req: dict):
    return {"status": "Use reset_db.py for reset"}


@router.get("/state")
async def get_risk_state(conversation_id: str, user_id: str):
    """查詢某使用者在某對話中的當前風險狀態與剩餘冷卻秒數。

    供前端在重新進入聊天室時還原 UI：若不提供此查詢，關閉 App 重開後
    前端的冷卻倒數即消失，冷卻等同未曾施加。
    """
    prior_state, _ = await state_machine.get_user_state(conversation_id, user_id)

    level = "safe"
    try:
        response = chat_log_service.db.list_documents(
            chat_log_service.db_id, "risk_state_history",
            queries=[
                Query.equal("conversation_id", conversation_id),
                Query.equal("user_id", user_id),
                Query.order_desc("timestamp"),
                Query.limit(1),
            ]
        )
        if response.documents:
            doc = response.documents[0]
            data = doc.data if hasattr(doc, 'data') else doc
            level = data.get("risk_level", "safe")
    except Exception as e:
        print(f"get_risk_state: 讀取最新風險等級失敗: {e}")

    remaining = await chat_log_service.get_remaining_cooldown(conversation_id, user_id)

    return {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "risk_level": level,
        "risk_state": prior_state.model_dump(),
        "remaining_cooldown": remaining,
    }

@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """
    Receiver/Sender 對某則訊息的介入回饋。
    寫入 intervention_logs.receiver_feedback or sender_feedback。
    """
    if req.role not in ("sender", "receiver"):
        raise HTTPException(status_code=400, detail="role must be 'sender' or 'receiver'")
    if req.feedback not in ("comfortable", "uncomfortable"):
        raise HTTPException(status_code=400, detail="feedback must be 'comfortable' or 'uncomfortable'")

    ok = await chat_log_service.update_intervention_feedback(
        msg_id=req.triggered_by_msg_id,
        role=req.role,
        feedback=req.feedback
    )
    if not ok:
        raise HTTPException(status_code=404, detail="intervention log not found")

    print(f"   [ Feedback ] {req.role}={req.feedback} for msg {req.triggered_by_msg_id}")
    return {
        "status": "ok",
        "msg_id": req.triggered_by_msg_id,
        "role": req.role,
        "feedback": req.feedback
    }


@router.post("/appeal")
async def submit_sender_appeal(req: SenderAppealRequest):
    """寄件方對某次介入提出文字申訴（供人工稽核）。

    使用時機：訊息被判定為 restricted 或 blocked 時，讓寄件方說明或表達異議，
    後續由人工稽核與接收方的回饋並列對照，判斷該次介入是否恰當。

    **此內容不進入任何演算法**，不影響風險分數、不影響 feedback_signal。
    """
    result = await chat_log_service.save_sender_appeal(
        msg_id=req.triggered_by_msg_id,
        sender_id=req.sender_id,
        appeal_text=req.appeal_text,
    )

    if not result["ok"]:
        err = result["error"]
        if err == "not_found":
            raise HTTPException(status_code=404, detail="intervention log not found")
        if err == "sender_mismatch":
            raise HTTPException(status_code=403, detail="only the message sender may appeal")
        if err == "attribute_missing":
            raise HTTPException(
                status_code=503,
                detail="Appwrite intervention_logs 尚未建立 sender_appeal_text 屬性（String, size 2000）"
            )
        raise HTTPException(status_code=500, detail="failed to save appeal")

    print(f"   [ Appeal ] sender={req.sender_id} 對 msg {req.triggered_by_msg_id} 提出申訴（{len(req.appeal_text)} 字）")
    return {
        "status": "ok",
        "msg_id": req.triggered_by_msg_id,
        "note": "已記錄，供人工稽核；不影響風險判斷"
    }


@router.post("/report")
async def submit_receiver_report(req: ReceiverReportRequest):
    """收件方對某次介入補充文字說明（供人工稽核）。

    使用時機：warning 以上等級的保護卡片中，收件方按「有問題」後展開的輸入框；
    restricted 與 blocked 則直接顯示。

    **與 `/appeal` 刻意分開**：兩者稽核意義相反——一個是被警告者自辯，
    一個是被保護者陳述。混用同一端點會使後台無從分辨。

    **此內容不進入任何演算法。** 收件方的演算法訊號由 `/feedback` 的二元值承載。
    """
    result = await chat_log_service.save_receiver_report(
        msg_id=req.triggered_by_msg_id,
        receiver_id=req.receiver_id,
        report_text=req.report_text,
    )

    if not result["ok"]:
        err = result["error"]
        if err == "not_found":
            raise HTTPException(status_code=404, detail="intervention log not found")
        if err == "receiver_mismatch":
            raise HTTPException(status_code=403, detail="only the message receiver may report")
        if err == "attribute_missing":
            raise HTTPException(
                status_code=503,
                detail="Appwrite intervention_logs 尚未建立 receiver_report_text 屬性（String, size 2000）"
            )
        raise HTTPException(status_code=500, detail="failed to save report")

    print(f"   [ Report ] receiver={req.receiver_id} 對 msg {req.triggered_by_msg_id} 補充說明（{len(req.report_text)} 字）")
    return {
        "status": "ok",
        "msg_id": req.triggered_by_msg_id,
        "note": "已記錄，供人工稽核；不影響風險判斷"
    }


# ─────────────────────────────────────────────────────────────
# 行動按鈕（2026-08-31）
#
# 介入卡片上的四顆按鈕（見 intervention-ux-spec.md §4），只有兩顆需要後端：
#   [封鎖]        → POST /block ／ POST /unblock ／ GET /blocks
#   [檢舉]        → POST /report-user
#   [停止／結束對話] → 關閉聊天室回列表，純前端導航
#   [繼續對話]     → 關掉卡片，純前端
#
# **兩者皆不進入任何演算法。** 與 `/feedback` 的界線相同：封鎖與檢舉是對
# 「這段關係」與「這個人」的選擇，不是對「這則訊息」的判斷。尤其不可把
# 「沒有封鎖」讀成安全訊號——創傷連結下留下來不代表沒有風險。
# ─────────────────────────────────────────────────────────────

REPORT_REASON_CATEGORIES = {
    "sexual_boundary", "coercion", "manipulation",
    "harassment", "emotional_pressure", "other",
}


@router.post("/block")
async def block_user(req: BlockUserRequest):
    """封鎖另一位使用者。配對功能應據此排除對象（見 `GET /blocks`）。

    **冪等**：重複封鎖同一人回 200 並標示 `already=true`，不報錯。
    卡片捷徑與聊天室選單是同一個功能的兩個入口，使用者從兩處各按一次
    不該產生兩筆或看到錯誤。

    立即生效、不需審核——這是當事人自己的邊界設定，與需要人工判斷的檢舉不同。
    """
    result = await chat_log_service.save_user_block(
        blocker_id=req.blocker_id,
        blocked_id=req.blocked_id,
        conversation_id=req.conversation_id,
        source=req.source,
    )
    if not result["ok"]:
        err = result["error"]
        if err == "self_block":
            raise HTTPException(status_code=400, detail="cannot block yourself")
        if err == "collection_missing":
            raise HTTPException(
                status_code=503,
                detail="Appwrite 尚未建立 user_blocks collection（見 02_資料庫/add_block_report_collections.py）"
            )
        raise HTTPException(status_code=500, detail="failed to save block")

    print(f"   [ Block ] {req.blocker_id} 封鎖 {req.blocked_id}"
          f"（source={req.source}{'，已存在' if result['already'] else ''}）")
    return {"status": "ok", "already": result["already"],
            "blocker_id": req.blocker_id, "blocked_id": req.blocked_id}


@router.post("/unblock")
async def unblock_user(req: UnblockUserRequest):
    """解除封鎖。封鎖必須可逆，否則使用者會因怕誤按而不敢使用。"""
    result = await chat_log_service.remove_user_block(req.blocker_id, req.blocked_id)
    if not result["ok"]:
        if result["error"] == "not_found":
            raise HTTPException(status_code=404, detail="block record not found")
        raise HTTPException(status_code=500, detail="failed to remove block")
    print(f"   [ Unblock ] {req.blocker_id} 解除封鎖 {req.blocked_id}")
    return {"status": "ok", "blocker_id": req.blocker_id, "blocked_id": req.blocked_id}


@router.get("/blocks")
async def list_blocked_users(user_id: str):
    """回傳配對應排除的對象 id 清單。**雙向**。

    雙向是必要的：A 封鎖 B 之後，若 B 仍配得到 A，等於告訴 B 他被封鎖了。

    配對端也可以直接查 Appwrite 的 `user_blocks`；提供此端點是為了讓
    「雙向」這條規則只實作一次，不必在兩邊各寫一次而有機會漏掉一半。
    """
    blocked = await chat_log_service.get_blocked_user_ids(user_id)
    return {"user_id": user_id, "excluded_user_ids": blocked, "count": len(blocked)}


@router.post("/report-user")
async def report_user(req: ReportUserRequest):
    """檢舉另一位使用者，進人工審核佇列（status=pending）。

    **與 `/report` 是不同的東西**，端點刻意分開：
      - `/report`      ：針對「這一次系統介入」的補充說明，必須先有介入，
                         寫進 `intervention_logs` 的欄位，沒有處理狀態。
      - `/report-user` ：針對「這個人」的指控，使用者隨時可主動發起，
                         獨立 collection，有審核狀態。

    兩者可能同時發生（同一張卡片上既有檢舉按鈕、也有補充說明框），
    混用同一端點會使後台無從分辨是「補充說明」還是「正式檢舉」。

    `reason_category` 沿用系統的五個風險維度＋`other`，讓審核者能直接與
    該對話的 `risk_state` 對照：使用者檢舉 harassment 而系統的 harassment
    也偏高＝相互佐證；完全不合＝校準訊號。
    """
    if req.reason_category not in REPORT_REASON_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"reason_category must be one of {sorted(REPORT_REASON_CATEGORIES)}"
        )

    result = await chat_log_service.save_user_report(
        reporter_id=req.reporter_id,
        reported_id=req.reported_id,
        reason_category=req.reason_category,
        detail_text=req.detail_text,
        conversation_id=req.conversation_id,
        triggered_by_msg_id=req.triggered_by_msg_id,
    )
    if not result["ok"]:
        err = result["error"]
        if err == "self_report":
            raise HTTPException(status_code=400, detail="cannot report yourself")
        if err == "collection_missing":
            raise HTTPException(
                status_code=503,
                detail="Appwrite 尚未建立 user_reports collection（見 02_資料庫/add_block_report_collections.py）"
            )
        raise HTTPException(status_code=500, detail="failed to save report")

    print(f"   [ ReportUser ] {req.reporter_id} 檢舉 {req.reported_id}"
          f"（{req.reason_category}）→ {result['report_id']}")
    return {
        "status": "ok",
        "report_id": result["report_id"],
        "review_status": "pending",
        "note": "已進入人工審核佇列；不影響風險判斷"
    }
