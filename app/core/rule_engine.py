"""
Rule-based 風險檢測引擎 - 整合 MySQL 特徵正則掃描
"""

import re
from app.models.schemas import TemporalFeatures, RiskState
from app.services.kb_service import KBService
from typing import Dict, List

class RuleBasedEngine:
    def __init__(self):
        pass

    def calculate(
            self,
            current_message: str,
            temporal_features: TemporalFeatures
    ) -> Dict:
        """
        同時計算：
        1. 行為不對等風險 (來自 kb_rules)
        2. 明確特徵風險 (來自 kb_features 的正則掃描)
        """

        risk_delta = {
            'sexual_boundary': 0.0,
            'coercion': 0.0,
            'manipulation': 0.0,
            'harassment': 0.0,
            'emotional_pressure': 0.0
        }

        triggered_rules = []

        # --- A. 處理行為規則 (如: 連發、字數比) ---
        behavior_rules = KBService.get_rules()
        for rule in behavior_rules:
            if self._check_behavior_conditions(rule, temporal_features):
                for risk_type, value in rule['actions'].items():
                    if risk_type in risk_delta:
                        risk_delta[risk_type] += value
                triggered_rules.append(f"BEHAVIOR: {rule['rule_name']}")

        # --- B. 處理特徵掃描 (正則表達式匹配) ---
        features = KBService.get_features()
        for f in features:
            pattern = f.get('regex_pattern')
            # 只有當資料庫有填寫正則表達式時才執行掃描
            if pattern and pattern.strip():
                try:
                    if re.search(pattern, current_message, re.IGNORECASE):
                        category = f['category']
                        delta = f['risk_delta']
                        if category in risk_delta:
                            risk_delta[category] += delta
                        triggered_rules.append(f"REGEX_MATCH: {f['feature_name']}")
                except Exception as e:
                    print(f"Regex syntax error [{pattern}]: {e}")

        return {
            'delta': RiskState(**risk_delta),
            'triggered_rules': triggered_rules
        }

    def _check_behavior_conditions(self, rule, features: TemporalFeatures):
        """檢查行為門檻 (支援 Phase 2 新欄位)"""
        conditions = rule.get('conditions', {})

        # 基礎特徵匹配
        field_mapping = {
            'unreplied_count': features.unreplied_count,
            'volume_ratio': features.volume_ratio,
            'message_burst_count': features.message_burst_count,
            'message_ratio': features.message_ratio,
            'consecutive_char_count': features.consecutive_char_count,
            'avg_chars_per_message': features.avg_chars_per_message,
            'latency': features.latency,
            'frequency': features.frequency
        }

        for cond_key, cond_cfg in conditions.items():
            if cond_key in field_mapping:
                val = field_mapping[cond_key]
                if not self._compare(val, cond_cfg['operator'], cond_cfg['threshold']):
                    return False

        return True

    def _compare(self, value, operator, threshold):
        if operator == '>': return value > threshold
        if operator == '<': return value < threshold
        if operator == '>=': return value >= threshold
        if operator == '<=': return value <= threshold
        return False
