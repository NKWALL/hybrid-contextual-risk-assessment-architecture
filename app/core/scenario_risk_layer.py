"""
二階情境風險檢測層 (Scenario Risk Detection Layer) - 動態動態匹配版
"""

import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Tuple, List
from app.models.schemas import RiskState, TemporalFeatures
from app.services.kb_service import KBService

class ScenarioRiskLayer:
    def __init__(self):
        # 定義 DB Key 與物件屬性的對應關係，確保未來新增維度時只需改這裡或完全自動化
        self.nlp_map = {
            'sexual': 'sexual_boundary',
            'coercion': 'coercion',
            'manipulation': 'manipulation',
            'harassment': 'harassment',
            'emotional': 'emotional_pressure'
        }
        
        # 已警告過的未知條件鍵，避免每則訊息重複印
        self._warned_unknown_keys = set()

        self.rule_map = {
            'freq': 'frequency',
            'latency': 'latency',
            'burst': 'message_burst_count',
            'ratio': 'volume_ratio',
            'volume_ratio': 'volume_ratio',
            'message_ratio': 'message_ratio',
            'unreplied': 'unreplied_count',
            'consecutive_char': 'consecutive_char_count',
            'avg_chars': 'avg_chars_per_message'
        }

    def evaluate(
        self,
        rule_result: Dict,
        nlp_result: Dict,
        temporal_features: TemporalFeatures,
        memory_metrics: Dict = None,
        last_summary: Dict = None,
        message_time: datetime.datetime = None
    ) -> Tuple[RiskState, List[str]]:
        """
        根據第一層引擎的輸出與環境/記憶資訊，判定是否觸發二階複合情境規則

        message_time: 當前訊息的發送時間。用於判定深夜時段。
                      未提供時退回「現在」，維持既有行為。
        """
        bonus_delta_values = {
            'sexual_boundary': 0.0,
            'coercion': 0.0,
            'manipulation': 0.0,
            'harassment': 0.0,
            'emotional_pressure': 0.0
        }
        triggered_scenarios = []

        # 1. 從 MySQL 獲取二階規則
        scenarios = KBService.get_scenario_rules()

        # 取得訊息發送當下的環境資訊 (明確使用 Asia/Taipei 時區)
        # 使用「訊息時間」而非「處理時間」：訊息若因排隊或重試而延後處理，
        # 用處理時間會判錯時段；離線重跑歷史訊息時亦然。
        ref_time = message_time or datetime.datetime.now(datetime.timezone.utc)
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=datetime.timezone.utc)
        now_taipei = ref_time.astimezone(ZoneInfo("Asia/Taipei"))
        current_hour = now_taipei.hour

        # 定義深夜時段: 00:00 <= hour < 06:00 (台北時間)
        is_midnight = 0 <= current_hour <= 5

        nlp_delta = nlp_result.get('delta')

        for sc in scenarios:
            logic = sc['condition_logic']
            match = True
            
            # 2. 動態條件匹配
            for key, threshold in logic.items():
                # --- [A] 原始分析引擎條件 ---
                if key.startswith('nlp_') and key.endswith('_min'):
                    short_key = key.replace('nlp_', '').replace('_min', '')
                    attr_name = self.nlp_map.get(short_key)
                    if not attr_name:
                        print(f"   [ Scenario Warning ] Unknown NLP condition: {key}")
                        match = False
                        break
                    if getattr(nlp_delta, attr_name) < threshold:
                        match = False
                        break
                elif key.startswith('rule_') and key.endswith('_min'):
                    short_key = key.replace('rule_', '').replace('_min', '')
                    attr_name = self.rule_map.get(short_key)
                    if not attr_name:
                        print(f"   [ Scenario Warning ] Unknown rule condition: {key}")
                        match = False
                        break
                    if getattr(temporal_features, attr_name) < threshold:
                        match = False
                        break
                elif key == 'is_midnight':
                    if threshold != is_midnight:
                        match = False
                        break
                elif key == 'nlp_all_below':
                    # 內容面的安全閥（2026-08-05 新增）：五個維度全部低於門檻時本規則才成立。
                    # 專為抑制型規則設計——熟識可以提高對「你在哪」這類語句的容忍度，
                    # 但不能合理化死亡威脅或跟蹤尾隨：這類內容的威脅性與關係親疏無關。
                    # 原設計預設「熟識與危險此消彼長」，未考慮「很熟 ＋ 內容極端」的組合，
                    # 導致 holdout 的 H08 被降權至 warning（見 known-issues #21）。
                    if nlp_delta is not None:
                        peak = max(getattr(nlp_delta, attr) for attr in self.nlp_map.values())
                        if peak >= threshold:
                            match = False
                            break

                # --- [B] 新增：記憶與指標條件 (L1 & L2) ---
                elif key == 'familiarity_max':
                    val = memory_metrics.get('familiarity_score', 0.0) if memory_metrics else 0.0
                    if val > threshold: match = False; break
                elif key == 'familiarity_min':
                    val = memory_metrics.get('familiarity_score', 0.0) if memory_metrics else 0.0
                    if val < threshold: match = False; break
                elif key == 'intimacy_min':
                    val = last_summary.get('intimacy_level', 0.0) if last_summary else 0.0
                    if val < threshold: match = False; break
                elif key == 'intimacy_max':
                    val = last_summary.get('intimacy_level', 0.0) if last_summary else 0.0
                    if val > threshold: match = False; break
                elif key == 'progression_rate_min':
                    val = memory_metrics.get('intimacy_progression_rate', 0.0) if memory_metrics else 0.0
                    if val < threshold: match = False; break
                elif key == 'balance_max':
                    val = memory_metrics.get('conversation_balance', 0.5) if memory_metrics else 0.5
                    if val > threshold: match = False; break
                elif key == 'balance_range':
                    val = memory_metrics.get('conversation_balance', 0.5) if memory_metrics else 0.5
                    if not (threshold[0] <= val <= threshold[1]): match = False; break
                elif key == 'imbalance_min':
                    val = memory_metrics.get('conversation_balance', 0.5) if memory_metrics else 0.5
                    imbalance = abs(val - 0.5) * 2
                    if imbalance < threshold: match = False; break
                elif key == 'total_messages_min':
                    val = memory_metrics.get('total_messages', 0) if memory_metrics else 0
                    if val < threshold: match = False; break

                else:
                    # 兜底（2026-08-05 新增）：認不得的條件鍵原本會**靜默忽略**——
                    # 不匹配任何分支時 match 維持 True，規則照樣觸發，且沒有任何徵兆。
                    #
                    # 這在「知識庫比程式碼新」時會直接變成安全漏洞：例如 KB 已加入
                    # `nlp_all_below`（熟識降權的內容守衛）而程式碼尚未同步時，
                    # 該守衛會被忽略，死亡威脅又會被降權（known-issues #21 的原始缺陷）。
                    #
                    # 保守作法是「認不得就不套用規則」，但那會使正向規則在同步落差期間
                    # 全部失效（另一個方向的漏洞）。故維持既有行為，只補上警告——
                    # 與上方 nlp_/rule_ 兩個分支的 `Unknown ... condition` 一致。
                    if key not in self._warned_unknown_keys:
                        self._warned_unknown_keys.add(key)
                        print(f"   [ Scenario Warning ] 規則 '{sc['rule_name']}' 使用了認不得的"
                              f"條件鍵 '{key}'，本次判定**已略過此條件**。"
                              f"多半是知識庫比程式碼新——請確認程式碼已同步。")


            # 3. 如果全部符合，套用 Bonus
            if match:
                bonus_actions = sc.get('bonus_actions', {})
                for category, value in bonus_actions.items():
                    if category in bonus_delta_values:
                        bonus_delta_values[category] += value
                triggered_scenarios.append(sc['rule_name'])
                print(f"   [ Scenario Layer Triggered ] 觸發規則: {sc['rule_name']}")

        # 多條規則可能加分到同一維度（例如 grooming 案例會同時命中
        # pua_isolation_guilt / gaslighting_pattern / abnormal_acceleration_grooming，
        # manipulation 累加達 1.2）。RiskState 的欄位上限為 1.0，不裁切會拋
        # ValidationError 使整個 /detect 回 500——而觸發規則越多代表風險越明顯，
        # 等於專門在最高風險的訊息上失效。故在建構前先夾住上限。
        # 下限不夾：負值是刻意設計（stable_familiar_relationship 等抑制規則）。
        bonus_delta_values = {k: min(1.0, v) for k, v in bonus_delta_values.items()}

        return RiskState(**bonus_delta_values), triggered_scenarios
