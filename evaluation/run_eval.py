"""
評估執行腳本（replay）
======================
把案例池的案例重播進完整 pipeline，收集系統判斷與內部軌跡，供 metrics.py 計算指標。

用法（在 python_backend 目錄下，且風險服務需已啟動）
--------------------------------------------------
    venv/bin/python evaluation/run_eval.py --preflight              # 先驗 schema，不燒 LLM 額度
    venv/bin/python evaluation/run_eval.py --split test --tag baseline
    venv/bin/python evaluation/run_eval.py --split dev               # 調參用
    venv/bin/python evaluation/run_eval.py --split holdout           # ⚠️ 只在最終評估時跑一次
    venv/bin/python evaluation/run_eval.py --split test --dry-run    # 只印出將送出什麼

每則案例的處理流程
------------------
1. 產生獨立 conversation_id（累積狀態不跨案例污染）
2. **時間戳平移**：使 target 對齊「執行當下 + lead」，並保留 context 的相對間隔
3. **種入關係背景**：conversations／relationship_metrics／conversation_summaries
   ——**不產生假訊息**：messages 只寫入案例自身的 context_messages
4. 等到牆上時鐘走到對齊點，才呼叫 /detect
5. 帶入 message_timestamp（案例原始時間）供時段規則判定
6. 收集回應與內部軌跡；全部跑完後統一清除

以下設計依實際程式碼查證，改動前請先確認對應行號仍成立
------------------------------------------------------
* **conversations 的文件 ID 必須等於 conversation_id**
  —— `relationship_service.py:161` 用 `get_document(db_id, "conversations", conv_id)` 取角色，
  不是用欄位查詢。用 `ID.unique()` 會使角色查詢落入 except 分支而自動重建。

* **user_a = 收件方、user_b = 寄件方**
  —— 系統的 `conversation_balance = count_b / total`（`relationship_service.py:65`）。
  案例池的 `conversation_balance`（如 0.75）語意是「目標訊息發送方講得多」，
  故把寄件方放在 user_b，系統存的數值才會與標註者看到的數值一致。
  （現行所有消費端其實都對稱——熟悉度公式 `1-2*abs(b-0.5)`、`imbalance_min`、
  NLP prompt 皆然——但對齊數值可避免日後改成不對稱時默默失準。）

* **角色必須與 relationship_metrics 內的一致**
  —— `relationship_service.py:48` 一旦發現角色不符，會改以「實際 delivered 訊息」
  重算計數（`_recalculate_message_counts`），種入的 total_messages=300 會瞬間掉到個位數。

* **一定要種 conversation_summaries**
  —— 情境層的 `intimacy_min`／`intimacy_max` 讀 `last_summary.intimacy_level`
  （`scenario_risk_layer.py:114-119`）。不種就恆為 0.0，相關規則永遠不會觸發。
  且 `msg_count_snapshot` 要等於 total_messages，否則
  `risk_detection.py:59` 的 `total - snapshot >= 20` 會成立，
  觸發一次多餘的 L1 摘要 LLM 呼叫並覆寫種入的親密度。

* **關係指標在偵測當下不會被覆寫**
  —— `update_metrics` 只在 background task 中執行（`risk_detection.py:281`），
  在回應送出之後才跑。故 Step 1 讀到的就是種入的原值。
  也因為如此，**清除要等背景任務跑完**，否則會留下孤兒文件——
  本腳本一律延到全部跑完後統一清除。

* **時間對齊要留 lead**
  —— `temporal_feature_service.py:12` 以 `datetime.now()` 當作目前訊息的時間。
  種資料需要數秒，若以「送出前的當下」為平移基準，伺服器實際看到的間隔會被墊高。
  `frequency` 的斜率是 10s→1.0、180s→0.0（同檔 :85），5 秒偏移約影響 0.03；
  `message_burst_count` 更以 `interval <= 5` 判定（同檔 :48），偏移足以直接歸零。
  故先訂好對齊時刻，種完資料再等到該時刻才送出。
"""

import argparse
import json
import math
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
CASE_POOL = BASE_DIR / "case_pool.json"
RESULT_DIR = BASE_DIR / "results"

