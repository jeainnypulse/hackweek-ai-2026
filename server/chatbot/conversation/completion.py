from __future__ import annotations

from chatbot.conversation.queries import _query_records
from chatbot.conversation.state import _confirmed_view, _with_intent
from chatbot.query_resolution.entity_linking import upsert_synonym


def _complete_from_session(session: dict) -> dict:
    records = _query_records(
        session["linked"], intent=session.get("intent") or "access"
    )
    if records and session.get("pending_writebacks"):
        from chatbot.query_resolution.entity_linking import USE_SYNONYM_TABLE

        if USE_SYNONYM_TABLE:
            for item in session["pending_writebacks"]:
                try:
                    upsert_synonym(
                        item["dimension"],
                        item["alias"],
                        item["canonical"],
                        source="user_confirm",
                    )
                    print(
                        f"[conversation] synonym writeback "
                        f"{item['dimension']}: {item['alias']!r} -> "
                        f"{item['canonical']!r}"
                    )
                except Exception as exc:
                    print(
                        f"[conversation] synonym writeback failed: {exc}"
                    )
        session["pending_writebacks"] = []

    return _with_intent(
        session,
        {
            "status": "complete",
            "session_id": session["session_id"],
            "question": session["question"],
            "records": records,
            "confirmed_slots": _confirmed_view(session),
            "link_info": session["link_info"],
            "candidates": [],
            "cascade": None,
            "message": None,
            "current_slot": None,
        },
    )
