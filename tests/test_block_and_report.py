"""封鎖、檢舉與行動按鈕清單的測試（2026-08-31）。

背景：intervention-ux-spec.md §4 在三處畫了行動列，但 8/22 交付時後端
既沒有按鈕清單的契約、也沒有封鎖與檢舉的儲存與端點——那三顆按鈕
（封鎖／檢舉／停止對話）按下去不會有任何事發生，而 README D10 的措辭
（「需要你們補一個常駐入口」）還讓人以為功能已存在。

本檔鎖住三件事：
  1. `action_options` 依等級送出**不同**的按鈕組（三組確實不同）。
  2. 封鎖是冪等的、雙向的、可逆的。
  3. 檢舉不去重（重複發生本身是訊號），且理由分類受限於五維度＋other。

（本專案未安裝 pytest-asyncio，沿用既有測試的 asyncio.run 寫法。）
"""

import asyncio

import pytest
from unittest.mock import patch

from app.core.intervention_engine import InterventionEngine


RESTRICTED_OPTS = [
    {"action": "block_user", "label": "封鎖"},
    {"action": "report_user", "label": "檢舉"},
    {"action": "leave_conversation", "label": "停止對話"},
]
BLOCKED_OPTS = [
    {"action": "dismiss", "label": "繼續對話"},
    {"action": "block_user", "label": "封鎖"},
    {"action": "report_user", "label": "檢舉"},
    {"action": "leave_conversation", "label": "結束對話"},
]

TEMPLATES = {
    "restricted": [
        {"template_id": "restrict_receiver_options", "primary_risk_type": "any",
         "action_type": "show_safety_info_card",
         "message_template": {"title": "已加強保護這段對話", "body": "…"},
         "ui_behavior": {"show_options": True},
         "action_options": RESTRICTED_OPTS},
        {"template_id": "restrict_sender_general", "primary_risk_type": "any",
         "action_type": "show_modal_warning",
         "message_template": {"title": "請暫停一下", "body": "…"},
         "ui_behavior": {"cooldown": 60, "require_ack": True},
         "action_options": None},
    ],
    "blocked": [
        {"template_id": "block_receiver_notice", "primary_risk_type": "any",
         "action_type": "show_blocked_notice",
         "message_template": {"body": "系統攔截了一則…"},
         "ui_behavior": {"show_options": True},
         # 刻意用字串，模擬 MySQL 直接回傳的 JSON 欄位
         "action_options": '[{"action":"dismiss","label":"繼續對話"},'
                           '{"action":"block_user","label":"封鎖"},'
                           '{"action":"report_user","label":"檢舉"},'
                           '{"action":"leave_conversation","label":"結束對話"}]'},
        {"template_id": "block_sender_final", "primary_risk_type": "any",
         "action_type": "block_message",
         "message_template": {"title": "訊息未送出", "body": "…"},
         "ui_behavior": {"cooldown": 1800, "require_ack": True},
         "action_options": None},
    ],
    "warning": [
        {"template_id": "receiver_info_warning", "primary_risk_type": "any",
         "action_type": "show_safety_info_card",
         "message_template": {"body": "已對對方啟動提醒"},
         "ui_behavior": {"show_options": False, "show_feedback_buttons": True},
         "action_options": None},
    ],
}


def _run(level):
    engine = InterventionEngine()
    with patch("app.core.intervention_engine.KBService.get_interventions_by_level",
               side_effect=lambda lv: TEMPLATES.get(lv, [])):
        return asyncio.run(engine.execute(
            risk_level=level,
            risk_state={"harassment": 0.7, "coercion": 0.1, "manipulation": 0.0,
                        "sexual_boundary": 0.0, "emotional_pressure": 0.0},
            diagnosis={}, conv_id="c", sender_id="s", receiver_id="r",
            msg_id="m", decision_reason="normal"))


# ── 1. 按鈕清單 ────────────────────────────────────────────────

def test_restricted_receiver_gets_three_buttons():
    """規格 §4 restricted：封鎖／檢舉／停止對話。"""
    opts = _run("restricted")["receiver_directive"]["action_options"]
    assert [o["label"] for o in opts] == ["封鎖", "檢舉", "停止對話"]


