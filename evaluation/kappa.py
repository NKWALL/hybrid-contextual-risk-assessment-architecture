"""
標註者間一致性（Kappa）計算腳本
================================
讀取 evaluation/annotations/ 下所有標註檔，計算標註者間一致性，並列出分歧案例。

Kappa 的意義：把「兩位標註者各憑自己的標註習慣、碰巧標成一樣」的部分扣掉之後，
真正的共識還剩多少。公式精神：

    Kappa = (實際一致率 − 巧合一致率) / (1 − 巧合一致率)

因此 Kappa = 1 代表完全一致，0 代表「跟隨機亂標一樣」，負值代表比亂標還糟。

本腳本以純 Python 實作，不需 numpy / sklearn / statsmodels。

用法（在 python_backend 目錄下）
--------------------------------
    venv/bin/python evaluation/kappa.py
    venv/bin/python evaluation/kappa.py --split test        # 只看主測試集
    venv/bin/python evaluation/kappa.py --json report.json  # 另存機器可讀結果

計算內容
--------
1. Fleiss' Kappa    ── 三位以上標註者的整體一致性（五級標籤）
2. Cohen's Kappa    ── 兩兩配對，五級標籤採 quadratic-weighted（差一級的懲罰小於差三級）
3. 介入 binary      ── warning 以上視為需介入，作為穩健備援指標
4. 分歧案例         ── 標註者意見不一致的案例，供後續分析
"""

import argparse
import json
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CASE_POOL = BASE_DIR / "case_pool.json"
ANNOT_DIR = BASE_DIR / "annotations"

LEVELS = ["safe", "observation", "warning", "restricted", "blocked"]
LEVEL_IDX = {l: i for i, l in enumerate(LEVELS)}
INTERVENE_FROM = LEVEL_IDX["warning"]  # warning 以上 = 需要對使用者做可見動作

LANDIS_KOCH = [
    (0.81, "幾乎完美 (almost perfect)"),
    (0.61, "高度一致 (substantial)"),
    (0.41, "中等一致 (moderate)"),
    (0.21, "尚可 (fair)"),
    (0.00, "極弱 (slight)"),
]


def interpret(k):
    if k < 0:
        return "比隨機還差 (poor)"
    for lo, label in LANDIS_KOCH:
        if k >= lo:
            return label
    return "極弱 (slight)"


# ---------------------------------------------------------------------------
# Kappa 實作
# ---------------------------------------------------------------------------
def cohen_kappa(a, b, k, weights=None):
    """兩位標註者的 Cohen's Kappa。

    a, b     : 等長的類別索引序列（0..k-1）
    weights  : None = 一般 Kappa；'quadratic' = 平方加權（適用有序尺度）
    """
    n = len(a)
    if n == 0:
        return None

    def w(i, j):
        if weights is None:
            return 0.0 if i == j else 1.0
        if weights == "quadratic":
            return ((i - j) ** 2) / ((k - 1) ** 2)
        raise ValueError(f"未知的加權方式：{weights}")

    obs = {}
    for x, y in zip(a, b):
        obs[(x, y)] = obs.get((x, y), 0) + 1
    ca, cb = Counter(a), Counter(b)

    # 加權版本以「不一致程度」計算：Kappa = 1 − 觀察到的不一致 / 期望的不一致
    d_obs = sum(w(i, j) * c / n for (i, j), c in obs.items())
    d_exp = sum(w(i, j) * (ca[i] / n) * (cb[j] / n) for i in range(k) for j in range(k))
    if d_exp == 0:
        return 1.0
    return 1.0 - d_obs / d_exp


