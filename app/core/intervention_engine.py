"""
ㄎSafety Intervention Engine - 指令式介入決策中心 (修正模板匹配邏輯)
"""

import json
import uuid
from datetime import datetime, timezone
from app.services.kb_service import KBService

# 介入顯示節流的預設窗（秒）。可由 kb_interventions.ui_behavior.display_throttle_seconds 覆寫。
# 目的：避免同一段對話內連續觸發時重複跳出提示，造成警示疲勞與使用者抗拒。
DEFAULT_THROTTLE_SECONDS = {
    "observation": 1800,   # 30 分鐘；低調提示，不需頻繁出現
    "warning": 300,        # 5 分鐘；柔性提醒，門檻低故需節流
    "restricted": 0,       # 不節流；已具傷害性，每次都應提醒
    "blocked": 0,          # 不節流；硬性攔截必須告知
}

LEVEL_ORDER = {"safe": 0, "observation": 1, "warning": 2, "restricted": 3, "blocked": 4}

# 已處置豁免（2026-08-15 新增）
# ---------------------------------------------------------------------------
# 【基本映射不變】risk_level → 介入動作。累積到 blocked 就是要攔、要鎖，
# 這是「風險為累積狀態」的核心主張，不因本機制而鬆動。
#
# 【例外】處置的措辭與強度都是寫成「在回應這一則訊息」的，但等級來自**累積狀態**。
# 當這一則其實沒有帶來新風險時，兩者都失準：
#
#   restricted / blocked —— 對一則正常訊息重複施加冷卻與攔截。
#       實測（threshold_v2_rule_heavy：decay=0.7、NOISE_FLOOR=0.10、effdim）：
#       blocked 之後的正常訊息會再被鎖一次，最糟連鎖 2 次共 60 分鐘，
#       期間 1 則無辜訊息被靜靜扣住（is_msg_blocked 只看 risk_level）。
#
#       ⚠️ 幅度不是重點。為一則「本身沒有問題」的訊息施加處置，
#       發生一次就已經是錯的——這才是要修的理由。
#   warning —— 對一則正常訊息說「頻繁的訊息有時會讓對方感到不舒服」。
#       這級本無冷卻可免，修的是**文案準確性**。
#
# 四個條件缺一不可，範圍刻意收窄：
#   ① 上一次介入存在        → 首次觸發不豁免，累積機制照常運作
#   ② 上次等級 ≥ 本次等級   → 風險升高即失效，不會變成保護傘
#   ③ 剩餘冷卻 = 0          → 確認真的服完刑；冷卻無伺服器端強制（known-issues），
#                             此條同時擋下繞過前端硬發的情形
#   ④ 本則 delta < ε        → 服完刑又違規則照罰，且從高狀態起跳罰得更重
#
# 理論依據：Ostrom (1990) 的 graduated sanctions——直接封禁易致怨恨，
# 制裁應與違規程度相稱；以及「同一違規不重複處罰」的 double jeopardy 原則。
# ε 的錨點是 NLP 的雜訊底線，不是調出來的數字。
#
# 【2026-08-31 修正：0.05 → 0.10】
# 原值 0.05 是對齊 `threshold_v1` 的 NOISE_FLOOR，但實際運行的是
# `threshold_v2_rule_heavy`，其 NOISE_FLOOR 為 0.10——錨錯了 config。
#
# 後果是這個門檻被設在「系統做得到的最小值」以下：
# `nlp_engine._parse_response` 的 NLP_NOISE_FLOOR 會把低於 0.10 的分數歸零，
# 所以 NLP 能輸出的最小非零分數就是 0.10；經融合後的 delta 最小約 0.055
# （v2 的 beta 上限 0.60 → 最大 0.06）。ε=0.05 落在它下面，等於要求
# 「NLP 給了字面上的 0」，而不是「本則沒有帶來新風險」。
#
# 實測（blocked → 服完刑 → 第一則中性訊息「抱歉」，跑 5 次）：
#     ε=0.05 → 1/5 豁免      ε=0.10 → 5/5 豁免
# 也就是說 #26 要修的正是這個情境，而舊值讓豁免機制實質上不生效。
#
# 放寬的代價有界：豁免**不動累積狀態**，持續發邊緣訊息分數照樣往上疊，
# 仍會再次觸及 blocked。
EXEMPT_DELTA_EPSILON = 0.10   # 對齊 NLP_NOISE_FLOOR 與 v2 的 NOISE_FLOOR；可由 KB config 覆寫

# 狀態式模板存於這個偽等級。它不是風險等級，只是讓 kb_interventions 能以同一張表
# 承載「豁免時要顯示什麼」，且不會被 get_interventions_by_level(真實等級) 誤抓。
EXEMPT_TEMPLATE_LEVEL = "exempt"

