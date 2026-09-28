"""
評估指標計算
============
讀取 run_eval.py 產出的預測結果，比對 ground_truth.json，計算指標並產出報告。

用法（在 python_backend 目錄下）
--------------------------------
    venv/bin/python evaluation/metrics.py --predictions test_baseline_20260805_1430.json
    venv/bin/python evaluation/metrics.py --predictions <檔名> --out report.md

設計要點
--------
* **所有指標對兩版參考答案各算一次**（gt_adjudicated / gt_majority）。裁決者同時為
  系統設計者，此敏感度分析用以檢驗結論是否穩健。
* **主指標為 quadratic-weighted Kappa**：可與標註者間一致性（人 × LLM 0.897–0.913）
  直接並排比較，回答「系統與參考答案的一致程度，是否接近人類標註者之間的水準」。
* **誤報率與漏報率分開報告**：安全系統的漏報比誤報嚴重，損失函數本就不對稱。
* **observation 排除於 macro 平均外**：參考答案中該級樣本數為 0（見
  `annotation-adjudication.md`），無法評估；系統若判該級則單獨列出診斷。
* 純 Python 實作，不需 numpy / sklearn。
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RESULT_DIR = BASE_DIR / "results"
GT_PATH = BASE_DIR / "ground_truth.json"
CASE_POOL = BASE_DIR / "case_pool.json"

LEVELS = ["safe", "observation", "warning", "restricted", "blocked"]
IDX = {l: i for i, l in enumerate(LEVELS)}
INTERVENE_FROM = IDX["warning"]


# ---------------------------------------------------------------------------
# 指標實作（純 Python）
# ---------------------------------------------------------------------------
def weighted_kappa(a, b, k=5, quadratic=True):
    """兩序列的 Kappa。quadratic=True 時對有序尺度加權（差一級的懲罰小於差三級）。

    與 kappa.py 的實作一致，故「系統 vs 參考答案」可與「標註者之間」直接比較。
    """
    n = len(a)
    if n == 0:
        return None

    def w(i, j):
        return ((i - j) ** 2) / ((k - 1) ** 2) if quadratic else (0.0 if i == j else 1.0)

    ca, cb = Counter(a), Counter(b)
    obs = Counter(zip(a, b))
    d_obs = sum(w(i, j) * c / n for (i, j), c in obs.items())
    d_exp = sum(w(i, j) * (ca[i] / n) * (cb[j] / n) for i in range(k) for j in range(k))
    return 1.0 if d_exp == 0 else 1.0 - d_obs / d_exp


def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def per_class(gold, pred, labels):
    """各類別的 precision / recall / F1 與 support。"""
    out = {}
    for lab in labels:
        tp = sum(1 for g, p in zip(gold, pred) if g == lab and p == lab)
        fp = sum(1 for g, p in zip(gold, pred) if g != lab and p == lab)
        fn = sum(1 for g, p in zip(gold, pred) if g == lab and p != lab)
        p, r, f = prf(tp, fp, fn)
        out[lab] = {"precision": p, "recall": r, "f1": f,
                    "support": sum(1 for g in gold if g == lab),
                    "tp": tp, "fp": fp, "fn": fn}
    return out


def compute(gold_levels, pred_levels):
    """對一版參考答案計算全部指標。"""
    g_i = [IDX[x] for x in gold_levels]
    p_i = [IDX[x] for x in pred_levels]
    n = len(g_i)

    exact = sum(1 for g, p in zip(g_i, p_i) if g == p)
    adjacent = sum(1 for g, p in zip(g_i, p_i) if abs(g - p) <= 1)
    mae = sum(abs(g - p) for g, p in zip(g_i, p_i)) / n

    # 只對「參考答案中實際出現的等級」算 macro，避免無樣本類別稀釋平均
    present = [l for l in LEVELS if l in set(gold_levels)]
    pc = per_class(gold_levels, pred_levels, LEVELS)
    macro_f1 = sum(pc[l]["f1"] for l in present) / len(present) if present else 0.0

    # 介入 binary
    g_b = [1 if v >= INTERVENE_FROM else 0 for v in g_i]
    p_b = [1 if v >= INTERVENE_FROM else 0 for v in p_i]
    tp = sum(1 for g, p in zip(g_b, p_b) if g == 1 and p == 1)
    fp = sum(1 for g, p in zip(g_b, p_b) if g == 0 and p == 1)
    fn = sum(1 for g, p in zip(g_b, p_b) if g == 1 and p == 0)
    tn = sum(1 for g, p in zip(g_b, p_b) if g == 0 and p == 0)
    bp, br, bf = prf(tp, fp, fn)

    over = sum(1 for g, p in zip(g_i, p_i) if p > g)
    under = sum(1 for g, p in zip(g_i, p_i) if p < g)

    return {
        "n": n,
        "accuracy": exact / n,
        "adjacent_accuracy": adjacent / n,
        "mae_levels": mae,
        "macro_f1": macro_f1,
        "weighted_kappa": weighted_kappa(g_i, p_i),
        "unweighted_kappa": weighted_kappa(g_i, p_i, quadratic=False),
        "per_class": pc,
        "levels_present_in_gt": present,
        "intervene": {
            "precision": bp, "recall": br, "f1": bf,
            "false_positive_rate": fp / (fp + tn) if (fp + tn) else 0.0,
            "false_negative_rate": fn / (fn + tp) if (fn + tp) else 0.0,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        },
        "direction": {"over": over, "under": under, "exact": exact,
                      "over_rate": over / n, "under_rate": under / n},
    }


# ---------------------------------------------------------------------------
# 分層與對照對診斷
# ---------------------------------------------------------------------------
def stratum_breakdown(rows, gt_key):
    out = {}
    by = defaultdict(list)
    for r in rows:
        by[r["stratum"]].append(r)
    for s, items in sorted(by.items()):
        correct = sum(1 for r in items if r["pred"] == r[gt_key])
        adj = sum(1 for r in items if abs(IDX[r["pred"]] - IDX[r[gt_key]]) <= 1)
        out[s] = {"n": len(items), "accuracy": correct / len(items),
                  "adjacent": adj / len(items),
                  "misses": [{"case_id": r["case_id"], "gold": r[gt_key], "pred": r["pred"]}
                             for r in items if r["pred"] != r[gt_key]]}
    return out


def contrast_pair_analysis(rows, cases, gt_key):
    """脈絡對照對：同一句話在不同前文下，系統是否給出不同判斷。

    這是「情境敏感度」最直接的證據——比整體 F1 更能證明系統不是關鍵詞比對。
    """
    pairs = defaultdict(list)
    for r in rows:
        c = cases.get(r["case_id"])
        if c and c["stratum"] == "context_contrast" and c.get("pair"):
            pairs[c["pair"].rsplit("-", 1)[0]].append((r, c))

    out, incomplete = [], []
    for name, items in sorted(pairs.items()):
        if len(items) != 2:
            # 對照對只剩一半（另一半屬別的 split，或該案跑失敗）——不可靜默略過
            incomplete.append((name, [r["case_id"] for r, _ in items]))
            continue
        (r1, c1), (r2, c2) = items
        gold_differ = r1[gt_key] != r2[gt_key]
        pred_differ = r1["pred"] != r2["pred"]
        gold_dir = IDX[r1[gt_key]] - IDX[r2[gt_key]]
        pred_dir = IDX[r1["pred"]] - IDX[r2["pred"]]
        out.append({
            "pair": name,
            "cases": [r1["case_id"], r2["case_id"]],
            "target_text": c1["target"]["content"],
            "gold": [r1[gt_key], r2[gt_key]],
            "pred": [r1["pred"], r2["pred"]],
            "gold_differ": gold_differ,
            "pred_differ": pred_differ,
            # 方向一致 = 系統不只「有差別」，且差在正確的方向
            "direction_correct": gold_dir * pred_dir > 0 if gold_differ else None,
            "both_exact": r1["pred"] == r1[gt_key] and r2["pred"] == r2[gt_key],
        })
    return out, incomplete


# ---------------------------------------------------------------------------
# 報告
# ---------------------------------------------------------------------------
def fmt(v, digits=3):
    return f"{v:.{digits}f}" if isinstance(v, float) else str(v)


def build_report(meta, rows, cases, results_by_gt, contrast, incomplete, strata, obs_diag):
    n = results_by_gt["gt_adjudicated"]["n"]
    one_case = 100.0 / n if n else 0
    L = []
    A = L.append

    A(f"# 評估報告：{meta.get('split')}"
      + (f"（{meta['tag']}）" if meta.get("tag") else ""))
    A("")
    A(f"> 執行時間：{meta.get('executed_at')}")
    A(f"> 案例數：{meta.get('n_success')} / {meta.get('n_cases')}"
      + (f"（失敗 {meta['n_failed']} 則）" if meta.get("n_failed") else ""))
    A("")
    A(f"⚠️ **樣本數警語**：n = {n}，**一則案例 = {one_case:.1f} 個百分點**。")
    A(f"前後對照若差距小於 {one_case*2:.0f} 個百分點（約 2 則案例），"
      "不足以宣稱有改善；差距達 15 個百分點以上才較有把握。")
    A("")
    A("---")
    A("")
    A("## 1. 主要指標")
    A("")
    A("所有指標對**兩版參考答案**各算一次。裁決者同時為系統設計者，"
      "此敏感度分析用以檢驗結論是否穩健：兩版結論一致代表結果可信。")
    A("")
    A("| 指標 | 裁決版 | 多數決版 | 說明 |")
    A("|---|---|---|---|")
    a, m = results_by_gt["gt_adjudicated"], results_by_gt["gt_majority"]
    A(f"| **Quadratic-weighted Kappa** | **{fmt(a['weighted_kappa'])}** | **{fmt(m['weighted_kappa'])}** | "
      "⭐ 主指標；可與標註者間一致性直接比較 |")
    A(f"| Unweighted Kappa | {fmt(a['unweighted_kappa'])} | {fmt(m['unweighted_kappa'])} | 完全命中才算對 |")
    A(f"| Accuracy | {fmt(a['accuracy'])} | {fmt(m['accuracy'])} | 五級完全命中率 |")
    A(f"| Adjacent Accuracy (±1) | {fmt(a['adjacent_accuracy'])} | {fmt(m['adjacent_accuracy'])} | 差一級內視為正確 |")
    A(f"| Macro-F1 | {fmt(a['macro_f1'])} | {fmt(m['macro_f1'])} | 僅計參考答案中出現的等級 |")
    A(f"| MAE（級距） | {fmt(a['mae_levels'])} | {fmt(m['mae_levels'])} | 平均錯幾級 |")
    A("")
    A("**對照基準**：標註者之間的 quadratic-weighted Cohen's Kappa 為"
      "**人 × LLM 0.897–0.913**、LLM 之間 0.915–0.985（見 `annotation-adjudication.md` §1）。"
      "若系統的 Kappa 接近人 × LLM 的水準，可主張其判斷與參考答案的一致程度已近似人類標註者。")
    A("")

    A("## 2. 介入判斷（binary，穩健備援）")
    A("")
    A("以 `warning` 以上視為需介入。此指標較五級穩健：即使系統對"
      "「warning 還是 restricted」判斷有偏差，「該不該出手」通常仍可靠。")
    A("")
    A("| 指標 | 裁決版 | 多數決版 | 意義 |")
    A("|---|---|---|---|")
    ai, mi = a["intervene"], m["intervene"]
    A(f"| Precision | {fmt(ai['precision'])} | {fmt(mi['precision'])} | 判需介入者中真正需要的比例 |")
    A(f"| Recall | {fmt(ai['recall'])} | {fmt(mi['recall'])} | 真正需介入者中被抓到的比例 |")
    A(f"| F1 | {fmt(ai['f1'])} | {fmt(mi['f1'])} | |")
    A(f"| **誤報率 FPR** | **{fmt(ai['false_positive_rate'])}** | **{fmt(mi['false_positive_rate'])}** | "
      "正常訊息被判需介入的比例 → **影響使用者體驗** |")
    A(f"| **漏報率 FNR** | **{fmt(ai['false_negative_rate'])}** | **{fmt(mi['false_negative_rate'])}** | "
      "真風險未被介入的比例 → **影響安全性** |")
    A("")
    A("> **兩者不可只看 F1**：安全系統的漏報後果重於誤報，損失函數本就不對稱"
      "（見 `risk-detection-rationale.md` §6）。")
    A("")

    A("## 3. 判斷方向")
    A("")
    A("| | 裁決版 | 多數決版 |")
    A("|---|---|---|")
    ad, md = a["direction"], m["direction"]
    A(f"| 完全正確 | {ad['exact']} | {md['exact']} |")
    A(f"| **高估**（判得比答案嚴重） | {ad['over']}（{fmt(ad['over_rate'])}） | {md['over']}（{fmt(md['over_rate'])}） |")
    A(f"| **低估**（判得比答案寬鬆） | {ad['under']}（{fmt(ad['under_rate'])}） | {md['under']}（{fmt(md['under_rate'])}） |")
    A("")

    A("## 4. 各等級表現（裁決版）")
    A("")
    A("| 等級 | Support | Precision | Recall | F1 |")
    A("|---|---|---|---|---|")
    for lab in LEVELS:
        c = a["per_class"][lab]
        note = "" if c["support"] else "　←　參考答案無此級樣本"
        A(f"| `{lab}` | {c['support']} | {fmt(c['precision'])} | {fmt(c['recall'])} | {fmt(c['f1'])}{note} |")
    A("")
    if obs_diag["predicted_count"]:
        A(f"**`observation` 診斷**：系統判了 {obs_diag['predicted_count']} 次，"
          f"對應的參考答案分佈為 {obs_diag['gold_distribution']}。")
        A("參考答案中無此級樣本，故其 precision／recall 無法評估——"
          "成因是單點標註無法承載「需觀察後續」的語意（見 `annotation-adjudication.md` §4）。")
    else:
        A("**`observation` 診斷**：系統未判過此級。")
    A("")

    A("## 5. 分層表現（裁決版）")
    A("")
    A("| 分層 | n | Accuracy | ±1 內 | 判錯的案例 |")
    A("|---|---|---|---|---|")
    for s, v in strata.items():
        miss = "、".join(f"{x['case_id']}({x['gold']}→{x['pred']})" for x in v["misses"]) or "—"
        A(f"| {s} | {v['n']} | {fmt(v['accuracy'])} | {fmt(v['adjacent'])} | {miss} |")
    A("")
    A("**重點分層的意義**：")
    A("- `context_contrast` — 情境敏感度（見 §6）")
    A("- `b4c_falsepos` — 敏感詞在求助／新聞情境是否被誤判")
    A("- `midnight` — 時段規則是否生效，且未僅因深夜就誤升")
    A("")

    A("## 6. 脈絡對照對分析 ⭐")
    A("")
    A("**同一句話、不同前文，系統是否給出不同判斷。**"
      "這是「系統依脈絡判斷而非關鍵詞比對」最直接的證據，比整體 F1 更能支持核心主張。")
    A("")
    if not contrast:
        A("（本次未包含脈絡對照對案例。）")
    else:
        ok = sum(1 for c in contrast if c["pred_differ"] and c["direction_correct"])
        A(f"**{ok} / {len(contrast)} 對**在正確方向上被區分開。")
        A("")
        A("| 對照組 | 目標訊息 | 參考答案 | 系統判斷 | 有區分 | 方向正確 |")
        A("|---|---|---|---|---|---|")
        for c in contrast:
            A(f"| {c['pair']} | 「{c['target_text'][:18]}」 | "
              f"{c['gold'][0]} / {c['gold'][1]} | {c['pred'][0]} / {c['pred'][1]} | "
              f"{'✅' if c['pred_differ'] else '❌'} | "
              f"{'✅' if c['direction_correct'] else ('❌' if c['gold_differ'] else '—')} |")
        A("")
        A("> **有區分 ❌** 代表系統對同一句話在兩種脈絡下給了相同判斷——"
          "即該案例上未能運用脈絡。**方向正確 ❌** 代表有區分但方向相反。")
    if incomplete:
        A("")
        A("⚠️ 下列對照對只有一半在本次結果中，無法比對：")
        for name, ids in incomplete:
            A(f"- `{name}`（只有 {', '.join(ids)}）——另一半屬別的 split，或該案執行失敗")
    A("")

    A("## 7. 逐案結果")
    A("")
    A("| 案例 | 分層 | 參考答案（裁決／多數決） | 系統 | 差距 | 延遲 |")
    A("|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: x["case_id"]):
        d = IDX[r["pred"]] - IDX[r["gt_adjudicated"]]
        mark = "✅" if d == 0 else ("⚠️" if abs(d) == 1 else "❌")
        A(f"| {r['case_id']} | {r['stratum']} | {r['gt_adjudicated']} / {r['gt_majority']} | "
          f"{r['pred']} | {mark} {d:+d} | {r.get('latency', '—')}s |")
    A("")
    A("完整內部軌跡（各引擎 delta、觸發規則、診斷分數）見預測結果 JSON 的 `trace` 欄位，"
      "供 dev 集錯誤歸責使用。")
    return "\n".join(L)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="評估指標計算")
    ap.add_argument("--predictions", required=True, help="results/ 下的預測結果檔名")
    ap.add_argument("--out", help="輸出報告檔名（預設與預測檔同名的 .md）")
    args = ap.parse_args()

    pred_path = RESULT_DIR / args.predictions
    if not pred_path.exists():
        pred_path = Path(args.predictions)
    if not pred_path.exists():
        print(f"找不到預測結果：{args.predictions}")
        print(f"可用的檔案：{[p.name for p in RESULT_DIR.glob('*.json')] if RESULT_DIR.exists() else '（無）'}")
        return 1

    with open(pred_path, encoding="utf-8") as f:
        data = json.load(f)
    with open(GT_PATH, encoding="utf-8") as f:
        gt = {g["case_id"]: g for g in json.load(f)["ground_truth"]}
    with open(CASE_POOL, encoding="utf-8") as f:
        cases = {c["case_id"]: c for c in json.load(f)["cases"]}

    rows, skipped = [], []
    for p in data["predictions"]:
        cid = p["case_id"]
        if cid not in gt:
            skipped.append(cid)
            continue
        if not p.get("predicted_level"):
            skipped.append(cid)
            continue
        rows.append({
            "case_id": cid,
            "stratum": p.get("stratum") or cases.get(cid, {}).get("stratum", "?"),
            "pred": p["predicted_level"],
            "gt_adjudicated": gt[cid]["gt_adjudicated"],
            "gt_majority": gt[cid]["gt_majority"],
            "latency": p.get("latency_seconds"),
        })

    if not rows:
        print("沒有可評估的案例。")
        if skipped:
            print(f"略過 {len(skipped)} 則（無參考答案或預測失敗）：{skipped[:10]}")
        return 1
    if skipped:
        print(f"⚠️ 略過 {len(skipped)} 則（無參考答案或預測失敗）：{', '.join(skipped[:10])}")

    preds = [r["pred"] for r in rows]
    results_by_gt = {
        k: compute([r[k] for r in rows], preds)
        for k in ("gt_adjudicated", "gt_majority")
    }

    obs_pred = [r for r in rows if r["pred"] == "observation"]
    obs_diag = {
        "predicted_count": len(obs_pred),
        "gold_distribution": dict(Counter(r["gt_adjudicated"] for r in obs_pred)),
    }

    strata = stratum_breakdown(rows, "gt_adjudicated")
    contrast, incomplete = contrast_pair_analysis(rows, cases, "gt_adjudicated")

    report = build_report(data["meta"], rows, cases, results_by_gt,
                          contrast, incomplete, strata, obs_diag)

    out_path = Path(args.out) if args.out else pred_path.with_suffix(".md")
    out_path.write_text(report, encoding="utf-8")

    a = results_by_gt["gt_adjudicated"]
    m = results_by_gt["gt_majority"]
    print()
    print("=" * 60)
    print(f"  n = {a['n']}　｜　一則案例 = {100/a['n']:.1f} 個百分點")
    print("=" * 60)
    print(f"  Weighted Kappa   裁決版 {a['weighted_kappa']:.3f}　多數決版 {m['weighted_kappa']:.3f}")
    print(f"  Accuracy         裁決版 {a['accuracy']:.3f}　多數決版 {m['accuracy']:.3f}")
    print(f"  Adjacent (±1)    裁決版 {a['adjacent_accuracy']:.3f}　多數決版 {m['adjacent_accuracy']:.3f}")
    print(f"  介入 F1          裁決版 {a['intervene']['f1']:.3f}　多數決版 {m['intervene']['f1']:.3f}")
    print(f"    誤報率 FPR     裁決版 {a['intervene']['false_positive_rate']:.3f}")
    print(f"    漏報率 FNR     裁決版 {a['intervene']['false_negative_rate']:.3f}")
    print(f"  高估 {a['direction']['over']} ／ 低估 {a['direction']['under']} ／ 正確 {a['direction']['exact']}")
    if contrast:
        ok = sum(1 for c in contrast if c["pred_differ"] and c["direction_correct"])
        print(f"  脈絡對照對       {ok} / {len(contrast)} 對在正確方向上被區分")
    print()
    print(f"完整報告：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
