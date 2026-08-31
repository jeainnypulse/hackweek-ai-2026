from __future__ import annotations

from chatbot.conversation.queries import _query_records
from chatbot.conversation.slot_plan import SLOT_LABELS, required_slots
from chatbot.conversation.state import _confirmed_view, _with_intent
from chatbot.query_resolution.entity_linking import upsert_synonym


def _complete_from_session(session: dict) -> dict:
    missing = [
        field
        for field in required_slots(session.get("intent") or "access")
        if session["linked"].get(field) in (None, "")
        or (session["link_info"].get(field) or {}).get("status") != "matched"
    ]
    if missing:
        labels = ", ".join(SLOT_LABELS.get(field, field) for field in missing)
        return _with_intent(
            session,
            {
                "status": "error",
                "session_id": session["session_id"],
                "question": session["question"],
                "message": f"Cannot query until these fields are confirmed: {labels}.",
                "records": [],
                "confirmed_slots": _confirmed_view(session),
                "link_info": session["link_info"],
                "candidates": [],
                "current_slot": None,
            },
        )

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
            "message": None,
            "current_slot": None,
        },
    )
