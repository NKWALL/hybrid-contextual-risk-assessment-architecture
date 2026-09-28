"""
Pydantic 資料模型 - 規範化版本 (與 Appwrite 0518 Schema 深度對齊版)
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict

class TemporalFeatures(BaseModel):
    """時間行為特徵 - 擴充版 (與 Appwrite Integer/Optional 對齊)"""
    # 原始數據 (以原始秒數整數為準，對齊 Appwrite Integer Type)
    reply_latency_seconds: Optional[int] = Field(None, description="對方說完後，我多久才回")
    idle_time_seconds: Optional[int] = Field(None, description="上一則訊息距離現在多久")
    
    # 統計特徵
    unreplied_count: int = Field(0, description="對方未回覆前，我方連續發言數")
    consecutive_char_count: int = Field(0, description="本次連續發言累計字數")
    message_ratio: float = Field(1.0, description="近期發言數量佔比")
    volume_ratio: float = Field(1.0, description="近期發言字數佔比")
    avg_chars_per_message: float = Field(0.0, description="近期平均單則訊息字數")
    
    # 舊有相容欄位
    latency: float = Field(0.0, ge=0, le=1)
    frequency: float = Field(0.0, ge=0, le=1)
    message_burst_count: int = Field(0, ge=0)

class RiskState(BaseModel):
    """風險狀態 (允許負值以支援風險降權邏輯)"""
    sexual_boundary: float = Field(0.0, le=1.0)
    coercion: float = Field(0.0, le=1.0)
    manipulation: float = Field(0.0, le=1.0)
    harassment: float = Field(0.0, le=1.0)
    emotional_pressure: float = Field(0.0, le=1.0)

class Message(BaseModel):
    """訊息 (用於 LLM 上下文，應僅包含 delivered 內容)"""
    sender: str
    content: str
    timestamp: str

class RelationshipMetrics(BaseModel):
    """關係動力學指標 (L2) - 規格對齊版"""
    conversation_id: str
    user_a_id: str
    user_b_id: str
    total_messages: int = 0
    user_a_message_count: int = 0
    user_b_message_count: int = 0
    familiarity_score: float = 0.0
    conversation_balance: float = 0.5
    interaction_days: int = 1
    first_contact_at: str
    last_contact_at: Optional[str] = None
    updated_at: Optional[str] = None
    intimacy_progression_rate: float = 0.0

class ConversationSummary(BaseModel):
    """對話語意摘要 (L1) - 規格對齊版"""
    conversation_id: str
    summary_content: str
    intimacy_level: float = 0.0
    version: int = 1
    main_topics: Optional[str] = None # 對齊 Appwrite String Type (JSON String)
    tone_shift: Optional[str] = "stable"
    first_processed_msg_id: Optional[str] = None
    last_processed_msg_id: Optional[str] = None
    updated_at: str
    # 可解釋性欄位
    conversation_summaries_reasoning: Optional[str] = None
    self_disclosure_depth: float = 0.0
    emotional_intensity: float = 0.0
    exclusivity_framing: float = 0.0
    physical_intimacy_reference: float = 0.0

class RiskDetectionRequest(BaseModel):
    """風險檢測請求"""
    conversation_id: str
    current_message: str
    sender_id: str
    receiver_id: str
    recent_messages: List[Message] = []
    temporal_features: Optional[TemporalFeatures] = None
    prior_risk_state: Optional[RiskState] = None
    relationship_memory: Optional[RelationshipMetrics] = None
    last_summary: Optional[ConversationSummary] = None
    message_timestamp: Optional[str] = Field(
        None,
        description="訊息發送時間 (ISO 8601)。用於時段相關的情境判定；未提供時以處理當下為準。"
                    "正式運作可省略；離線重跑歷史訊息或評估時應帶入，以確保結果可重現。"
    )

class RiskDetectionResponse(BaseModel):
    """風險檢測回應"""
    conversation_id: str
    risk_delta_rule: RiskState
    risk_delta_nlp: RiskState
    risk_delta_total: RiskState
    new_risk_state: RiskState
    risk_level: str
    should_intervene: bool
    intervention_message: Optional[str] = None
    intervention_command: Optional[Any] = None
    triggered_rules: List[str] = []
    diagnostic_signals: Optional[Dict[str, float]] = Field(None)
    nlp_confidence: float = Field(
        0.0,
        description="NLP 引擎對本次判斷的自評信心。融合層據此決定規則／語意的權重。"
    )
    nlp_degraded: bool = Field(
        False,
        description="NLP 是否走了 fallback（LLM 呼叫或解析失敗）。為 True 時語意通道的 delta 恆為 0，"
                    "本次判斷僅由規則引擎與情境層支撐，風險等級可能被低估。"
                    "呼叫端應將此視為『判斷不完整』而非『判定安全』；離線評估時必須排除或另行標記，"
                    "否則失敗案例會與真正的 safe 混在一起無法分辨（見 known-issues #17）。"
    )
    timings_ms: Optional[Dict[str, float]] = Field(
        None,
        description="本次 /detect 各階段耗時（毫秒），供呼叫端定位瓶頸。"
                    "`_total` 是本服務內部的總時間——**不含網路往返**。"
                    "呼叫端自己量到的時間減去 `_total`，即為 gateway、序列化與網路的開銷；"
                    "使用者體感時間再減去呼叫端量到的時間，即為前端與推送的開銷。"
                    "三段分開量，才不會把『NLP 慢』與『中間層慢』混為一談。"
    )
    guardrail_degraded: bool = Field(
        False,
        description="Guardrail 第二層（OpenAI Moderation 或 LLM classifier）是否未能實際執行——"
                    "呼叫失敗、初始化失敗、或未設定憑證而被跳過。"
                    "為 True 時 classifier_flagged 恆為 False，但那代表『沒檢查』而非『檢查過沒問題』。"
                    "禁詞比對（第一層）不受影響，仍照常執行。"
                    "此欄位僅供觀測，不影響任何計分；Guardrail 本就是 flag-only 層，"
                    "失敗時規則引擎與 NLP 兩條路徑仍會產生完整判斷。"
    )

class FeedbackRequest(BaseModel):
    """Receiver / sender 對某次介入的回饋"""
    triggered_by_msg_id: str
    role: str
    feedback: str

class SenderAppealRequest(BaseModel):
    """寄件方對某次介入的文字申訴。

    僅供人工稽核使用，**不進入任何演算法**：若讓被警告者自述無惡意即可
    降低風險分數，將形成可被濫用的繞道。
    """
    triggered_by_msg_id: str
    sender_id: str
    appeal_text: str = Field(..., min_length=1, max_length=2000,
                             description="寄件方對此次介入的說明或異議")


class ReceiverReportRequest(BaseModel):
    """收件方對某次介入的文字補充說明。

    與 SenderAppealRequest 對稱但角色相反，**刻意分成兩個欄位／兩個端點**：
    一個是被警告者自辯，一個是被保護者陳述，稽核意義相反，混用會使後台無從分辨。

    同樣**不進入任何演算法**：收件方的演算法訊號由 /feedback 的二元值
    （comfortable／uncomfortable）承載，那是結構化的。
    """
    triggered_by_msg_id: str
    receiver_id: str
    report_text: str = Field(..., min_length=1, max_length=2000,
                             description="收件方對此次介入的補充說明")


# ── 行動按鈕（2026-08-31）────────────────────────────────────────────
# 介入卡片上的四顆按鈕裡，只有這兩顆需要後端：
#   封鎖 → BlockUserRequest    ：寫入封鎖名單，配對功能據此排除
#   檢舉 → ReportUserRequest   ：進人工審核佇列
# 「停止／結束對話」是關閉聊天室、「繼續對話」是關掉卡片，兩者純前端導航。
#
# **兩者皆不進入任何演算法。** 理由與 /feedback 那條界線相同：
# 封鎖與檢舉是對「這段關係」與「這個人」的選擇，不是對「這則訊息」的判斷。
# 尤其不可把「沒有封鎖」讀成安全訊號——創傷連結下的留下不代表沒有風險
# （見 intervention-ux-spec.md §4 blocked 段的說明）。

class BlockUserRequest(BaseModel):
    """使用者封鎖另一位使用者。

    與檢舉分開：封鎖是**當事人自己的邊界設定**（立即生效、不需審核），
    檢舉是**對平台的申訴**（需要人工判斷）。混成一個端點會讓
    「我只是不想再看到這個人」被迫等待審核。
    """
    blocker_id: str = Field(..., description="執行封鎖的人")
    blocked_id: str = Field(..., description="被封鎖的人")
    conversation_id: Optional[str] = Field(None, description="從哪段對話發起，供稽核")
    source: str = Field("manual", description="manual＝選單／intervention＝安全卡片捷徑")


class UnblockUserRequest(BaseModel):
    """解除封鎖。封鎖必須可逆，否則使用者會因怕誤按而不敢用。"""
    blocker_id: str
    blocked_id: str


class ReportUserRequest(BaseModel):
    """檢舉另一位使用者，進人工審核佇列。

    與 ReceiverReportRequest（`/report`）**是不同的東西**：
      - `/report`      ：針對「這一次系統介入」的補充說明，必須先有介入才存在，
                         寫進 intervention_logs 的欄位，沒有處理狀態。
      - `/report-user` ：針對「這個人」的指控，使用者隨時可主動發起，
                         獨立 collection，有審核狀態。

    `reason_category` 沿用系統既有的五個風險維度＋other，理由是後台審核時
    可直接與該對話的 risk_state 對照——若使用者檢舉 harassment 而系統的
    harassment 維度也偏高，那是相互佐證；若完全不合，那是校準訊號。
    """
    reporter_id: str = Field(..., description="檢舉人")
    reported_id: str = Field(..., description="被檢舉人")
    conversation_id: Optional[str] = None
    reason_category: str = Field(..., description=(
        "sexual_boundary／coercion／manipulation／harassment／"
        "emotional_pressure／other，其餘值回 400"))
    detail_text: Optional[str] = Field(None, max_length=2000,
                                       description="補充描述，選填")
    triggered_by_msg_id: Optional[str] = Field(None, description=(
        "若從安全卡片發起則帶上，讓審核者能直接定位到那一則訊息"))
