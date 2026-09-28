"""
Knowledge Base 服務 - 支援全功能特徵、動態配置、二階規則與介入模板
"""

import json
from app.core.database import get_mysql_connection

class KBService:
    @staticmethod
    def get_features():
        """從 MySQL 讀取所有特徵清單"""
        conn = get_mysql_connection()
        if not conn: return []
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM kb_features WHERE enabled = TRUE")
            features = cursor.fetchall()
            for f in features:
                if f['logic_config']:
                    f['logic_config'] = json.loads(f['logic_config'])
            return features
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_scenario_rules():
        """從 MySQL 讀取二階複合規則"""
        conn = get_mysql_connection()
        if not conn: return []
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM kb_scenario_rules WHERE enabled = TRUE")
            rules = cursor.fetchall()
            for r in rules:
                r['condition_logic'] = json.loads(r['condition_logic']) if isinstance(r['condition_logic'], str) else r['condition_logic']
                r['bonus_actions'] = json.loads(r['bonus_actions']) if isinstance(r['bonus_actions'], str) else r['bonus_actions']
            return rules
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_rules():
        """從 MySQL 讀取行為規則 (用於 RuleBasedEngine)"""
        conn = get_mysql_connection()
        if not conn: return []
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM kb_rules WHERE enabled = TRUE ORDER BY priority DESC")
            rules = cursor.fetchall()
            for r in rules:
                if r['conditions']:
                    r['conditions'] = json.loads(r['conditions']) if isinstance(r['conditions'], str) else r['conditions']
                if r['actions']:
                    r['actions'] = json.loads(r['actions']) if isinstance(r['actions'], str) else r['actions']
            return rules
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_prompt(prompt_id="risk_analysis_v2"):
        conn = get_mysql_connection()
        if not conn: return None
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM kb_prompts WHERE prompt_id = %s AND enabled = TRUE", (prompt_id,))
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_prompt_by_id(prompt_id: str):
        """抓取特定的 Prompt 模板"""
        return KBService.get_prompt(prompt_id)

    @staticmethod
    def get_fusion_config(config_id="threshold_v1"):
        conn = get_mysql_connection()
        if not conn: return None
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM kb_configs WHERE config_id = %s AND enabled = TRUE", (config_id,))
            config = cursor.fetchone()
            if config:
                config['thresholds'] = json.loads(config['thresholds']) if isinstance(config['thresholds'], str) else config['thresholds']
                config['weights'] = json.loads(config['weights']) if isinstance(config['weights'], str) else config['weights']
            return config
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_interventions_by_level(risk_level: str):
        """從 MySQL 讀取特定等級的所有介入模板"""
        conn = get_mysql_connection()
        if not conn: return []
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM kb_interventions WHERE risk_level = %s", (risk_level,))
            templates = cursor.fetchall()
            for t in templates:
                t['message_template'] = json.loads(t['message_template']) if isinstance(t['message_template'], str) else t['message_template']
                t['ui_behavior'] = json.loads(t['ui_behavior']) if isinstance(t['ui_behavior'], str) else t['ui_behavior']
            return templates
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_hard_block_records():
        """Get all hard-block records with trigger_mode.

        DB 不可用時退回下列清單。**全部為 `flag`**（2026-09-24 對齊設計決策 D3）：
        原本 8-11 這四筆保留 `block`，使得 D3「禁詞只標記、不攔截」在降級路徑上
        不成立——資料層一掛掉，硬攔截就悄悄復活。這與 D3 的理由無關乎資料來源：
        沒有任何詞句在所有脈絡下都是危險的，降級時更不該由關鍵詞決定結果。
        """
        fallback = [
            {"keyword": "炸彈", "reason_label": "violence", "trigger_mode": "flag"},
            {"keyword": "槍枝", "reason_label": "violence", "trigger_mode": "flag"},
            {"keyword": "自殺", "reason_label": "self_harm", "trigger_mode": "flag"},
            {"keyword": "毒品", "reason_label": "illegal_drugs", "trigger_mode": "flag"},
            {"keyword": "殺人", "reason_label": "violence", "trigger_mode": "flag"},
            {"keyword": "強姦", "reason_label": "sexual_violence", "trigger_mode": "flag"},
            {"keyword": "裸照", "reason_label": "sexual_content", "trigger_mode": "flag"},
            {"keyword": "殺死你", "reason_label": "violence_threat", "trigger_mode": "flag"},
            {"keyword": "強姦你", "reason_label": "sexual_violence_threat", "trigger_mode": "flag"},
            {"keyword": "傳裸照給我", "reason_label": "sexual_demand", "trigger_mode": "flag"},
            {"keyword": "拍裸照給我", "reason_label": "sexual_demand", "trigger_mode": "flag"},
            {"keyword": "不准拒絕", "reason_label": "coercion_threat", "trigger_mode": "flag"},
            {"keyword": "傷害你的家人", "reason_label": "violence_threat", "trigger_mode": "flag"},
            {"keyword": "去你家堵你", "reason_label": "stalking_threat", "trigger_mode": "flag"},
            {"keyword": "宰了你", "reason_label": "violence_threat", "trigger_mode": "flag"},
            {"keyword": "弄死你", "reason_label": "violence_threat", "trigger_mode": "flag"},
            {"keyword": "性侵你", "reason_label": "sexual_violence_threat", "trigger_mode": "flag"},
            {"keyword": "我知道你住哪", "reason_label": "stalking_threat", "trigger_mode": "flag"},
            {"keyword": "把你宰了", "reason_label": "violence_threat", "trigger_mode": "flag"},
            {"keyword": "把你弄死", "reason_label": "violence_threat", "trigger_mode": "flag"},
            {"keyword": "把你殺了", "reason_label": "violence_threat", "trigger_mode": "flag"},
            {"keyword": "把你砍死", "reason_label": "violence_threat", "trigger_mode": "flag"},
            {"keyword": "殺了你", "reason_label": "violence_threat", "trigger_mode": "flag"},
            {"keyword": "殺掉你", "reason_label": "violence_threat", "trigger_mode": "flag"},
            {"keyword": "沒有拒絕的權利", "reason_label": "coercion_threat", "trigger_mode": "flag"},
            {"keyword": "砍死你", "reason_label": "violence_threat", "trigger_mode": "flag"},
            {"keyword": "私密照傳給", "reason_label": "intimate_image_blackmail", "trigger_mode": "flag"},
            {"keyword": "私密照公開", "reason_label": "intimate_image_blackmail", "trigger_mode": "flag"},
            {"keyword": "私密照外流", "reason_label": "intimate_image_blackmail", "trigger_mode": "flag"},
            {"keyword": "裸照公開", "reason_label": "intimate_image_blackmail", "trigger_mode": "flag"},
            {"keyword": "裸照外流", "reason_label": "intimate_image_blackmail", "trigger_mode": "flag"},
            {"keyword": "跟蹤你", "reason_label": "stalking_threat", "trigger_mode": "flag"},
        ]

        conn = get_mysql_connection()
        if not conn:
            return fallback

        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT keyword, reason_label, trigger_mode FROM kb_hard_blocks WHERE enabled = TRUE"
            )
            results = cursor.fetchall()
            return results if results else fallback
        except:
            return fallback
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_hard_block_keywords():
        """Backward compat: returns just keyword strings."""
        records = KBService.get_hard_block_records()
        return [r["keyword"] for r in records]