DEFAULT_API = "http://127.0.0.1:8000/api/v1/risk"   # main.py 的 uvicorn.run(port=8000)
DEFAULT_LEAD = 12.0   # 秒；預留給種資料的時間，需大於單案的種入耗時

CLEANUP_COLLECTIONS = [
    "messages", "temporal_features", "risk_analysis_logs_",
    "risk_state_history", "intervention_logs",
    "conversation_summaries", "relationship_metrics",
    "guardrail_context_reviews",
]


# ---------------------------------------------------------------------------
# 案例準備
# ---------------------------------------------------------------------------
def resolve_participants(case):
    """回傳 (sender, receiver)。receiver 取 context 中的另一方。"""
    sender = case["target"]["sender"]
    others = [m["sender"] for m in case["context_messages"] if m["sender"] != sender]
    return sender, (others[0] if others else "_receiver")


def plan_timeline(case, lead_seconds):
    """決定對齊時刻，並回傳平移後的 context 訊息。

    對齊時刻 = 現在 + lead。所有時間戳整體平移相同量，故相對間隔完整保留；
    絕對時刻（是否深夜）不靠平移保留，而是另以 message_timestamp 傳入原始時間。
    """
    align_at = datetime.now(timezone.utc) + timedelta(seconds=lead_seconds)
    target_ts = datetime.fromisoformat(case["target"]["timestamp"])
    delta = align_at - target_ts
    shifted = [
        {**m, "timestamp": (datetime.fromisoformat(m["timestamp"]) + delta).isoformat()}
        for m in case["context_messages"]
    ]
    return align_at, shifted


def expected_familiarity(total_messages, interaction_days, balance):
    """複算系統的熟悉度公式（relationship_service.py:185-191），用於核對種入值。"""
    msg_factor = min(1.0, math.log(1 + total_messages) / math.log(1 + 501))
    days_factor = min(1.0, math.log(1 + interaction_days) / math.log(1 + 91))
    balance_factor = 1 - 2 * abs(balance - 0.5)
    return round(0.40 * msg_factor + 0.40 * days_factor + 0.20 * balance_factor, 4)


# ---------------------------------------------------------------------------
# 種入與清除
# ---------------------------------------------------------------------------
class Seeder:
    def __init__(self):
        from app.services.chat_log_service import ChatLogService
        self.svc = ChatLogService()
        self.db = self.svc.db
        self.db_id = self.svc.db_id

    def seed(self, case, conv_id, shifted_messages, sender, receiver):
        from appwrite.id import ID

        facts = case["meta"].get("relationship_facts", {}) or {}
        seeded = case["meta"].get("seed_metrics", {}) or {}
        now_iso = datetime.now(timezone.utc).isoformat()

        total = facts.get("total_messages", len(shifted_messages) + 1)
        balance = facts.get("conversation_balance", 0.5)
        days = facts.get("interaction_days", 1)

        # user_b = 寄件方，使系統算出的 balance 與案例設計值一致（見檔頭）
        user_a, user_b = receiver, sender
        count_b = int(round(total * balance))
        count_a = total - count_b

        # conversations —— 文件 ID 必須是 conv_id
        self.db.create_document(self.db_id, "conversations", conv_id, {
            "user_a_id": user_a,
            "user_b_id": user_b,
            "last_activity": now_iso,
        })

        # relationship_metrics（L2）—— 直接種數值，不補假訊息
        self.db.create_document(self.db_id, "relationship_metrics", ID.unique(), {
            "conversation_id": conv_id,
            "user_a_id": user_a,
            "user_b_id": user_b,
            "total_messages": total,
            "user_a_message_count": count_a,
            "user_b_message_count": count_b,
            "familiarity_score": seeded.get("familiarity_score", 0.0),
            "conversation_balance": round(balance, 4),
            "interaction_days": days,
            "intimacy_progression_rate": seeded.get("intimacy_progression_rate", 0.0),
            "first_contact_at": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),
            "last_contact_at": now_iso,
            "updated_at": now_iso,
        })

        # conversation_summaries（L1）—— msg_count_snapshot=total 以抑制多餘的摘要觸發
        summary = case["meta"].get("relationship_summary") or {}
        self.db.create_document(self.db_id, "conversation_summaries", ID.unique(), {
            "conversation_id": conv_id,
            "summary_content": summary.get("content", ""),
            "main_topics": json.dumps(summary.get("main_topics", []), ensure_ascii=False),
            "tone_shift": summary.get("tone_shift", "stable"),
            "intimacy_level": seeded.get("intimacy_level", 0.0),
            "self_disclosure_depth": 0.0,
            "emotional_intensity": 0.0,
            "exclusivity_framing": 0.0,
            "physical_intimacy_reference": 0.0,
            "version": 1,
            "msg_count_snapshot": total,
            "updated_at": now_iso,
            "first_processed_msg_id": "",
            "last_processed_msg_id": "",
            "conversation_summaries_reasoning": "{}",
        })

        # messages —— 只寫案例自身的 context
        for m in shifted_messages:
            self.db.create_document(self.db_id, "messages", ID.unique(), {
                "conversation_id": conv_id,
                "sender_id": m["sender"],
                "content": m["content"],
                "timestamp": m["timestamp"],
                "is_blocked": False,
                "delivery_status": "delivered",
                "reviewed_at": m["timestamp"],
                "delivered_at": m["timestamp"],
            })

    def cleanup(self, conv_id):
        from appwrite.query import Query
        removed = 0
        for coll in CLEANUP_COLLECTIONS:
            try:
                while True:
                    res = self.db.list_documents(self.db_id, coll, queries=[
                        Query.equal("conversation_id", conv_id), Query.limit(100)
                    ])
                    if not res.documents:
                        break
                    for d in res.documents:
                        self.db.delete_document(
                            self.db_id, coll, d.id if hasattr(d, "id") else d["$id"])
                        removed += 1
            except Exception as e:
                print(f"      清除 {coll} 失敗（{conv_id}）：{e}")
        try:
            self.db.delete_document(self.db_id, "conversations", conv_id)
            removed += 1
        except Exception:
            pass
        return removed


