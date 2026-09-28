"""
風險融合層 - 細粒度 10 級距配置版
"""

from app.models.schemas import RiskState
from app.services.kb_service import KBService

class RiskFusionLayer:
    def __init__(self):
        pass

    def fuse(self, rule_delta: RiskState, nlp_delta: RiskState, nlp_confidence: float = 0.5) -> RiskState:
        """
        根據 NLP 的信心度，從資料庫級距表檢索權重 (STEP 4)
        實作 10 級距細粒度融合，兼顧平滑性與可解釋性。
        """
        
        # 1. 從 MySQL 讀取配置 (！！！！改Config改這邊！！！！)
        config = KBService.get_fusion_config("threshold_v2_rule_heavy")
        
        # 預設基礎權重 (若 DB 讀取失敗時使用)
        b_dynamic = 0.3 
        tier_label = "Default (Rule-Heavy)"

        if config and 'weights' in config:
            weights_cfg = config['weights']
            
            # 2. 執行 10 級距細粒度搜尋
            # 遍歷從 SQL 讀取的 tiers 列表
            tiers = weights_cfg.get('tiers', [])
            for tier in tiers:
                # 判斷信心度落在哪個區間 [min, max]
                if tier['min'] <= nlp_confidence <= tier['max']:
                    b_dynamic = tier['beta']
                    tier_label = tier.get('label', 'Unknown Tier')
                    break
        
        a_dynamic = 1.0 - b_dynamic

        print(f"   [ Step 4: Fusion ] NLP 信心度: {nlp_confidence:.2f}")
        print(f"   [ Step 4: Fusion ] 匹配級距: {tier_label}")
        print(f"   [ Step 4: Fusion ] 最終權重分配: Rule(α)={a_dynamic:.2f}, NLP(β)={b_dynamic:.2f}")

        # 3. 執行加權融合計算
        total = {}
        fields = ['sexual_boundary', 'coercion', 'manipulation', 'harassment', 'emotional_pressure']
        for key in fields:
            # 權重融合公式：Total = α * Rule_Delta + β * NLP_Delta
            val = a_dynamic * getattr(rule_delta, key) + b_dynamic * getattr(nlp_delta, key)
            total[key] = val

        return RiskState(**total)

    def apply_scenario_bonus(self, base_delta: RiskState, bonus_delta: RiskState) -> RiskState:
        """
        實作演算法 STEP 6: 最終融合
        將初次融合結果加上二階情境獎勵 (Bonus)，並確保不超過 1.0
        """
        final_values = {}
        fields = ['sexual_boundary', 'coercion', 'manipulation', 'harassment', 'emotional_pressure']
        for field in fields:
            sum_val = getattr(base_delta, field) + getattr(bonus_delta, field)
            # 上限 1.0：防止風險值溢出。
            # 下限 0.0（2026-08-05 新增）：抑制型情境規則（如 stable_familiar_relationship）
            # 的負向 bonus 原本可把單則訊息的 delta 壓成負值，語意上等同宣稱「這則訊息
            # 降低了關係的風險」——對死亡威脅之類的內容顯然不成立。
            # 更嚴重的是，負值會壓低 composite 中權重 0.30 的 spread（活躍維度比例），
            # 使等級被連帶拉低：holdout 的 H08「你敢離開我 我就殺死你」即因四個維度
            # 轉負而只判到 warning（見 known-issues #21）。
            # 抑制的設計意圖是「提高容忍度」，不是「反向抵銷」，故夾在 0。
            # 既有累積風險的消退由 Step 7 的 decay 負責，不依賴負向 delta。
            final_values[field] = max(0.0, min(1.0, sum_val))

        return RiskState(**final_values)
