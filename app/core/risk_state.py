"""
風險狀態機 - 智慧決策版 (完全資料驅動 + Pydantic V2 規範化)
"""

import math
from datetime import datetime
from typing import Dict, Optional, Tuple, List
from app.models.schemas import RiskState
from app.services.kb_service import KBService
from app.services.chat_log_service import ChatLogService

# 等級高低比較用。與 intervention_engine.LEVEL_ORDER 同義，
# 但此處刻意獨立定義，避免 core 反向依賴 intervention 層。
LEVEL_ORDER = {"safe": 0, "observation": 1, "warning": 2, "restricted": 3, "blocked": 4}


class RiskStateMachine:
    def __init__(self):
        self.chat_log_service = ChatLogService()
        self.last_diagnostic = {}

    async def get_user_state(self, conversation_id: str, user_id: str) -> Tuple[RiskState, Optional[str]]:
        return await self.chat_log_service.get_latest_risk_state_with_time(conversation_id, user_id)

    async def update(self, conversation_id: str, user_id: str, msg_id: str, delta: RiskState,
                     degraded_with_flags: bool = False) -> Tuple[RiskState, str]:
        """更新累積狀態並判定風險等級。

        degraded_with_flags: NLP 走了 fallback **且** guardrail 命中禁詞時為 True。
            此時本次顯示的等級不低於 `observation`（見下方說明）。
            **不影響 risk_state 的任何維度**，故不會累積、不會被衰退帶走。
            預設 False，未傳入時行為與既有版本完全相同。
        """
        prior, last_ts_str = await self.get_user_state(conversation_id, user_id)
        #！！！！改Config改這邊！！！！
        config = KBService.get_fusion_config("threshold_v2_rule_heavy")
        
        # 預設值一律對齊現行運行的 threshold_v2_rule_heavy（2026-09-27）：
        # 原本 0.9 是舊 config 的值，DB 一讀不到，衰退就會大幅變慢而風險黏著，
        # 等於降級路徑跑的是另一套參數。見本檔 _BOUNDARY_DEFAULTS 的同批修正。
        msg_decay = 0.7
        if config:
            msg_decay = config.get('decay_factor', 0.7)
        
        time_decay_factor = 1.0
        if last_ts_str:
            try:
                last_ts = datetime.fromisoformat(last_ts_str.replace('Z', '+00:00'))
                delta_t_h = (datetime.now(last_ts.tzinfo) - last_ts).total_seconds() / 3600.0
                time_decay_factor = self._calculate_time_decay(delta_t_h, config)
            except Exception as e:
                print(f"   [ Warning ] Time decay calculation failed: {e}")
        
        total_decay = msg_decay * time_decay_factor

        feedback_signal = await self._get_feedback_signal(conversation_id, user_id)
        if feedback_signal == 'trust':
            total_decay *= 0.7
        if feedback_signal == 'alert':
            delta_dict = delta.model_dump()
            if any(v > 0 for v in delta_dict.values()):
                primary = max(delta_dict, key=delta_dict.get)
                delta_dict[primary] = min(1.0, delta_dict[primary] + 0.1)
                delta = RiskState(**delta_dict)

        new_values = {}
        fields = ['sexual_boundary', 'coercion', 'manipulation', 'harassment', 'emotional_pressure']
        for field in fields:
            new_val = (getattr(prior, field) * total_decay) + getattr(delta, field)
            new_values[field] = min(1.0, max(0.0, new_val))
        
        new_state = RiskState(**new_values)

        logic_cfg = {}
        if config and 'weights' in config:
            logic_cfg = config['weights'].get('decision_logic', {})
            
        w_max = logic_cfg.get('W_MAX', 0.60)
        w_spread = logic_cfg.get('W_SPREAD', 0.30)
        w_trend = logic_cfg.get('W_TREND', 0.10)
        noise_floor = logic_cfg.get('NOISE_FLOOR', 0.10)
        decrease_damping = logic_cfg.get('DECREASE_DAMPING', 0.03)

        # 預設 'effdim' 而非 'count'（2026-09-27）：'count' 是評估第一輪抓出的缺陷
        # （一個維度 1.0 加四個殘影被當成五個維度全開），已改用參與比修正。
        # 預設值若留在 'count'，資料層一失效就退回那個缺陷，等於修正只在正常路徑上成立。
        spread_mode = logic_cfg.get('SPREAD_MODE', 'effdim')
        max_s = max(new_values.values())
        spread_s = self.compute_spread(list(new_values.values()), spread_mode, noise_floor)

        history = await self.chat_log_service.get_recent_risk_state_history(conversation_id, user_id, limit=5)
        trend_s = self._calculate_trend(new_state, history)

        level, composite_s, reason = self._decide_5_level(max_s, spread_s, trend_s, config, w_max, w_spread, w_trend, decrease_damping)

        # 判斷不可用時的等級下限（2026-08-23，known-issues #17）
        # ---------------------------------------------------------------
        # NLP 走了 fallback 時 delta 恆為 0，而規則引擎只看行為（連發、字數比、時間），
        # 抓不到語意。於是「你敢離開我 我就殺死你」這種單獨一句、間隔正常的訊息
        # 會被判 safe——一個安全系統在自己壞掉時回答「安全」。
        #
        # 【為什麼只墊等級，不寫進維度】
        # 寫進某個維度等於宣稱「這是 sexual_boundary 0.20」——但我們並不知道是哪個維度，
        # 那正是掛掉的那一層要判斷的事。而且寫進狀態會被後續的衰退與累積帶著走，
        # 變成「系統壞得越多次就認為你越危險」。
        # 因此 risk_state 完全不動，只保證本次顯示不低於 observation。
        #
        # 【為什麼不看禁詞是哪一類】
        # 設計決策 D3 已確立「沒有任何詞句在所有脈絡下都是危險的」，
        # 組合詞／單一詞的區分於 2026-08-04 退役。依 reason_label 推論「是否指向收件方」
        # 等於重新引入「關鍵詞決定結果」的旁路，與核心主張矛盾。
        # 這裡只用「有沒有命中禁詞」這個最弱的訊號，不宣稱它代表什麼。
        #
        # 【為什麼是 observation 而不是更高】
        # observation 是唯一「只讓收件方知道、寄件方無感」的等級——
        # 語意上剛好等於「我們不確定，所以只告知被保護的一方，不指責任何人」。
        if degraded_with_flags and LEVEL_ORDER.get(level, 0) < LEVEL_ORDER["observation"]:
            level = "observation"
            reason = "degraded_floor"

        decay_applied = (time_decay_factor < 1.0)

        self.last_diagnostic = {
            "max_score": max_s,
            "spread_score": spread_s,
            # 記錄本次用的是哪個 spread 定義：兩個部署若設定不同步，
            # 產出的分數不可互相比較，必須能從結果本身分辨（見 compute_spread 的說明）
            "spread_mode": spread_mode,
            "trend_score": trend_s,
            "composite_score": composite_s,
            "reason": reason,
            "decay_applied": decay_applied,
            "feedback_signal": feedback_signal
        }

        await self.chat_log_service.save_risk_state_history(conversation_id, user_id, msg_id, new_state, level, delta, decay_applied=decay_applied)
        return new_state, level

    async def _get_feedback_signal(self, conversation_id: str, sender_id: str) -> str:
        """Aggregate receiver feedback and background judgments into a recalibration signal."""
        try:
            feedbacks = await self.chat_log_service.get_recent_feedbacks(
                conversation_id, sender_id, limit=5
            )
            judgments = await self.chat_log_service.get_recent_guardrail_context_reviews(
                conversation_id, sender_id, limit=5
            )

            uncomfortable_count = sum(1 for f in feedbacks if f == 'uncomfortable')
            comfortable_count = sum(1 for f in feedbacks if f == 'comfortable')
            concerning_count = sum(1 for j in judgments if j == 'concerning')
            healthy_count = sum(1 for j in judgments if j == 'healthy')

            if uncomfortable_count >= 1 or concerning_count >= 1:
                return 'alert'
            if comfortable_count >= 2 or healthy_count >= 2:
                return 'trust'
            return 'neutral'
        except Exception as e:
            print(f"feedback signal error: {e}")
            return 'neutral'

    @staticmethod
    def compute_spread(values: List[float], mode: str = 'count', noise_floor: float = 0.05) -> float:
        """風險的「分散程度」：同樣強度下，風險攤在越多維度上，等級應越高。

        ⚠️ 【暫時性設施｜校準期專用】2026-08-05 加入
        =============================================
        `SPREAD_MODE` 是為了在新案例池上做 A／B 對照而保留的開關。
        **新案例池校準完成後，應選定一種寫死、並移除另一種與本開關。**
        留著兩套算法超過校準期，只會讓後續維護者誤以為那是刻意的功能。

        ── `count`（原始設計，預設值）─────────────────────────
            spread = 超過 noise_floor 的維度數 / 5

        問題：這是**二元計數**——0.12 的維度與 1.0 的維度算同一票。
        B6 前導評估的 M06 即為實例：manipulation 1.0、其餘四維 0.12～0.20，
        spread 卻是 1.00 並拿滿 0.30 權重，composite 因而達 0.90 判 blocked
        （參考答案為 warning）。
        「同時**活躍**的維度比例」這個設計意圖，在實作上被壓成了布林值。

        提高 noise_floor 不是解法：實測門檻拉到 0.30 反而使低估由 5 則增為 8 則。

        ── `effdim`（有效維度數 / 參與比）────────────────────
            spread = (Σv)² / (Σv² × 5)

        直接回答「風險實際上等於攤在幾個維度上」：

            五維均等   → 5.00 個 → 1.00   （完全分散）
            單一維度   → 1.00 個 → 0.20   （完全集中）

        均等時剛好等於維度數、集中時剛好等於 1，中間連續變化。
        M06 換算為 2.50 個有效維度（spread 0.50），四個殘影不再等同四個真實訊號。

        此測度即統計上的 participation ratio，於生態學（Hill numbers）、
        凝態物理（localization）、經濟學（HHI 倒數）皆為標準的「有效成分數」。

        ── 目前的證據強度 ────────────────────────────────
        退役案例池（n=33）上 `effdim` 的 ±1 準確率為 0.970、`count` 為 0.879，
        差距僅 3 則案例。**結構性理由充分，數值證據尚薄**——這正是保留開關的原因。
        """
        n = len(values) or 1
        if mode == 'effdim':
            # ⚠️ 必須先套 noise_floor（2026-08-15 修）。
            #
            # 參與比是**尺度不變**的：所有維度等比衰退時 spread 完全不動
            #   [0.70,0.30,0.20,0.10,0.05] → 0.576
            #   全部 ×0.9 五次後          → 0.576（一模一樣）
            #
            # 後果一：composite 被鎖在 `W_SPREAD × spread` 的地板上，
            #         永遠回不到 safe。實測 30 則後仍停在 0.191（observation），
            #         極限 0.173 > B_OBSERVATION(0.10)。
            # 後果二：五維皆 0.01（等於沒風險）時 spread=1.00、composite=0.306，
            #         判為 **warning**。同一組值在 count 模式下是 safe。
            #
            # 成因是 count 分支有 `v > noise_floor` 過濾、effdim 分支沒有。
            # 先濾再算即可：真實訊號不受影響（實測 0.576 → 0.537），
            # 只有衰退後的殘影會被清掉。
            #
            # 分母維持 len(values) 而非過濾後的長度——spread 的語意是
            # 「風險攤在**五個維度中的幾個**」，分母必須是總維度數。
            active = [v for v in values if v > noise_floor]
            s1 = sum(active)
            s2 = sum(v * v for v in active)
            return (s1 * s1 / s2) / n if s2 > 0 else 0.0
        # 預設 count：維持原始行為，未同步設定的部署不受影響
        return sum(1 for v in values if v > noise_floor) / n

    def _calculate_trend(self, current: RiskState, history: List[RiskState]) -> float:
        if not history: return 0.0
        # 修正: 使用 model_dump() 取代 dict()
        prev_max = max(history[0].model_dump().values())
        curr_max = max(current.model_dump().values())
        return max(-0.3, min(0.3, (curr_max - prev_max)))

    # 五個等級只需要四條邊界，而舊設定檔有五個 T_* 值——多出來的 T_CRITICAL
    # 其實是被拿去驅動「單一維度覆寫」，並非 composite 的 blocked 門檻。
    # 兩個概念共用一個叫 CRITICAL 的名字，使 T_CRITICAL 看起來能調整 blocked 門檻，
    # 實際上調了完全沒有作用（見 known-issues #20）。
    #
    # 2026-08-05 起改用「邊界語意」命名，一個鍵對應一條邊界，並把覆寫拆為獨立參數：
    #
    #   B_OBSERVATION   safe      → observation
    #   B_WARNING       observation → warning
    #   B_RESTRICTED    warning   → restricted
    #   B_BLOCKED       restricted → blocked
    #   SINGLE_DIM_OVERRIDE  任一維度達此值即直接 blocked（不看 composite）
    #
    # 舊鍵仍可讀取（見下方對照），故未同步設定檔的部署不會壞。
    # ⚠️ 本次僅改命名與結構，**數值行為完全不變**：邊界值與覆寫門檻沿用舊鍵的原數值。
    #    「blocked 是否應從 0.65 提高到 0.85」「覆寫是否應由 0.85 降到 0.65~0.70」
    #    屬數值校準，需以新案例池的 dev 集決定，不在本次範圍。
    _LEGACY_KEY_MAP = {
        'B_OBSERVATION': 'T_SAFE',
        'B_WARNING': 'T_OBSERVATION',
        'B_RESTRICTED': 'T_WARNING',
        'B_BLOCKED': 'T_RESTRICTED',
        'SINGLE_DIM_OVERRIDE': 'T_CRITICAL',
    }
    _BOUNDARY_DEFAULTS = {
        'B_OBSERVATION': 0.10,
        'B_WARNING': 0.20,   # 2026-09-27 由 0.25 對齊 DB 現值；原值會讓降級時警告門檻變嚴
        'B_RESTRICTED': 0.45,
        'B_BLOCKED': 0.65,
        'SINGLE_DIM_OVERRIDE': 0.85,
    }

    def _resolve_thresholds(self, config, w_max: float = 0.60, w_spread: float = 0.30) -> dict:
        """讀取邊界值：優先新鍵，其次同義的舊鍵，最後才用預設值。

        並檢查兩個門檻之間的**結構性前後關係**（見 `_warn_if_override_unreachable`）。
        """
        t = (config or {}).get('thresholds') or {}
        out = {}
        for new_key, legacy_key in self._LEGACY_KEY_MAP.items():
            if new_key in t:
                out[new_key] = t[new_key]
            elif legacy_key in t:
                out[new_key] = t[legacy_key]
            else:
                out[new_key] = self._BOUNDARY_DEFAULTS[new_key]
        self._warn_if_override_unreachable(out, w_max, w_spread)
        return out

    # 已警告過的組合，避免每則訊息都重複印
    _warned_configs = set()

    def _warn_if_override_unreachable(self, b: dict, w_max: float, w_spread: float) -> None:
        """檢查單一維度覆寫是否仍有作用區間。

        `B_BLOCKED` 比的是 composite，`SINGLE_DIM_OVERRIDE` 比的是單一維度，
        兩者尺度不同、不能直接比大小——但它們之間有一個**結構性的前後關係**：

        覆寫存在的理由，是 composite 會把「單一維度極高」稀釋掉
        （該維度只佔 w_max 的權重，且只有它活躍時 spread 僅 1/5）。
        因此覆寫要有意義，必須存在一種狀態滿足：
        **單一維度已達覆寫門檻，但 composite 尚未達到 blocked。**

        最容易滿足的情況是只有一個維度活躍（spread = 0.2、trend = 0）：

            composite_min(max) = w_max * max + w_spread * 0.2

        令其小於 B_BLOCKED，可得覆寫仍有作用的上界：

            SINGLE_DIM_OVERRIDE < (B_BLOCKED - 0.2 * w_spread) / w_max

        超過此上界時，凡是單一維度達到覆寫門檻者 composite 早已 blocked，
        **覆寫永遠不會改變任何結果**——它會變成一個看得到、調了也沒反應的參數，
        正是 known-issues #20 描述的那類缺陷。

        現行值（B_BLOCKED=0.65、w_max=0.60、w_spread=0.30）的上界為 0.983，
        覆寫設 0.85 仍在範圍內，故有作用（專屬窗口約佔輸入空間的 5.6%）。
        """
        ceiling = (b['B_BLOCKED'] - 0.2 * w_spread) / w_max if w_max else float('inf')
        if b['SINGLE_DIM_OVERRIDE'] >= ceiling:
            key = (b['B_BLOCKED'], b['SINGLE_DIM_OVERRIDE'], w_max, w_spread)
            if key not in self._warned_configs:
                self._warned_configs.add(key)
                print(
                    f"   [ Config Warning ] SINGLE_DIM_OVERRIDE={b['SINGLE_DIM_OVERRIDE']} "
                    f"已達或超過作用上界 {ceiling:.3f}（由 B_BLOCKED={b['B_BLOCKED']} 推得）。"
                    f"此設定下單一維度覆寫永遠不會改變結果——凡達覆寫門檻者 composite 早已判 blocked。"
                    f"若要保留覆寫機制，需調低 SINGLE_DIM_OVERRIDE 或調高 B_BLOCKED。"
                )

    def _decide_5_level(self, max_s, spread_s, trend_s, config, w_max, w_spread, w_trend, damp) -> Tuple[str, float, str]:
        b = self._resolve_thresholds(config, w_max, w_spread)

        # 單一維度覆寫：與 composite 分級是兩件不同的事。
        # composite 問的是「整體有多嚴重」，覆寫問的是「有沒有哪一項嚴重到不必看整體」。
        #
        # 回傳的 reason 字串維持 "critical_override" 不變——它是**有作用的**：
        # intervention_engine.py:65 依此設定 priority="high"，
        # risk_detection.py 的 guardrail 攔截路徑也共用同一字串。
        # 改名會靜默降低介入優先度，故本次只改設定鍵名，不動此字串。
        if max_s >= b['SINGLE_DIM_OVERRIDE']:
            return 'blocked', max_s, "critical_override"

        trend_adj = (w_trend * trend_s) if trend_s > 0 else (damp * trend_s)
        composite = (max_s * w_max) + (spread_s * w_spread) + trend_adj
        composite = min(1.0, max(0.0, composite))

        if composite < b['B_OBSERVATION']:   level = 'safe'
        elif composite < b['B_WARNING']:     level = 'observation'
        elif composite < b['B_RESTRICTED']:  level = 'warning'
        elif composite < b['B_BLOCKED']:     level = 'restricted'
        else:                                level = 'blocked'
        return level, composite, "normal"

    def _calculate_time_decay(self, delta_t_h: float, config: dict) -> float:
        lam = 0.028
        if config and 'weights' in config and 'time_decay' in config['weights']:
            tiers = config['weights']['time_decay'].get('tiers', [])
            for tier in tiers:
                if tier['min_h'] <= delta_t_h < tier['max_h']:
                    lam = tier['lambda']
                    break
        return math.exp(-lam * delta_t_h)

    def decide_level(self, state: RiskState) -> str:
        return "SEE_UPDATE_RESULT"
