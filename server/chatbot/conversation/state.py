from __future__ import annotations

from typing import Any

# session_id -> interactive clarify state
_sessions: dict[str, dict[str, Any]] = {}


def _is_lives(session: dict) -> bool:
    return session.get("intent") == "lives"


def _is_joint(session: dict) -> bool:
    return session.get("intent") == "joint"


def _with_intent(session: dict, payload: dict) -> dict:
    payload["intent"] = session.get("intent") or "access"
    return payload


def _confirmed_view(session: dict) -> dict:
    return {
        key: value
        for key, value in (session.get("linked") or {}).items()
        if value is not None and value != ""
        and (
            not (session.get("link_info") or {}).get(key)
            or (session["link_info"][key] or {}).get("status") == "matched"
        )
    }