def test_blocked_receiver_gets_four_buttons_including_continue():
    """規格 §4 blocked 多一顆「繼續對話」——三組按鈕確實不同，
    所以清單必須由後端送，前端無法用單一組硬寫。"""
    opts = _run("blocked")["receiver_directive"]["action_options"]
    assert [o["label"] for o in opts] == ["繼續對話", "封鎖", "檢舉", "結束對話"]


def test_action_options_parsed_from_json_string():
    """MySQL 的 longtext 欄位會以字串回傳，不能只接受已解析的 list。"""
    opts = _run("blocked")["receiver_directive"]["action_options"]
    assert all(isinstance(o, dict) and "action" in o and "label" in o for o in opts)


def test_sender_never_gets_action_options():
    """規格：行動列只給收件方。寄件方模板的 action_options 為 NULL。"""
    for level in ("restricted", "blocked"):
        assert "action_options" not in _run(level)["sender_directive"]


def test_warning_receiver_has_no_action_options():
    """規格 §4 warning：『本級展開後沒有行動按鈕』。"""
    assert "action_options" not in _run("warning")["receiver_directive"]


def test_missing_action_options_does_not_raise():
    """知識庫未同步（欄位不存在）時 fail-open：不送清單，但不拋錯。

    與 `_to_state_notice` 找不到狀態式模板時的處理一致——
    知識庫落後不該讓使用者收不到介入。
    """
    engine = InterventionEngine()
    stripped = [{k: v for k, v in t.items() if k != "action_options"}
                for t in TEMPLATES["restricted"]]
    with patch("app.core.intervention_engine.KBService.get_interventions_by_level",
               side_effect=lambda lv: stripped if lv == "restricted" else []):
        cmd = asyncio.run(engine.execute(
            risk_level="restricted",
            risk_state={"harassment": 0.7, "coercion": 0.0, "manipulation": 0.0,
                        "sexual_boundary": 0.0, "emotional_pressure": 0.0},
            diagnosis={}, conv_id="c", sender_id="s", receiver_id="r",
            msg_id="m", decision_reason="normal"))
    assert "action_options" not in cmd["receiver_directive"]
    assert cmd["receiver_directive"]["action"] == "show_safety_info_card"


@pytest.mark.parametrize("bad", ["not json at all", "{}", "[1,2,3]",
                                 '[{"action":"x"}]', '[{"label":"y"}]'])
def test_malformed_action_options_ignored(bad):
    """壞資料一律忽略，不讓知識庫的錯誤變成前端的例外。"""
    assert InterventionEngine._parse_action_options(bad) == []


# ── 1b. warning 豁免的行動列例外 ──────────────────────────────
# 規格 §6 的 show_options 欄寫「✓（warning 除外）」。三個等級的豁免共用同一個
# `receiver_state_notice` 模板，而該模板是照 restricted／blocked 設計的、帶行動列。
# 少了這個例外，warning 會出現「情況越平靜、選項越多」：
#     正常 warning（對方剛發了要警告的訊息）→ 收件方沒有封鎖按鈕
#     豁免 warning（對方發的是正常訊息）    → 收件方多出三顆

EXEMPT_TEMPLATES = [
    {"template_id": "receiver_state_notice", "primary_risk_type": "any",
     "action_type": "show_safety_info_card",
     "message_template": {"body": "這段對話仍在加強保護中"},
     "ui_behavior": {"show_options": True, "allow_report_text": True},
     "action_options": RESTRICTED_OPTS},
    {"template_id": "sender_state_notice", "primary_risk_type": "any",
     "action_type": "show_reflection_banner",
     "message_template": {"body": "這段對話仍在觀察中"},
     "ui_behavior": {"allow_report_text": False},
     "action_options": None},
]

WARNING_EXEMPT_TEMPLATES = TEMPLATES["warning"] + [
    {"template_id": "warn_sender_harassment", "primary_risk_type": "harassment",
     "action_type": "show_reflection_banner",
     "message_template": {"body": "頻繁的訊息有時會讓對方感到不舒服"},
     "ui_behavior": {"cooldown": 0, "require_ack": False},
     "action_options": None},
]


