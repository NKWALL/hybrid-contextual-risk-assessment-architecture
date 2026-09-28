"""
標註執行腳本
============
把 case_pool.json 中 split=test / holdout 的案例（共 30 則），交給多個標註者各自
獨立標註，結果存成統一格式，供 kappa.py 計算標註者間一致性。

判準來自 docs/current-specs/annotation-rubric.md（人與 LLM 共用同一份）。

用法（在 python_backend 目錄下）
--------------------------------
列出可用標註者與目前進度：
    venv/bin/python evaluation/annotate.py --status

用某個 LLM 標註（可重複執行，已標過的會跳過，中斷可續跑）：
    venv/bin/python evaluation/annotate.py --rater gemini
    venv/bin/python evaluation/annotate.py --rater ollama

產生給人類填寫的表單（CSV，可用 Excel 開）：
    venv/bin/python evaluation/annotate.py --make-form human_author

把填好的表單匯入成標註檔：
    venv/bin/python evaluation/annotate.py --import-form evaluation/forms/human_author.csv

設計要點
--------
* 每個標註者**獨立**標註，不會看到其他標註者的結果（避免互相污染）。
* 溫度固定、逐案獨立呼叫，原始輸出全部存檔以供稽核與重跑。
* `intervene` 不由標註者填寫，由 kappa.py 從 risk_level 換算（避免同一判斷標兩次而自相矛盾）。
* 只讀 case_pool.json，不寫入；不觸碰風險偵測 pipeline 與任何資料庫。
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent
CASE_POOL = BASE_DIR / "case_pool.json"
RUBRIC = PROJECT_ROOT / "docs" / "current-specs" / "annotation-rubric.md"
ANNOT_DIR = BASE_DIR / "annotations"
RAW_DIR = ANNOT_DIR / "raw"
FORM_DIR = BASE_DIR / "forms"

VALID_LEVELS = ["safe", "observation", "warning", "restricted", "blocked"]
VALID_DIMS = [
    "sexual_boundary", "coercion", "manipulation",
    "harassment", "emotional_pressure", "none",
]

# 要標註的切分：dev 由作者自行標註即可，不進 Kappa
TARGET_SPLITS = {"test", "holdout"}


# ---------------------------------------------------------------------------
# 標註者設定
# ---------------------------------------------------------------------------
# Ollama Cloud 標註者以環境變數 ANNOTATOR_MODELS 指定，逗號分隔，例如：
#     ANNOTATOR_MODELS=qwen3.5:397b,glm-5.2,gpt-oss:120b
# 每個模型會成為一位獨立標註者，rater_id 由模型名轉換而來（: 與 . 轉為 _）。
#
# 標註者的獨立性：請避免選用與系統本身相同或同家族的模型（本系統 NLP 引擎用
# Gemini、背景判斷器用 gemma4:e2b），否則等同「系統的一部分在幫自己出解答」，
# 一致性會虛高。建議挑三個不同廠系的模型。
OLLAMA_CLOUD_URL = "https://ollama.com/v1"
DEFAULT_ANNOTATOR_MODELS = "qwen3.5:397b,glm-5.2,gpt-oss:120b"


def _rater_id_from_model(model):
    return "m_" + model.replace(":", "_").replace(".", "_").replace("/", "_")


def rater_configs():
    """回傳可用的 LLM 標註者設定。以環境變數覆寫，避免與 pipeline 設定互相干擾。"""
    cfgs = {}

    # --- Ollama Cloud（主要標註者來源）---
    ollama_key = os.getenv("ANNOTATOR_OLLAMA_API_KEY") or os.getenv("OLLAMA_API_KEY")
    ollama_url = os.getenv("ANNOTATOR_OLLAMA_BASE_URL", OLLAMA_CLOUD_URL)
    models = [m.strip() for m in os.getenv("ANNOTATOR_MODELS", DEFAULT_ANNOTATOR_MODELS).split(",") if m.strip()]
    for model in models:
        cfgs[_rater_id_from_model(model)] = {
            "kind": "openai_compat",
            "base_url": ollama_url,
            "api_key": ollama_key,
            "model": model,
            "source": "ollama_cloud",
        }

    # --- Gemini（選用；注意勿與 NLP 引擎同型號）---
    gemini_model = os.getenv("ANNOTATOR_GEMINI_MODEL")
    if gemini_model:
        cfgs["gemini"] = {"kind": "gemini", "model": gemini_model, "source": "gemini"}

    # --- 任意 OpenAI 相容端點（選用，例如 Groq）---
    extra_url = os.getenv("ANNOTATOR_EXTRA_BASE_URL")
    extra_model = os.getenv("ANNOTATOR_EXTRA_MODEL")
    if extra_url and extra_model:
        cfgs["extra"] = {
            "kind": "openai_compat",
            "base_url": extra_url,
            "api_key": os.getenv("ANNOTATOR_EXTRA_API_KEY"),
            "model": extra_model,
            "source": "extra",
        }

    return cfgs


def build_adapter(cfg):
    from app.core.llm_adapters import GeminiAdapter, OpenAICompatAdapter

    if cfg["kind"] == "gemini":
        return GeminiAdapter()
    if not cfg.get("api_key"):
        raise RuntimeError(
            "缺少 api_key。Ollama Cloud 請設定 OLLAMA_API_KEY（或 ANNOTATOR_OLLAMA_API_KEY）"
        )
    return OpenAICompatAdapter(base_url=cfg["base_url"], api_key=cfg["api_key"])


def probe_rater(rater_id, cfg, timeout=20):
    """實際發一個極短請求，確認該標註者真的可用。回傳 (ok, 說明)。"""
    if cfg["kind"] != "gemini" and not cfg.get("api_key"):
        return False, "缺 api_key"
    try:
        adapter = build_adapter(cfg)
        resp = adapter.generate("請只回覆兩個字：可用", model=cfg["model"])
        text = (resp or "").strip().replace("\n", " ")
        return True, f"連線正常（回應：{text[:20]}）"
    except Exception as e:
        msg = str(e)
        return False, msg[:90]


# ---------------------------------------------------------------------------
# 載入與呈現
# ---------------------------------------------------------------------------
def load_cases():
    with open(CASE_POOL, encoding="utf-8") as f:
        pool = json.load(f)
    cases = [c for c in pool["cases"] if c["split"] in TARGET_SPLITS]
    cases.sort(key=lambda c: c["case_id"])
    return cases


def load_rubric():
    if not RUBRIC.exists():
        raise FileNotFoundError(f"找不到標註準則：{RUBRIC}")
    return RUBRIC.read_text(encoding="utf-8")


STAGE_LABELS = {
    "strangers_new": "剛認識",
    "acquainted": "熟人",
    "familiar_intimate": "熟識",
}


def _describe_balance(b):
    """把對話平衡度轉成標註者看得懂的描述。"""
    if b is None:
        return None
    imbalance = abs(b - 0.5)
    if imbalance < 0.08:
        return "雙方發言量相當"
    side = "目標訊息的發送方" if b > 0.5 else "另一方"
    if imbalance < 0.2:
        return f"{side}發言略多"
    return f"{side}發言明顯偏多"


def render_case(case):
    """把案例轉成標註者看到的文字。

    刻意排除：intended 標籤、split，以及 meta.seed_metrics。
    seed_metrics（familiarity_score / intimacy_level）是系統以自身公式算出的中間結論，
    不是證據；若提供給標註者，等同讓標註者接收系統的判斷，並使標註答案綁定於一個
    尚待校準的公式尺度（見 known-issues #14）。標註者應僅依「事實」自行判斷。
    """
    lines = [f"案例編號：{case['case_id']}"]
    meta = case.get("meta", {})

    stage = meta.get("relationship_stage")
    facts = meta.get("relationship_facts") or {}
    if facts:
        parts = []
        if facts.get("total_messages") is not None:
            parts.append(f"雙方至今累積約 {facts['total_messages']} 則訊息")
        if facts.get("interaction_days") is not None:
            parts.append(f"認識約 {facts['interaction_days']} 天")
        desc = _describe_balance(facts.get("conversation_balance"))
        if desc:
            parts.append(desc)
        label = f"（{STAGE_LABELS.get(stage, stage)}）" if stage else ""
        lines.append(f"關係背景{label}：" + "、".join(parts))
    elif stage:
        lines.append(f"關係背景：{STAGE_LABELS.get(stage, stage)}")
    lines.append("")
    lines.append("【對話歷史】（依時間排序）")
    for m in case["context_messages"]:
        lines.append(f"  [{m['timestamp']}] {m['sender']}：{m['content']}")
    t = case["target"]
    lines.append("")
    lines.append("【要判斷的目標訊息】")
    lines.append(f"  [{t['timestamp']}] {t['sender']}：{t['content']}")
    lines.append("")
    lines.append(f"請判斷發送方 {t['sender']} 的這則目標訊息，對接收方構成的風險等級。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 標註檔讀寫
# ---------------------------------------------------------------------------
def annot_path(rater_id):
    return ANNOT_DIR / f"{rater_id}.json"


def load_annotations(rater_id):
    p = annot_path(rater_id)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return {a["case_id"]: a for a in json.load(f)}


def save_annotations(rater_id, records):
    ANNOT_DIR.mkdir(parents=True, exist_ok=True)
    ordered = [records[k] for k in sorted(records)]
    with open(annot_path(rater_id), "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)


def normalize(rec, case_id, rater_id):
    """驗證並正規化一筆標註。回傳 (record, error)。"""
    level = str(rec.get("risk_level", "")).strip().lower()
    if level not in VALID_LEVELS:
        return None, f"risk_level 不合法：{rec.get('risk_level')!r}"

    dim = str(rec.get("primary_dimension", "none")).strip().lower()
    if dim not in VALID_DIMS:
        dim = "none"

    sec = rec.get("secondary_dimensions") or []
    if not isinstance(sec, list):
        sec = []
    sec = [str(s).strip().lower() for s in sec if str(s).strip().lower() in VALID_DIMS]

    return {
        "case_id": case_id,
        "rater_id": rater_id,
        "risk_level": level,
        "primary_dimension": dim,
        "secondary_dimensions": sec,
        "note": str(rec.get("note", "")).strip(),
    }, None


def parse_json_response(text):
    """從 LLM 回應中取出第一個 JSON 物件。"""
    import re

    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        raise ValueError("回應中找不到 JSON")
    return json.loads(m.group(0))


# ---------------------------------------------------------------------------
# LLM 標註
# ---------------------------------------------------------------------------
def run_llm_rater(rater_id, limit=None, sleep=0.0):
    cfgs = rater_configs()
    if rater_id not in cfgs:
        print(f"未知的標註者：{rater_id}（可用：{', '.join(cfgs)}）")
        return 1

    cfg = cfgs[rater_id]
    try:
        adapter = build_adapter(cfg)
    except Exception as e:
        print(f"標註者 {rater_id} 初始化失敗：{e}")
        return 1

    rubric = load_rubric()
    cases = load_cases()
    done = load_annotations(rater_id)
    todo = [c for c in cases if c["case_id"] not in done]
    if limit:
        todo = todo[:limit]

    print(f"標註者：{rater_id}（模型 {cfg['model']}）")
    print(f"總案例 {len(cases)} 則，已完成 {len(done)} 則，本次要標 {len(todo)} 則")
    if not todo:
        print("沒有待標註的案例。")
        return 0

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ok = fail = 0

    for i, case in enumerate(todo, 1):
        cid = case["case_id"]
        prompt = (
            f"{rubric}\n\n"
            "=====================================\n"
            "以上是標註準則。請依據該準則，標註以下案例。\n"
            "=====================================\n\n"
            f"{render_case(case)}\n\n"
            "請**只輸出**符合準則 §6 格式的 JSON，不要加入 markdown 標記或任何說明文字。\n"
            f'其中 rater_id 請填 "{rater_id}"，case_id 請填 "{cid}"。'
        )
        try:
            resp = adapter.generate(prompt, model=cfg["model"])
            (RAW_DIR / f"{rater_id}__{cid}.txt").write_text(resp or "", encoding="utf-8")
            rec, err = normalize(parse_json_response(resp), cid, rater_id)
            if err:
                raise ValueError(err)
            done[cid] = rec
            save_annotations(rater_id, done)
            ok += 1
            print(f"  [{i}/{len(todo)}] {cid} -> {rec['risk_level']} / {rec['primary_dimension']}")
        except Exception as e:
            fail += 1
            print(f"  [{i}/{len(todo)}] {cid} -> 失敗：{e}")
        if sleep:
            time.sleep(sleep)

    print(f"\n完成 {ok} 則，失敗 {fail} 則。結果：{annot_path(rater_id)}")
    if fail:
        print("失敗的案例可直接重跑本指令（已完成的會自動跳過）。")
    return 0


# ---------------------------------------------------------------------------
# 人工標註表單
# ---------------------------------------------------------------------------
def make_form(rater_id):
    """產生人工標註表單。優先產生帶下拉選單的 xlsx，無 openpyxl 時退回 csv。"""
    cases = load_cases()
    FORM_DIR.mkdir(parents=True, exist_ok=True)

    try:
        path = _make_form_xlsx(rater_id, cases)
    except ImportError:
        path = _make_form_csv(rater_id, cases)
        print("（未安裝 openpyxl，改產生 CSV。若要下拉選單版：venv/bin/pip install openpyxl）")

    print(f"已產生表單：{path}")
    print(f"共 {len(cases)} 則案例。必填 risk_level 與 primary_dimension 兩欄：")
    print(f"  risk_level        ：{' / '.join(VALID_LEVELS)}")
    print(f"  primary_dimension ：{' / '.join(VALID_DIMS)}")
    print("  secondary_dimensions：可留空，多個以分號分隔")
    print("  note：判斷理由（難以判定的案例請務必填寫）")
    print(f"\n填好後匯入：venv/bin/python evaluation/annotate.py --import-form {path}")
    return 0


def _make_form_csv(rater_id, cases):
    path = FORM_DIR / f"{rater_id}.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "對話內容（唯讀）", "risk_level", "primary_dimension",
                    "secondary_dimensions", "note"])
        for c in cases:
            w.writerow([c["case_id"], render_case(c), "", "", "", ""])
    return path


def _make_form_xlsx(rater_id, cases):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    path = FORM_DIR / f"{rater_id}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "標註"

    FONT = "Arial"
    header_fill = PatternFill("solid", fgColor="D9D9D9")
    input_fill = PatternFill("solid", fgColor="FFFF99")  # 黃底 = 請填寫

    headers = ["case_id", "對話內容（唯讀）", "risk_level", "primary_dimension",
               "secondary_dimensions", "note"]
    ws.append(headers)
    for i in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=i)
        c.font = Font(name=FONT, bold=True)
        c.fill = header_fill
        c.alignment = Alignment(vertical="center")
    ws.freeze_panes = "C2"

    for case in cases:
        ws.append([case["case_id"], render_case(case), "", "", "", ""])

    n = len(cases) + 1
    for row in range(2, n + 1):
        for col in range(1, len(headers) + 1):
            c = ws.cell(row=row, column=col)
            c.font = Font(name=FONT)
            c.alignment = Alignment(vertical="top", wrap_text=(col == 2))
        for col in (3, 4, 5, 6):
            ws.cell(row=row, column=col).fill = input_fill
        ws.row_dimensions[row].height = 150

    widths = {"A": 10, "B": 78, "C": 15, "D": 22, "E": 22, "F": 40}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    # 下拉選單（放在 C、D 欄）
    dv_level = DataValidation(
        type="list", formula1='"' + ",".join(VALID_LEVELS) + '"',
        allow_blank=True, showDropDown=False,
    )
    dv_level.error = "請從清單選擇：" + " / ".join(VALID_LEVELS)
    dv_level.errorTitle = "風險等級不合法"
    dv_level.prompt = "safe / observation / warning / restricted / blocked"
    dv_level.promptTitle = "風險等級"
    ws.add_data_validation(dv_level)
    dv_level.add(f"C2:C{n}")

    dv_dim = DataValidation(
        type="list", formula1='"' + ",".join(VALID_DIMS) + '"',
        allow_blank=True, showDropDown=False,
    )
    dv_dim.error = "請從清單選擇維度代碼"
    dv_dim.errorTitle = "風險維度不合法"
    dv_dim.prompt = "判為 safe 時填 none"
    dv_dim.promptTitle = "主要風險維度"
    ws.add_data_validation(dv_dim)
    dv_dim.add(f"D2:D{n}")

    # 說明分頁
    guide = wb.create_sheet("填寫說明")
    rows = [
        ("填寫說明", ""),
        ("", ""),
        ("必填欄位", "risk_level（C 欄）、primary_dimension（D 欄）"),
        ("選填欄位", "secondary_dimensions（多個以分號分隔）、note（判斷理由）"),
        ("黃底儲存格", "代表需要你填寫的欄位"),
        ("", ""),
        ("風險等級（五選一）", ""),
        ("safe", "正常健康互動，無風險訊號 → 系統不做任何事"),
        ("observation", "輕微苗頭，值得系統默默記著 → 不對使用者顯示任何東西"),
        ("warning", "明顯越界／施壓，接收方可能已不舒服 → 顯示提醒"),
        ("restricted", "高風險，具傷害性或持續壓迫 → 限制互動＋強提醒（訊息仍送達）"),
        ("blocked", "嚴重明確的傷害／威脅 → 直接攔截，不送達"),
        ("", ""),
        ("風險維度（判斷關鍵）", ""),
        ("sexual_boundary", "是否將互動推向性領域或私密空間"),
        ("coercion", "是否存在「不照做會有後果」"),
        ("manipulation", "是否在改變「對方怎麼理解這段關係」"),
        ("harassment", "是否忽視對方界線並持續干擾"),
        ("emotional_pressure", "是否透過情緒負擔而非威脅來促使讓步"),
        ("none", "判為 safe 時填此值"),
        ("", ""),
        ("常見誤判提醒", "「罪惡感綁架」「情緒勒索」屬 emotional_pressure，不是 manipulation"),
        ("", ""),
        ("關係階段（案例已標示）", ""),
        ("剛認識", "近期配對、互動次數少。此階段對親密／性相關語句的容忍度最低"),
        ("熟人", "已有一段時間往來、互動穩定"),
        ("熟識", "長期且雙向互動、關係親近。對曖昧語句容忍度較高，但明確越界不因熟識而降級"),
        ("", ""),
        ("判斷原則", "1. 看脈絡不看單句：同一句話在不同前文下風險天差地別"),
        ("", "2. 累積 > 單點：風險常來自模式而非單一字眼"),
        ("", "3. 關係階段會改變基準"),
        ("", "4. 脈絡可以降險：敏感詞出現在新聞／求助／討論情境，風險應低"),
        ("", "5. 判斷的是「發送方行為對接收方的風險」，不是有沒有惡意"),
        ("", ""),
        ("完整準則", "docs/current-specs/annotation-rubric.md"),
    ]
    for label, desc in rows:
        guide.append([label, desc])
    for row in guide.iter_rows():
        for c in row:
            c.font = Font(name=FONT, bold=(c.column == 1 and c.row in (1, 7, 14, 22, 24)))
            c.alignment = Alignment(vertical="top", wrap_text=True)
    guide.column_dimensions["A"].width = 22
    guide.column_dimensions["B"].width = 82

    wb.save(path)
    return path


def _read_form_rows(path):
    """讀取表單，回傳 list[dict]。支援 .xlsx 與 .csv。"""
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook

        wb = load_workbook(path, data_only=True)
        ws = wb["標註"] if "標註" in wb.sheetnames else wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else "" for h in next(rows)]
        out = []
        for r in rows:
            out.append({h: ("" if v is None else str(v)) for h, v in zip(headers, r)})
        return out

    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def import_form(path):
    path = Path(path)
    if not path.exists():
        print(f"找不到檔案：{path}")
        return 1

    rater_id = path.stem
    valid_ids = {c["case_id"] for c in load_cases()}
    records, errors, skipped = {}, [], 0

    for row in _read_form_rows(path):
        cid = (row.get("case_id") or "").strip()
        if not cid:
            continue
        if cid not in valid_ids:
            errors.append(f"{cid}：不在待標註清單中")
            continue
        if not (row.get("risk_level") or "").strip():
            skipped += 1
            continue
        sec = [s.strip() for s in (row.get("secondary_dimensions") or "").replace("，", ";").split(";") if s.strip()]
        rec, err = normalize(
            {
                "risk_level": row.get("risk_level"),
                "primary_dimension": row.get("primary_dimension"),
                "secondary_dimensions": sec,
                "note": row.get("note"),
            },
            cid, rater_id,
        )
        if err:
            errors.append(f"{cid}：{err}")
        else:
            records[cid] = rec

    if records:
        save_annotations(rater_id, records)
        print(f"已匯入 {len(records)} 則 -> {annot_path(rater_id)}")
    if skipped:
        print(f"略過 {skipped} 則（risk_level 未填）")
    if errors:
        print(f"\n{len(errors)} 則有問題：")
        for e in errors:
            print(f"  {e}")
    missing = valid_ids - set(records)
    if missing:
        print(f"\n尚未標註 {len(missing)} 則：{', '.join(sorted(missing))}")
    return 0


# ---------------------------------------------------------------------------
def list_models():
    """列出 Ollama Cloud 目前可用的模型。"""
    import urllib.request

    url = os.getenv("ANNOTATOR_OLLAMA_BASE_URL", OLLAMA_CLOUD_URL).rstrip("/") + "/models"
    key = os.getenv("ANNOTATOR_OLLAMA_API_KEY") or os.getenv("OLLAMA_API_KEY")
    req = urllib.request.Request(url)
    if key:
        req.add_header("Authorization", f"Bearer {key}")

    # macOS 的 Python 預設不使用系統憑證庫，明確指定 certifi 以免 SSL 驗證失敗
    ctx = None
    try:
        import ssl

        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass

    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            data = json.load(r)
    except Exception as e:
        print(f"取得模型清單失敗：{e}")
        return 1

    ids = sorted(m.get("id", "") for m in data.get("data", []))
    print(f"{url} 可用模型（{len(ids)} 個）：\n")
    for m in ids:
        print(f"  {m}")
    print("\n挑選建議：")
    print("  ─ 選 3 個**不同廠系**的模型，避免同源導致一致性虛高")
    print("  ─ 避開與系統本身同家族者（本系統背景判斷器用 gemma4 家族）")
    print("\n設定方式（寫進 .env）：")
    print("  ANNOTATOR_MODELS=模型1,模型2,模型3")
    return 0


def show_status(probe=False):
    cases = load_cases()
    total = len(cases)
    by_split = {}
    for c in cases:
        by_split[c["split"]] = by_split.get(c["split"], 0) + 1

    print(f"待標註案例：{total} 則 " + "（" + "、".join(f"{k} {v}" for k, v in sorted(by_split.items())) + "）")
    print(f"案例池：{CASE_POOL}")
    print(f"標註準則：{RUBRIC}")

    cfgs = rater_configs()
    print(f"\nLLM 標註者（{len(cfgs)} 位）：")
    if not cfgs:
        print("  （未設定。請在 .env 設 ANNOTATOR_MODELS）")
    for rid, cfg in cfgs.items():
        n = len(load_annotations(rid))
        if probe:
            ok, msg = probe_rater(rid, cfg)
            state = ("✅ " if ok else "❌ ") + msg
        else:
            state = "設定完整" if (cfg["kind"] == "gemini" or cfg.get("api_key")) else "缺 api_key"
        print(f"  {rid:22} {cfg['model']:22} 進度 {n:>2}/{total}  [{state}]")

    if not probe:
        print("\n  （以上僅檢查設定是否填齊，不代表服務可連線。")
        print("    加 --probe 會實際發一個測試請求確認。）")

    others = []
    if ANNOT_DIR.exists():
        known = set(cfgs)
        others = sorted(p.stem for p in ANNOT_DIR.glob("*.json") if p.stem not in known)
    if others:
        print("\n其他標註檔（人工等）：")
        for rid in others:
            print(f"  {rid:22} {'':22} 進度 {len(load_annotations(rid)):>2}/{total}")

    print("\n下一步：")
    print("  看可用模型      ：venv/bin/python evaluation/annotate.py --list-models")
    print("  實際連線測試    ：venv/bin/python evaluation/annotate.py --status --probe")
    if cfgs:
        first = next(iter(cfgs))
        print(f"  小量試跑        ：venv/bin/python evaluation/annotate.py --rater {first} --limit 3")
        print(f"  正式標註        ：venv/bin/python evaluation/annotate.py --rater {first}")
    print("  產生人工表單    ：venv/bin/python evaluation/annotate.py --make-form human_author")
    print("  全部標完後算 Kappa：venv/bin/python evaluation/kappa.py")
    return 0


def main():
    ap = argparse.ArgumentParser(description="評估案例標註工具")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true", help="顯示標註進度")
    g.add_argument("--list-models", action="store_true", help="列出 Ollama Cloud 可用模型")
    g.add_argument("--rater", metavar="ID", help="以指定 LLM 標註者執行標註")
    g.add_argument("--make-form", metavar="ID", help="產生人工標註表單（CSV）")
    g.add_argument("--import-form", metavar="PATH", help="匯入填好的表單")
    ap.add_argument("--probe", action="store_true", help="搭配 --status：實際測試每位標註者可否連線")
    ap.add_argument("--limit", type=int, help="本次最多標註幾則（測試用）")
    ap.add_argument("--sleep", type=float, default=0.0, help="每則之間暫停秒數（避開速率限制）")
    args = ap.parse_args()

    if args.list_models:
        return list_models()
    if args.status:
        return show_status(probe=args.probe)
    if args.rater:
        return run_llm_rater(args.rater, limit=args.limit, sleep=args.sleep)
    if args.make_form:
        return make_form(args.make_form)
    return import_form(args.import_form)


if __name__ == "__main__":
    sys.exit(main())