# 豁免時**不**給行動列的等級（規格 §6 的 show_options 欄：「✓（warning 除外）」）。
# 理由見 _to_state_notice 內的說明：行動列從 restricted 才開始，
# warning 若因豁免而多出封鎖／檢舉，會變成「情況越平靜、選項越多」。
_NO_OPTIONS_ON_EXEMPT = {"warning"}


class InterventionEngine:
    def __init__(self):
        self.INTERVENTION_LABELS = {
            "show_ambient_icon": "安全提示",
            "show_reflection_banner": "柔性提醒",
            "show_modal_warning": "正式警告",
            "block_message": "訊息攔截"
        }

    async def execute(self, risk_level: str, risk_state: dict, diagnosis: dict,
                      conv_id: str, sender_id: str, receiver_id: str,
                      msg_id: str, decision_reason: str, chat_log_service=None,
                      message_delta: dict = None) -> dict:
        """產生介入指令。

        chat_log_service: 提供則啟用顯示節流（查詢上次實際顯示的介入）。
                          未提供時不節流，維持既有行為。
        message_delta:    本則訊息自身的 delta。提供則啟用「已處置豁免」判定
                          （見檔案上方說明）。未提供時不豁免，維持既有行為。
        """
        if risk_level == "safe":
            return self._build_empty_command(conv_id, msg_id)

        exempt = await self._is_sanction_served(
            risk_level, conv_id, sender_id, message_delta, chat_log_service)

        primary_risk = max(risk_state, key=risk_state.get)

        # 取得所有該等級的模板
        all_templates = KBService.get_interventions_by_level(risk_level)

        # 2. 取得發送方指令 (確保 template_id 包含 'sender')
        sender_d = self._get_specific_directive(all_templates, primary_risk, "sender")

        # 3. 取得接收方指令 (確保 template_id 包含 'receiver')
        receiver_d = self._get_specific_directive(all_templates, "any", "receiver")

        # 3.4 已處置豁免：換成狀態式文案，並解除冷卻／強制確認／攔截
        #     狀態式模板存於 risk_level='exempt'，不屬於任何風險等級，
        #     故 get_interventions_by_level(risk_level) 抓不到，需另行取得。
        if exempt:
            notice_templates = KBService.get_interventions_by_level(EXEMPT_TEMPLATE_LEVEL)
            sender_d = self._to_state_notice(sender_d, notice_templates, "sender", risk_level)
            receiver_d = self._to_state_notice(receiver_d, notice_templates, "receiver", risk_level)

        # 3.5 顯示節流：寄件方與收件方分別判定
        if chat_log_service is not None:
            sender_d = await self._apply_throttle(
                sender_d, risk_level, conv_id, sender_id, "sender", chat_log_service)
            receiver_d = await self._apply_throttle(
                receiver_d, risk_level, conv_id, sender_id, "receiver", chat_log_service)

        # 4. 管理員通報
        #    豁免時不通報：本則沒有新的違規事實，人工覆核佇列不該被正常訊息灌爆。
        admin_d = None
        if risk_level == "blocked" and not exempt:
            admin_d = {
                "type": "human_review_queue",
                "priority": "high" if decision_reason == "critical_override" else "normal",
                "requires_review_within_hours": 24
            }

        # 每個指令標上它是給誰看的（2026-08-31）。
        #
        # key 名稱（sender_directive／receiver_directive）在回應剛收到時很清楚，
        # 但兩個指令會被一起存進訊息紀錄，雙方之後從歷史讀回來時拿到的是同一份，
        # 少了「我是哪一方」的判斷就會兩個都畫出來——實測整合端正是如此。
        # 帶上 target_user_id 讓前端能直接比對，不必回推誰是寄件方。
        if isinstance(sender_d, dict) and sender_d.get("action") not in (None, "none"):
            sender_d["target_user_id"] = sender_id
        if isinstance(receiver_d, dict) and receiver_d.get("action") not in (None, "none"):
            receiver_d["target_user_id"] = receiver_id

        intervention_id = f"int_{uuid.uuid4().hex[:8]}"
        command = {
            "intervention_id": intervention_id,
            "conversation_id": conv_id,
            "triggered_by_msg_id": msg_id,
            "risk_level": risk_level,
            # 呼叫端據此決定是否扣留訊息：豁免成立時訊息照常送達。
            # 等級本身不變——名聲留著，只是不為同一件事處罰第二次。
            "sanction_exempted": exempt,
            "sender_directive": sender_d,
            "receiver_directive": receiver_d,
            "admin_directive": admin_d
        }

        return command

    async def _is_sanction_served(self, risk_level: str, conv_id: str, sender_id: str,
                                  message_delta: dict, chat_log_service) -> bool:
        """判定「已處置豁免」是否成立。四個條件缺一不可，說明見檔案上方。"""
        if chat_log_service is None or message_delta is None:
            return False

        # ④ 本則沒有帶來新風險
        if max(message_delta.values(), default=0.0) >= EXEMPT_DELTA_EPSILON:
            return False

        # ① 上一次介入存在
        last = await chat_log_service.get_last_displayed_intervention(conv_id, sender_id, "sender")
        if not last:
            return False

        # ② 上次等級 ≥ 本次等級（風險升高即失效）
        if LEVEL_ORDER.get(last.get("risk_level"), 0) < LEVEL_ORDER.get(risk_level, 0):
            return False

        # ③ 冷卻已服完
        remaining = await chat_log_service.get_remaining_cooldown(conv_id, sender_id)
        if remaining and remaining > 0:
            return False

        print(f"   [ Exempt ] 已處置豁免成立（等級仍為 {risk_level}，本則無新風險）")
        return True

    def _to_state_notice(self, directive: dict, templates: list, role: str,
                         risk_level: str = None) -> dict:
        """把「事件式」介入換成「狀態式」通知。

        原文案是寫成在回應這一則訊息的（如「頻繁的訊息有時會讓對方感到不舒服」），
        豁免時那句話是假的。改用 `*_state_notice` 模板陳述目前狀態，
        並解除冷卻與強制確認——刑期已服完，不重複施加。

        找不到狀態式模板時退回原指令但仍解除處置，避免因知識庫未同步而
        讓使用者繼續被鎖（fail-open）。

        risk_level: 用於 warning 的行動列例外，見下方 _NO_OPTIONS_ON_EXEMPT。
        """
        if not directive or directive.get("action") in (None, "none"):
            return directive

        notice = next((t for t in templates
                       if t['template_id'].endswith('_state_notice') and role in t['template_id']), None)
        out = dict(directive)
        if notice:
            ui = notice['ui_behavior']
            out = {
                "action": notice['action_type'],
                "content": {
                    "title": notice['message_template'].get('title'),
                    "body": notice['message_template'].get('body'),
                    "primary_risk_type": "any",   # 豁免時不揭露維度
                },
            }
            for k, v in ui.items():
                if k not in ('cooldown', 'require_ack'):
                    out[k] = v
            # out 是重建的，原指令的行動按鈕不會自動帶過來。
            # 豁免時收件方仍保有行動選項（規格 §4「已豁免（收件方）」），
            # 故改由狀態式模板自己提供。
            #
            # 但 warning 是例外（規格 §6 的 show_options 欄：「✓（warning 除外）」）。
            # 三個等級的豁免共用同一個 `receiver_state_notice` 模板，而該模板是照
            # restricted／blocked 設計的、帶行動列；warning 直接套用會造成：
            #     正常 warning（對方剛發了要警告的訊息）→ 收件方**沒有**封鎖按鈕
            #     豁免 warning（對方發的是正常訊息）    → 收件方**多出**三顆
            # 越平靜給越多選項，方向是反的。行動列從 restricted 才開始，
            # 這是規格 §3「不預設答案」的漸進設計。
            if risk_level not in _NO_OPTIONS_ON_EXEMPT:
                opts = self._parse_action_options(notice.get('action_options'))
                if opts:
                    out['action_options'] = opts
            else:
                out['show_options'] = False
        else:
            print("   [ Exempt Warning ] 找不到 *_state_notice 模板，沿用原文案但仍解除處置")

        # 無論有沒有狀態式模板，處置一律解除
        out["cooldown_seconds"] = 0
        out["require_acknowledgment"] = False
        out["show_feedback_buttons"] = False   # 沒有新事件可供評價
        out["sanction_exempted"] = True
        return out

    async def _apply_throttle(self, directive: dict, risk_level: str, conv_id: str,
                              user_id: str, role: str, chat_log_service) -> dict:
        """依節流窗決定是否抑制本次顯示。

        規則：
        1. 本來就沒有動作（action == "none"）→ 不處理。
        2. 節流窗為 0（restricted / blocked）→ 一律顯示。
        3. **等級較上次顯示時更高 → 一律顯示**（風險正在升高，不可因節流而沉默）。
        4. 距上次顯示未超過節流窗，且等級未升高 → 抑制，action 改為 "suppressed"。

        被抑制者仍會寫入 intervention_logs（action="suppressed"），保留完整稽核軌跡，
        且不會被視為「上次顯示」而推遲下一次的節流窗。
        """
        if not directive or directive.get("action") in (None, "none"):
            return directive

        window = directive.get("display_throttle_seconds")
        if window is None:
            window = DEFAULT_THROTTLE_SECONDS.get(risk_level, 0)
        if not window:
            return directive

        last = await chat_log_service.get_last_displayed_intervention(conv_id, user_id, role)
        if not last or not last.get("timestamp"):
            return directive

        # 等級升高一律顯示
        if LEVEL_ORDER.get(risk_level, 0) > LEVEL_ORDER.get(last.get("risk_level"), 0):
            return directive

        try:
            last_ts = datetime.fromisoformat(str(last["timestamp"]).replace("Z", "+00:00"))
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
        except (ValueError, TypeError) as e:
            print(f"   [ Throttle Warning ] 無法解析上次介入時間: {e}")
            return directive

        if elapsed < window:
            suppressed = dict(directive)
            suppressed["action"] = "suppressed"
            suppressed["content"] = None
            suppressed["cooldown_seconds"] = 0
            suppressed["require_acknowledgment"] = False
            suppressed["throttled"] = {
                "reason": "display_throttle",
                "window_seconds": window,
                "elapsed_seconds": int(elapsed),
                "last_shown_level": last.get("risk_level"),
            }
            print(f"   [ Throttle ] {role} 顯示已抑制（{int(elapsed)}s < {window}s，等級未升高）")
            return suppressed

        return directive

    def _get_specific_directive(self, templates: list, risk_type: str, role: str) -> dict:
        """更精確的過濾邏輯"""
        # 1. 優先找對應維度 + 對應角色
        target = next((t for t in templates if t['primary_risk_type'] == risk_type and role in t['template_id']), None)
        
        # 2. 退而求其次找 any + 對應角色
        if not target:
            target = next((t for t in templates if t['primary_risk_type'] == 'any' and role in t['template_id']), None)
        
        if not target:
            return {"action": "none", "content": None}

        ui = target['ui_behavior']
        directive = {
            "action": target['action_type'],
            "cooldown_seconds": ui.get('cooldown', 0),
            "require_acknowledgment": ui.get('require_ack', False),
            "content": {
                "title": target['message_template'].get('title'),
                "body": target['message_template'].get('body'),
                "primary_risk_type": risk_type
            }
        }
        # ui_behavior 的其餘鍵一律原樣傳遞（2026-08-15 改）。
        #
        # 原本是白名單：只讀 cooldown / require_ack / display_throttle_seconds，
        # 其餘一律丟棄。後果是 `show_options` 明明存在於四個收件方模板中，
        # 卻從未送達前端（全專案 grep 零筆讀取），前端只能自行用 risk_level 硬判。
        #
        # 改為 passthrough 之後，新增 UI 旗標（mascot / show_feedback_buttons /
        # allow_report_text …）都是純知識庫變更，不必動程式、不必重新部署——
        # 與本系統「規則放 KB 不寫死」的一貫做法一致。
        _RENAMED = {'cooldown', 'require_ack'}   # 已改名為上面的正式欄位，不重複輸出
        for k, v in ui.items():
            if k not in _RENAMED and k not in directive:
                directive[k] = v

        # 行動按鈕清單（2026-08-31）。
        #
        # `show_options` 只說「要不要顯示行動列」，沒說**顯示哪幾顆**——
        # 而規格 §4 的三處行動列按鈕組並不相同（restricted 三顆、blocked 四顆、
        # 已豁免三顆）。少了這個欄位，前端只能自行依 risk_level 硬判，
        # 與「後端送出的是指令」這條契約相違。
        opts = self._parse_action_options(target.get('action_options'))
        if opts:
            directive['action_options'] = opts
        return directive

    @staticmethod
    def _parse_action_options(raw) -> list:
        """把 KB 的 action_options 正規化成 [{action, label}, ...]。

        知識庫未同步（欄位不存在或為空）時回空 list，前端沿用 `show_options`
        的舊行為即可——不因此拋錯，與 `_to_state_notice` 的 fail-open 一致。
        """
        if not raw:
            return []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                print("   [ Options Warning ] action_options 不是合法 JSON，忽略")
                return []
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            if isinstance(item, dict) and item.get('action') and item.get('label'):
                out.append({"action": item['action'], "label": item['label']})
        return out

    def _build_empty_command(self, conv_id, msg_id):
        return {
            "intervention_id": None,
            "conversation_id": conv_id,
            "triggered_by_msg_id": msg_id,
            "risk_level": "safe",
            "sender_directive": {"action": "none", "content": None},
            "receiver_directive": {"action": "none", "content": None}
        }
