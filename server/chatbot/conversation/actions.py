from __future__ import annotations

from typing import Any

from chatbot.conversation.resolution import provide_current_slot
from chatbot.conversation.state import _confirmed_view, _sessions


def handle_continuation(
    session_id: str,
    action: str,
    value: Any = None,
) -> dict:
    """Validate a provide-only continuation for an active session."""
    session = _sessions.get(session_id)
    if not session:
        return {
            "status": "error",
            "session_id": session_id,
            "message": "Session expired. Please ask again.",
            "candidates": [],
            "records": [],
            "confirmed_slots": {},
        }

    if action.lower().strip() != "provide":
        return {
            "status": "error",
            "session_id": session_id,
            "message": "Only the provide continuation action is supported.",
            "candidates": [],
            "records": [],
            "confirmed_slots": _confirmed_view(session),
            "intent": session.get("intent"),
        }

    return provide_current_slot(session, value)