# ---------------------------------------------------------------------------
# 單案執行
# ---------------------------------------------------------------------------
def run_case(case, api_base, seeder, lead, timeout=180):
    conv_id = f"eval_{case['case_id']}_{uuid.uuid4().hex[:8]}"
    sender, receiver = resolve_participants(case)
    align_at, shifted = plan_timeline(case, lead)

    payload = {
        "conversation_id": conv_id,
        "current_message": case["target"]["content"],
        "sender_id": sender,
        "receiver_id": receiver,
        # 時段規則以案例原始時間判定，不受執行時刻影響
        "message_timestamp": case["target"]["timestamp"],
    }

    t_seed = time.time()
    seeder.seed(case, conv_id, shifted, sender, receiver)
    seed_seconds = time.time() - t_seed

    # 等到對齊時刻才送出；若種資料超時，記錄偏移量而不假裝準確
    drift = (datetime.now(timezone.utc) - align_at).total_seconds()
    if drift < 0:
        time.sleep(-drift)
        drift = 0.0

    t0 = time.time()
    resp = requests.post(f"{api_base}/detect", json=payload, timeout=timeout)
    elapsed = time.time() - t0
    resp.raise_for_status()
    data = resp.json()

    return {
        "case_id": case["case_id"],
        "split": case["split"],
        "stratum": case["stratum"],
        "pair": case.get("pair"),
        "conversation_id": conv_id,
        "predicted_level": data.get("risk_level"),
        "should_intervene": data.get("should_intervene"),
        # NLP 降級的案例其語意通道恆為 0，等級多半落在 safe，
        # 與真正的 safe 無法從結果分辨。必須單獨記錄，計算指標時排除或另行標記。
        "nlp_degraded": data.get("nlp_degraded", False),
        "nlp_confidence": data.get("nlp_confidence"),
        "latency_seconds": round(elapsed, 2),
        "timing": {
            "seed_seconds": round(seed_seconds, 2),
            # > 0 代表種資料超過 lead，該案的行為特徵間隔被墊高了這麼多秒
            "alignment_drift_seconds": round(drift, 2),
        },
        "trace": {
            "delta_rule": data.get("risk_delta_rule"),
            "delta_nlp": data.get("risk_delta_nlp"),
            "delta_total": data.get("risk_delta_total"),
            "new_state": data.get("new_risk_state"),
            "triggered_rules": data.get("triggered_rules"),
            "diagnostic": data.get("diagnostic_signals"),
            "intervention": data.get("intervention_command"),
        },
        "raw": data,
    }


