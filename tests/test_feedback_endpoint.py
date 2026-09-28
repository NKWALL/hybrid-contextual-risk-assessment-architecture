"""Feedback endpoint service tests (pure mock, no Appwrite)."""

import asyncio
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def chat_service(monkeypatch):
    monkeypatch.setenv("APPWRITE_ENDPOINT", "http://test/v1")
    monkeypatch.setenv("APPWRITE_PROJECT_ID", "test-proj")
    monkeypatch.setenv("APPWRITE_API_KEY", "test-key")
    monkeypatch.setenv("APPWRITE_DB_ID", "test-db")

    from app.services.chat_log_service import ChatLogService

    svc = ChatLogService()
    svc.db = MagicMock()
    return svc


def test_update_feedback_success(chat_service):
    """Normal path: find row, update it, return True."""
    mock_doc = MagicMock()
    mock_doc.id = "log_123"
    mock_response = MagicMock()
    mock_response.documents = [mock_doc]
    chat_service.db.list_documents.return_value = mock_response
    chat_service.db.update_document.return_value = {"$id": "log_123"}

    ok = asyncio.run(chat_service.update_intervention_feedback(
        msg_id="msg_abc", role="receiver", feedback="comfortable"
    ))

    assert ok is True
    call_args = chat_service.db.update_document.call_args
    assert call_args[0][2] == "log_123"
    assert call_args[0][3] == {"receiver_feedback": "comfortable"}


def test_update_feedback_invalid_role(chat_service):
    """Invalid role should return False without querying DB."""
    ok = asyncio.run(chat_service.update_intervention_feedback(
        msg_id="msg_abc", role="admin", feedback="comfortable"
    ))

    assert ok is False
    chat_service.db.list_documents.assert_not_called()


def test_update_feedback_invalid_value(chat_service):
    """Invalid feedback value should return False."""
    ok = asyncio.run(chat_service.update_intervention_feedback(
        msg_id="msg_abc", role="receiver", feedback="happy"
    ))

    assert ok is False


def test_update_feedback_msg_not_found(chat_service):
    """Missing intervention log should return False."""
    mock_response = MagicMock()
    mock_response.documents = []
    chat_service.db.list_documents.return_value = mock_response

    ok = asyncio.run(chat_service.update_intervention_feedback(
        msg_id="msg_xyz", role="receiver", feedback="uncomfortable"
    ))

    assert ok is False
    chat_service.db.update_document.assert_not_called()


# --- 收件方文字回報（2026-08-15）---
#
# 與 sender_appeal 對稱但角色相反，刻意分成兩個欄位／兩個端點：
# 一個是被警告者自辯、一個是被保護者陳述，稽核意義相反。
# 兩者皆不進入演算法。

def _doc(**data):
    d = MagicMock()
    d.id = "log_123"
    d.data = data
    resp = MagicMock()
    resp.documents = [d]
    return resp


def test_receiver_report_success(chat_service):
    chat_service.db.list_documents.return_value = _doc(receiver_id="r1", sender_id="s1")

    r = asyncio.run(chat_service.save_receiver_report("m1", "r1", "他一直問我住哪"))

    assert r["ok"] is True
    written = chat_service.db.update_document.call_args[0][3]
    assert written == {"receiver_report_text": "他一直問我住哪"}


def test_receiver_report_rejects_wrong_user(chat_service):
    """只有該則訊息的收件方本人可以回報。"""
    chat_service.db.list_documents.return_value = _doc(receiver_id="someone_else")

    r = asyncio.run(chat_service.save_receiver_report("m1", "r1", "x"))

    assert r == {"ok": False, "error": "receiver_mismatch"}
    chat_service.db.update_document.assert_not_called()


def test_receiver_report_missing_attribute_is_explicit(chat_service):
    """屬性未建立時要回明確錯誤，不可靜默失敗。

    Appwrite 為嚴格 schema，屬性未建立時整筆寫入失敗；若被 except 吞掉，
    使用者會以為回報成功但資料從未寫入。
    """
    chat_service.db.list_documents.return_value = _doc(receiver_id="r1")
    chat_service.db.update_document.side_effect = Exception(
        'Invalid document structure: Unknown attribute: "receiver_report_text"')

    r = asyncio.run(chat_service.save_receiver_report("m1", "r1", "x"))

    assert r == {"ok": False, "error": "attribute_missing"}


def test_receiver_report_and_sender_appeal_use_separate_fields(chat_service):
    """兩者不得寫進同一個欄位——後台要能分辨是自辯還是陳述。"""
    chat_service.db.list_documents.return_value = _doc(receiver_id="r1", sender_id="s1")
    asyncio.run(chat_service.save_receiver_report("m1", "r1", "收件方說的"))
    field_r = set(chat_service.db.update_document.call_args[0][3])

    chat_service.db.list_documents.return_value = _doc(receiver_id="r1", sender_id="s1")
    asyncio.run(chat_service.save_sender_appeal("m1", "s1", "寄件方說的"))
    field_s = set(chat_service.db.update_document.call_args[0][3])

    assert field_r == {"receiver_report_text"}
    assert field_s == {"sender_appeal_text"}
    assert field_r.isdisjoint(field_s)