def fleiss_kappa(rows, k):
    """Fleiss' Kappa。rows = 每個案例一列，列出每個類別被幾位標註者選中。

    要求每列的標註者總數相同（本腳本只取所有標註者都標過的案例）。
    """
    n_items = len(rows)
    if n_items == 0:
        return None
    n_raters = sum(rows[0])
    if n_raters < 2:
        return None
    if any(sum(r) != n_raters for r in rows):
        return None

    # 每個案例內，任兩位標註者同意的比例
    p_i = [
        (sum(c * c for c in row) - n_raters) / (n_raters * (n_raters - 1))
        for row in rows
    ]
    p_bar = sum(p_i) / n_items
    # 各類別被選用的整體比例
    p_j = [sum(row[j] for row in rows) / (n_items * n_raters) for j in range(k)]
    p_e = sum(p * p for p in p_j)

    if p_e == 1:
        return 1.0
    return (p_bar - p_e) / (1 - p_e)


# ---------------------------------------------------------------------------
# 載入
# ---------------------------------------------------------------------------
def load_pool(split_filter):
    with open(CASE_POOL, encoding="utf-8") as f:
        pool = json.load(f)
    cases = {}
    for c in pool["cases"]:
        if c["split"] in {"test", "holdout"} and (not split_filter or c["split"] == split_filter):
            cases[c["case_id"]] = c
    return cases


