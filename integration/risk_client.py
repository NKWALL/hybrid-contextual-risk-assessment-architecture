"""HTTP client wrapper for the Risk Detection microservice.

This module is the single integration point between the Unified Server (:8000)
and the external Risk Detection service (:8001). It encapsulates timeout,
error handling, and graceful degradation so callers can stay simple.

Environment variables:
  RISK_SERVICE_URL  base URL of the risk service (default: http://localhost:8001)
  RISK_TIMEOUT_SEC  request timeout in seconds (default: 1.5)
"""

import os
from typing import Optional

import httpx


_BASE_URL = os.getenv("RISK_SERVICE_URL", "http://localhost:8001")
_TIMEOUT = float(os.getenv("RISK_TIMEOUT_SEC", "1.5"))

_RISK_UI_LEVELS = {"warning", "restricted", "blocked"}


def check_risk(
    conversation_id: str,
    sender_id: str,
    receiver_id: str,
    content: str,
) -> Optional[dict]:
    """Call POST /api/v1/risk/detect on the risk service.

    Returns the parsed response dict on success, or None on any failure
    (timeout, connection error, non-200 status, malformed JSON).

    Callers should treat None as "risk service unavailable, fall back to
    local-only safety logic such as check_boundary_guard".
    """
    payload = {
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "current_message": content,
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(f"{_BASE_URL}/api/v1/risk/detect", json=payload)
        if resp.status_code != 200:
            print(f"[risk_client] non-200 status: {resp.status_code}")
            return None
        return resp.json()
    except httpx.RequestError as e:
        print(f"[risk_client] request error: {e}")
        return None
    except Exception as e:
        print(f"[risk_client] unexpected error: {e}")
        return None


def submit_feedback(triggered_by_msg_id: str, role: str, feedback: str) -> bool:
    """Forward receiver/sender feedback to POST /api/v1/risk/feedback.

    role     : 'receiver' | 'sender'
    feedback : 'comfortable' | 'uncomfortable'

    Returns True on HTTP 200, False otherwise.
    """
    payload = {
        "triggered_by_msg_id": triggered_by_msg_id,
        "role": role,
        "feedback": feedback,
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(f"{_BASE_URL}/api/v1/risk/feedback", json=payload)
        return resp.status_code == 200
    except Exception as e:
        print(f"[risk_client] feedback submit failed: {e}")
        return False


def should_show_risk_ui(risk_level: str) -> bool:
    """Returns True iff this risk level should override coach UI.

    Used by chat router to set the ui_priority flag on the response.
    Maps to integration_options.md §6 UI conflict rules.
    """
    return risk_level in _RISK_UI_LEVELS


def is_blocked(risk_assessment: Optional[dict]) -> bool:
    """Convenience guard for callers that only care about the block decision."""
    return bool(risk_assessment and risk_assessment.get("risk_level") == "blocked")


def attach_to_response(res: dict, risk_assessment: Optional[dict]) -> dict:
    """Mutate res to include risk fields. Safe to call with None risk_assessment.

    Adds two fields to res:
      - risk_assessment : the full response from the risk service (or None)
      - ui_priority     : 'risk' if warning/restricted/blocked, else 'coach'

    Frontend should branch on ui_priority to decide whether to render
    coach nudges or risk warnings (see RISK_INTEGRATION.md).
    """
    if risk_assessment:
        res["risk_assessment"] = risk_assessment
        res["ui_priority"] = (
            "risk" if should_show_risk_ui(risk_assessment["risk_level"]) else "coach"
        )
    else:
        res["risk_assessment"] = None
        res["ui_priority"] = "coach"
    return res
