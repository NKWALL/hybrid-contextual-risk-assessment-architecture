"""
Knowledge Base: 規則定義與風險配置

⚠️ 【已停用，勿參考】2026-08-05 標記
===================================
本檔為早期以硬編碼模擬知識庫的版本，**目前專案內無任何地方 import**。
現行的規則、閾值與配置一律由 MySQL 知識庫載入（`app/services/kb_service.py`），
整合端則改由 Appwrite 讀取。

保留本檔僅為歷史參考。**其中的數值已與現行系統嚴重脫節**，特別是：

* `MOCK_RISK_CONFIG` 的 `T_LOW` / `T_HIGH` 是**三級**風險設計的殘留，
  現行系統為**五級**（safe／observation／warning／restricted／blocked），
  邊界鍵名為 `B_OBSERVATION` / `B_WARNING` / `B_RESTRICTED` / `B_BLOCKED`
  ＋ 獨立的 `SINGLE_DIM_OVERRIDE`（見 `app/core/risk_state.py`）。
* `alpha` / `beta` 為固定權重，現行融合層改為依 NLP 自評信心動態加權。

**要查現行閾值請看 `kb_configs`，不要看這個檔。**
"""

# === 行為規則定義 (用於 Rule-based Engine) ===
MOCK_RULES = [
    {
        'rule_id': 'rule_behavior_001',
        'rule_name': 'unreplied_harassment',
        'description': '對方未回覆前持續發言',
        'conditions': {
            'unreplied_count': {'operator': '>=', 'threshold': 3}
        },
        'actions': {
            'harassment': 0.15
        },
        'enabled': True
    },
    {
        'rule_id': 'rule_behavior_002',
        'rule_name': 'volume_dumping',
        'description': '發言量極度不對等 (情緒傾倒)',
        'conditions': {
            'volume_ratio': {'operator': '>=', 'threshold': 3.0}
        },
        'actions': {
            'emotional_pressure': 0.10
        },
        'enabled': True
    },
    {
        'rule_id': 'rule_behavior_003',
        'rule_name': 'high_frequency_burst',
        'description': '短時間爆發式發送',
        'conditions': {
            'message_burst_count': {'operator': '>', 'threshold': 3}
        },
        'actions': {
            'emotional_pressure': 0.15,
            'harassment': 0.10
        },
        'enabled': True
    }
]

# === 風險融合配置 ===
MOCK_RISK_CONFIG = {
    'alpha': 0.6,           # Rule-based 權重 (行為)
    'beta': 0.4,            # NLP 權重 (語意)
    'decay_factor': 0.90,   # 衰減係數
    'T_LOW': 0.25,
    'T_HIGH': 0.60,
    'T_CRITICAL': 0.85
}