def preflight(seeder, api_base):
    """在燒掉 LLM 額度前，先驗證種入的欄位與 Appwrite schema 相符。"""
    print("Preflight：驗證種入欄位與 Appwrite schema")
    probe = {
        "case_id": "PREFLIGHT", "split": "-", "stratum": "-",
        "meta": {"relationship_facts": {"total_messages": 300, "interaction_days": 60,
                                        "conversation_balance": 0.75},
                 "seed_metrics": {"familiarity_score": 0.8307, "intimacy_level": 0.55,
                                  "intimacy_progression_rate": 0.02}},
        "context_messages": [{"sender": "B", "content": "測試",
                              "timestamp": "2026-07-19T20:00:00+08:00"}],
        "target": {"sender": "A", "content": "測試", "timestamp": "2026-07-19T20:00:30+08:00"},
    }
    conv_id = f"eval_preflight_{uuid.uuid4().hex[:8]}"
    _, shifted = plan_timeline(probe, 0)
    try:
        seeder.seed(probe, conv_id, shifted, "A", "B")
        print("  ✅ 四個 collection 皆可寫入")
    except Exception as e:
        print(f"  ❌ 種入失敗：{e}")
        print("     多半是 Appwrite 少了某個 attribute；請對照 relationship_service.py 的欄位補齊。")
        return False
    finally:
        n = seeder.cleanup(conv_id)
        print(f"  已清除 {n} 筆測試資料")

    try:
        requests.get(api_base.replace("/api/v1/risk", "/docs"), timeout=5)
        print(f"  ✅ 服務可連線：{api_base}")
    except Exception:
        print(f"  ❌ 無法連上 {api_base}；請先啟動：venv/bin/python main.py")
        return False
    return True


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="評估 replay 執行器")
    ap.add_argument("--split", choices=["dev", "test", "holdout"])
    ap.add_argument("--tag", default="", help="本次測量的標記（如 baseline / after-tuning）")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--lead", type=float, default=DEFAULT_LEAD,
                    help=f"種資料的預留秒數（預設 {DEFAULT_LEAD}）")
    ap.add_argument("--dry-run", action="store_true", help="只印出將送出的內容，不寫資料庫、不呼叫 API")
    ap.add_argument("--preflight", action="store_true", help="只驗證 schema 與連線")
    ap.add_argument("--limit", type=int, help="只跑前 N 則")
    ap.add_argument("--only", help="只跑指定案例（逗號分隔，如 H05 或 C01,C02）。"
                                   "用於補跑失敗案例，避免重跑整個 split")
    ap.add_argument("--no-cleanup", action="store_true", help="保留資料以便查驗（記得事後手動清）")
    args = ap.parse_args()

    if args.preflight:
        return 0 if preflight(Seeder(), args.api) else 1
    if not args.split:
        ap.error("需要 --split（或改用 --preflight）")

    with open(CASE_POOL, encoding="utf-8") as f:
        pool = json.load(f)
    cases = sorted([c for c in pool["cases"] if c["split"] == args.split],
                   key=lambda c: c["case_id"])
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        cases = [c for c in cases if c["case_id"] in want]
        missing = want - {c["case_id"] for c in cases}
        if missing:
            ap.error(f"這些案例不在 {args.split} 集內：{', '.join(sorted(missing))}")
    if args.limit:
        cases = cases[:args.limit]

    # 核對種入的熟悉度是否與系統公式一致——不一致代表案例池與實作已脫節
    drifted = []
    for c in cases:
        f_ = c["meta"].get("relationship_facts", {})
        s_ = c["meta"].get("seed_metrics", {})
        exp = expected_familiarity(f_.get("total_messages", 1),
                                   f_.get("interaction_days", 1),
                                   f_.get("conversation_balance", 0.5))
        if abs(exp - s_.get("familiarity_score", 0.0)) > 0.005:
            drifted.append((c["case_id"], s_.get("familiarity_score"), exp))
    if drifted:
        print("⚠️ 下列案例的 seed familiarity_score 與系統公式算出的值不符：")
        for cid, got, exp in drifted:
            print(f"     {cid}: 種入 {got} vs 公式 {exp}")
        print("   （系統在偵測當下讀的是種入值，故不影響本次結果，但代表案例池需要重算）\n")

    if args.split == "holdout" and not args.dry_run:
        print("⚠️  holdout 是最終封存集，只應在所有調校與功能開發完成後測【一次】。")
        print("    提前測量會使最終數字失去「未受重複測量影響」的性質。")
        if input("    確定要執行嗎？(輸入 yes 繼續): ").strip().lower() != "yes":
            print("已取消。")
            return 1

    if args.dry_run:
        for c in cases:
            sender, receiver = resolve_participants(c)
            _, shifted = plan_timeline(c, args.lead)
            print(f"{c['case_id']}  sender={sender} receiver={receiver}  "
                  f"種入 {len(shifted)} 則歷史  "
                  f"total_messages={c['meta']['relationship_facts'].get('total_messages')}  "
                  f"message_timestamp={c['target']['timestamp']}")
        print(f"\n乾跑完成（{len(cases)} 則），未寫入資料庫、未呼叫 API。")
        return 0

    seeder = Seeder()
    if not preflight(seeder, args.api):
        return 1

    print(f"\n案例：{len(cases)} 則（split={args.split}）"
          + (f"　標記：{args.tag}" if args.tag else ""))
    print(f"每則預留 {args.lead}s 對齊時間戳，故總時長約 "
          f"{len(cases) * (args.lead + 8) / 60:.0f} 分鐘\n")

    results, failures, conv_ids = [], [], []
    for i, case in enumerate(cases, 1):
        cid = case["case_id"]
        try:
            r = run_case(case, args.api, seeder, args.lead)
            results.append(r)
            conv_ids.append(r["conversation_id"])
            warn = ""
            if r.get("nlp_degraded"):
                warn += "　⚠️ NLP 降級（本則語意通道為 0，不應計入指標）"
            if r["timing"]["alignment_drift_seconds"] > 1:
                warn += f"　⚠️ 對齊偏移 {r['timing']['alignment_drift_seconds']}s"
            print(f"  [{i}/{len(cases)}] {cid}  -> {str(r['predicted_level']):11} "
                  f"({r['latency_seconds']}s){warn}")
        except Exception as e:
            failures.append({"case_id": cid, "error": str(e)})
            print(f"  [{i}/{len(cases)}] {cid}  -> 失敗：{e}")

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    path = RESULT_DIR / f"{args.split}_{args.tag or 'run'}_{stamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "meta": {
                "split": args.split, "tag": args.tag,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "n_cases": len(cases), "n_success": len(results), "n_failed": len(failures),
                "api": args.api, "lead_seconds": args.lead,
            },
            "predictions": results,
            "failures": failures,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n完成 {len(results)} 則，失敗 {len(failures)} 則")
    degraded = [r["case_id"] for r in results if r.get("nlp_degraded")]
    if degraded:
        print(f"⚠️ NLP 降級 {len(degraded)} 則：{', '.join(degraded)}")
        print("   這些案例的語意通道恆為 0，判斷不完整；計算指標前應排除或另行標記。")
    print(f"結果：{path}")

    if args.no_cleanup:
        print(f"\n⚠️ 已保留 {len(conv_ids)} 筆對話資料，事後請自行清除。")
    elif conv_ids:
        # 背景任務（關係指標更新、摘要、背景判斷）在回應之後才跑，先等它們收尾
        print(f"\n等待背景任務收尾後清除 {len(conv_ids)} 筆對話…")
        time.sleep(10)
        total = sum(seeder.cleanup(c) for c in conv_ids)
        print(f"已清除 {total} 筆文件")

    print(f"\n下一步計算指標：")
    print(f"  venv/bin/python evaluation/metrics.py --predictions {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