def _run_exempt(level, level_templates):
    """四個豁免條件全部成立時的指令。"""
    engine = InterventionEngine()

    class _Log:
        async def get_last_displayed_intervention(self, c, u, r):
            return {"risk_level": level, "timestamp": "2020-01-01T00:00:00+00:00"}

        async def get_remaining_cooldown(self, c, u):
            return 0

    def _kb(lv):
        return EXEMPT_TEMPLATES if lv == "exempt" else level_templates

    with patch("app.core.intervention_engine.KBService.get_interventions_by_level",
               side_effect=_kb):
        return asyncio.run(engine.execute(
            risk_level=level,
            risk_state={"harassment": 0.35, "coercion": 0.0, "manipulation": 0.0,
                        "sexual_boundary": 0.0, "emotional_pressure": 0.0},
            diagnosis={}, conv_id="c", sender_id="s", receiver_id="r",
            msg_id="m", decision_reason="normal",
            chat_log_service=_Log(), message_delta={"harassment": 0.0}))


def test_warning_exempt_has_no_action_options():
    """warning 豁免不給行動列——與正常 warning 一致。"""
    cmd = _run_exempt("warning", WARNING_EXEMPT_TEMPLATES)
    assert cmd["sanction_exempted"] is True
    rd = cmd["receiver_directive"]
    assert "action_options" not in rd
    assert rd["show_options"] is False


def test_restricted_exempt_keeps_action_options():
    """restricted 以上的豁免仍保有行動列（規格 §4「已豁免（收件方）」）——
    這條與上一條成對，任一邊被放寬都會破壞漸進設計。"""
    cmd = _run_exempt("restricted", TEMPLATES["restricted"])
    assert cmd["sanction_exempted"] is True
    rd = cmd["receiver_directive"]
    assert [o["label"] for o in rd["action_options"]] == ["封鎖", "檢舉", "停止對話"]


# ── 1c. 指令要標明給誰看 ──────────────────────────────────────
# 兩個指令會被一起存進訊息紀錄，雙方之後從歷史讀回來時拿到的是同一份。
# 少了 target_user_id，前端就得回推「我是不是這則訊息的寄件方」才知道該畫哪個——
# 實測整合端正是在這裡兩個都畫了出來。

def test_directives_carry_target_user_id():
    engine = InterventionEngine()
    with patch("app.core.intervention_engine.KBService.get_interventions_by_level",
               side_effect=lambda lv: TEMPLATES.get(lv, [])):
        cmd = asyncio.run(engine.execute(
            risk_level="restricted",
            risk_state={"harassment": 0.7, "coercion": 0.0, "manipulation": 0.0,
                        "sexual_boundary": 0.0, "emotional_pressure": 0.0},
            diagnosis={}, conv_id="c", sender_id="u_alice", receiver_id="u_bob",
            msg_id="m", decision_reason="normal"))
    assert cmd["sender_directive"]["target_user_id"] == "u_alice"
    assert cmd["receiver_directive"]["target_user_id"] == "u_bob"


def test_empty_directive_has_no_target_user_id():
    """action 為 none 的指令沒有東西要畫，不需要標記——
    否則前端可能誤以為「有指令給我」而畫出空卡片。"""
    engine = InterventionEngine()
    with patch("app.core.intervention_engine.KBService.get_interventions_by_level",
               side_effect=lambda lv: [t for t in TEMPLATES["warning"]]):
        cmd = asyncio.run(engine.execute(
            risk_level="warning",
            risk_state={"harassment": 0.3, "coercion": 0.0, "manipulation": 0.0,
                        "sexual_boundary": 0.0, "emotional_pressure": 0.0},
            diagnosis={}, conv_id="c", sender_id="u_alice", receiver_id="u_bob",
            msg_id="m", decision_reason="normal"))
    sd = cmd["sender_directive"]          # warning 的 TEMPLATES 只有 receiver 模板
    assert sd["action"] == "none"
    assert "target_user_id" not in sd


# ── 2. 封鎖 ────────────────────────────────────────────────────

class _FakeDocs:
    def __init__(self, docs): self.documents = docs


class _FakeDoc:
    def __init__(self, id_, data): self.id, self.data = id_, data