def load_all_annotations():
    if not ANNOT_DIR.exists():
        return {}
    out = {}
    for p in sorted(ANNOT_DIR.glob("*.json")):
        with open(p, encoding="utf-8") as f:
            out[p.stem] = {a["case_id"]: a for a in json.load(f)}
    return out


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="標註者間一致性計算")
    ap.add_argument("--split", choices=["test", "holdout"], help="只計算指定切分")
    ap.add_argument("--json", metavar="PATH", help="另存機器可讀結果")
    args = ap.parse_args()

    cases = load_pool(args.split)
    annots = load_all_annotations()

    if not annots:
        print(f"找不到任何標註檔（{ANNOT_DIR}）。")
        print("請先執行：venv/bin/python evaluation/annotate.py --status")
        return 1

    print("=" * 68)
    print("  標註者間一致性報告")
    print("=" * 68)
    scope = args.split or "test + holdout"
    print(f"\n案例範圍：{scope}，共 {len(cases)} 則")
    print("標註者進度：")
    for rid, recs in annots.items():
        covered = len(set(recs) & set(cases))
        print(f"  {rid:16} {covered}/{len(cases)}")

    # 只取「所有標註者都標過」的案例，確保比較基礎一致
    raters = sorted(annots)
    common = sorted(set(cases) & set.intersection(*(set(annots[r]) for r in raters)))
    print(f"\n所有標註者皆完成的案例：{len(common)} 則")
    if len(common) < 2:
        print("共同案例不足，無法計算一致性。請先完成標註。")
        return 1

    labels = {r: [LEVEL_IDX[annots[r][c]["risk_level"]] for c in common] for r in raters}
    binaries = {r: [1 if v >= INTERVENE_FROM else 0 for v in labels[r]] for r in raters}

    report = {"scope": scope, "n_cases": len(common), "raters": raters, "results": {}}

    # --- 標註分佈 ---
    print("\n" + "-" * 68)
    print("  各標註者的等級分佈")
    print("-" * 68)
    for r in raters:
        cnt = Counter(annots[r][c]["risk_level"] for c in common)
        print(f"  {r:16} " + "  ".join(f"{l}={cnt[l]}" for l in LEVELS if cnt[l]))

    # --- Fleiss ---
    print("\n" + "-" * 68)
    print("  整體一致性（Fleiss' Kappa，五級標籤）")
    print("-" * 68)
    if len(raters) >= 3:
        rows = []
        for i in range(len(common)):
            row = [0] * len(LEVELS)
            for r in raters:
                row[labels[r][i]] += 1
            rows.append(row)
        fk = fleiss_kappa(rows, len(LEVELS))
        if fk is None:
            print("  無法計算（標註者人數不一致）")
        else:
            print(f"  Fleiss' Kappa = {fk:.3f}   → {interpret(fk)}")
            report["results"]["fleiss_kappa_5level"] = round(fk, 4)

        rows_b = []
        for i in range(len(common)):
            row = [0, 0]
            for r in raters:
                row[binaries[r][i]] += 1
            rows_b.append(row)
        fkb = fleiss_kappa(rows_b, 2)
        if fkb is not None:
            print(f"  Fleiss' Kappa（是否介入 binary） = {fkb:.3f}   → {interpret(fkb)}")
            report["results"]["fleiss_kappa_intervene"] = round(fkb, 4)
    else:
        print(f"  標註者只有 {len(raters)} 位，需 3 位以上才適用 Fleiss。見下方兩兩配對。")

    # --- Cohen 兩兩配對 ---
    print("\n" + "-" * 68)
    print("  兩兩一致性（Cohen's Kappa）")
    print("-" * 68)
    print(f"  {'配對':34} {'加權':>8} {'未加權':>8} {'介入':>8}")
    pairs = {}
    for r1, r2 in combinations(raters, 2):
        kw = cohen_kappa(labels[r1], labels[r2], len(LEVELS), weights="quadratic")
        ku = cohen_kappa(labels[r1], labels[r2], len(LEVELS))
        kb = cohen_kappa(binaries[r1], binaries[r2], 2)
        name = f"{r1} × {r2}"
        print(f"  {name:34} {kw:8.3f} {ku:8.3f} {kb:8.3f}")
        pairs[name] = {"weighted": round(kw, 4), "unweighted": round(ku, 4), "intervene": round(kb, 4)}
    report["results"]["cohen_pairs"] = pairs
    print("\n  加權 = quadratic-weighted（五級為有序尺度，差一級的懲罰小於差三級），主指標。")

    # --- 分歧案例 ---
    print("\n" + "-" * 68)
    print("  分歧案例（供標註品質分析）")
    print("-" * 68)
    disagreements = []
    for i, cid in enumerate(common):
        vals = [labels[r][i] for r in raters]
        spread = max(vals) - min(vals)
        if spread > 0:
            disagreements.append({
                "case_id": cid,
                "spread": spread,
                "labels": {r: annots[r][cid]["risk_level"] for r in raters},
                "stratum": cases[cid]["stratum"],
            })
    disagreements.sort(key=lambda d: -d["spread"])

    if not disagreements:
        print("  所有標註者完全一致。")
    else:
        print(f"  共 {len(disagreements)} 則有分歧（占 {len(disagreements)/len(common)*100:.0f}%）\n")
        for d in disagreements:
            mark = "⚠ " if d["spread"] >= 2 else "  "
            detail = "  ".join(f"{r}={v}" for r, v in d["labels"].items())
            print(f"  {mark}{d['case_id']}（差 {d['spread']} 級，{d['stratum']}）  {detail}")
        big = [d for d in disagreements if d["spread"] >= 2]
        if big:
            print(f"\n  ⚠ 標記者為差距 2 級以上（{len(big)} 則），優先檢視：")
            print("    ─ 若為準則描述模糊 → 修訂 annotation-rubric.md 後重標")
            print("    ─ 若為案例本質具爭議 → 保留，並在報告中作為邊界情境討論")
    report["disagreements"] = disagreements

    # --- 總結 ---
    print("\n" + "=" * 68)
    print("  判讀參考（Landis & Koch, 1977）")
    print("=" * 68)
    print("  < 0.00 比隨機還差 ｜ 0.00-0.20 極弱 ｜ 0.21-0.40 尚可")
    print("  0.41-0.60 中等   ｜ 0.61-0.80 高度 ｜ 0.81-1.00 幾乎完美")
    print("\n  Kappa 高（≥0.6）→ 參考答案獲多位獨立標註者支持，可作為評估基準。")
    print("  Kappa 低         → 不是失敗；先檢視上方分歧案例，判斷是準則模糊還是案例本質有爭議。")

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已另存：{args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