def _svc(existing=None):
    from app.services.chat_log_service import ChatLogService
    svc = ChatLogService.__new__(ChatLogService)   # 不跑 __init__，避免連線
    svc.db_id = "db"
    svc.created, svc.deleted = [], []
    docs = existing or []

    class _DB:
        def list_documents(_s, db, coll, queries=None):
            # 依 query 值粗略過濾即可——本測試只需要「有沒有找到」
            vals = [q for q in (queries or [])]
            out = docs
            for d in docs:
                pass
            return _FakeDocs(out)

        def create_document(_s, db, coll, doc_id, data):
            svc.created.append((coll, data))
            return _FakeDoc("new_id", data)

        def delete_document(_s, db, coll, doc_id):
            svc.deleted.append((coll, doc_id))

    svc.db = _DB()
    return svc


def test_block_is_idempotent():
    """卡片捷徑與聊天室選單是同一個功能的兩個入口，各按一次不該產生兩筆。"""
    svc = _svc(existing=[_FakeDoc("b1", {"blocker_id": "a", "blocked_id": "b"})])
    res = asyncio.run(svc.save_user_block("a", "b"))
    assert res["ok"] is True and res["already"] is True
    assert svc.created == []          # 沒有重複建立


def test_block_creates_when_absent():
    svc = _svc(existing=[])
    res = asyncio.run(svc.save_user_block("a", "b", conversation_id="c1",
                                          source="intervention"))
    assert res["ok"] is True and res["already"] is False
    coll, data = svc.created[0]
    assert coll == "user_blocks"
    assert data["blocker_id"] == "a" and data["blocked_id"] == "b"
    assert data["source"] == "intervention"


def test_cannot_block_self():
    svc = _svc()
    assert asyncio.run(svc.save_user_block("a", "a"))["error"] == "self_block"


def test_blocked_list_is_bidirectional():
    """A 封鎖 B 之後，B 也不該再配到 A——否則等於告訴 B 他被封鎖了。"""
    from app.services.chat_log_service import ChatLogService
    svc = ChatLogService.__new__(ChatLogService)
    svc.db_id = "db"
    table = [{"blocker_id": "a", "blocked_id": "b"},
             {"blocker_id": "c", "blocked_id": "a"}]

    class _DB:
        def list_documents(_s, db, coll, queries=None):
            # Query.equal 產生的物件轉字串後含欄位名與值，據此判斷這次問的是哪一邊
            q = " ".join(str(x) for x in (queries or []))
            field = "blocker_id" if "blocker_id" in q else "blocked_id"
            return _FakeDocs([_FakeDoc("x", r) for r in table if r[field] == "a"])

    svc.db = _DB()
    out = asyncio.run(svc.get_blocked_user_ids("a"))
    assert out == ["b", "c"]          # 自己封鎖的 ＋ 封鎖自己的


# ── 3. 檢舉 ────────────────────────────────────────────────────

def test_report_is_not_deduplicated():
    """同一人被多次檢舉是有意義的訊號（重複發生），與封鎖不同，不合併。"""
    svc = _svc(existing=[_FakeDoc("r1", {"reporter_id": "a", "reported_id": "b"})])
    res = asyncio.run(svc.save_user_report("a", "b", "harassment"))
    assert res["ok"] is True
    assert len(svc.created) == 1      # 已有一筆仍照樣新增


def test_report_defaults_to_pending():
    """檢舉需要人工判斷，因此必須有處理狀態——這正是它與 `/report`
    （針對某次介入的補充說明，無狀態）的差別。"""
    svc = _svc(existing=[])
    asyncio.run(svc.save_user_report("a", "b", "coercion"))
    _, data = svc.created[0]
    assert data["status"] == "pending"


def test_cannot_report_self():
    svc = _svc()
    assert asyncio.run(svc.save_user_report("a", "a", "other"))["error"] == "self_report"


def test_report_reason_categories_match_risk_dimensions():
    """理由分類沿用五維度＋other，讓審核者能與該對話的 risk_state 對照。"""
    from app.api.risk_detection import REPORT_REASON_CATEGORIES
    from app.models.schemas import RiskState
    assert set(RiskState().model_dump().keys()) | {"other"} == REPORT_REASON_CATEGORIES
