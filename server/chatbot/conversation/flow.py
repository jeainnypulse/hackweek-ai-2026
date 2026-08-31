from __future__ import annotations

from chatbot.conversation.actions import (
    _maybe_lives_coverage_then_complete,
    _present_cascade,
    _present_lives_cascade,
    _territory_clarify_response,
    _territory_needs_clarification,
)
from chatbot.conversation.completion import _complete_from_session
from chatbot.conversation.identity import (
    _auto_confirm_sole_identity,
    _first_pending_link,
    _refresh_missing_access_identity,
    _scoped_candidates,
)
from chatbot.conversation.queries import _distinct_for_session
from chatbot.conversation.state import (
    _confirmed_view,
    _is_lives,
    _with_intent,
)
from chatbot.query_resolution.cascade_clarify import CASCADE_SLOTS, cascade_needed


def _linker_clarify_response(session: dict) -> dict:
    _refresh_missing_access_identity(session)
    _auto_confirm_sole_identity(session)
    if _is_lives(session) and not session["linked"].get("organization"):
        info = session["link_info"].get("organization") or {}
        session["current_slot"] = "organization"
        raw = info.get("raw") or session["slots"].get("organization") or ""
        candidates = _scoped_candidates(
            session, "organization", list(info.get("candidates") or [])
        )
        if len(candidates) == 1:
            pick = candidates[0]
            session["linked"]["organization"] = pick
            info = dict(info)
            info["status"] = "matched"
            info["value"] = pick
            info["method"] = "auto_sole"
            session["link_info"]["organization"] = info
            print(
                f"[exact_match_entity_linker] auto-confirmed sole "
                f"organization={pick!r}"
            )
            return _after_identity_resolved(session)
        return _with_intent(
            session,
            {
                "status": "needs_input",
                "session_id": session["session_id"],
                "question": session["question"],
                "message": (
                    f"I couldn't uniquely resolve payer ({raw!r}). "
                    "Please select the correct value from the list:"
                    if raw
                    else "Please select which payer you want lives for:"
                ),
                "current_slot": "organization",
                "proposed_value": None,
                "candidates": candidates,
                "input_mode": "dropdown",
                "confirmed_slots": _confirmed_view(session),
                "allow_skip": False,
                "cascade": None,
                "link_info": session["link_info"],
                "records": [],
            },
        )

    pending = _first_pending_link(session["link_info"], lives=_is_lives(session))
    if not pending:
        if _territory_needs_clarification(session):
            return _territory_clarify_response(session)
        return _after_identity_resolved(session)

    dimension, info = pending
    session["current_slot"] = dimension
    raw = info.get("raw") or session["slots"].get(dimension) or ""
    candidates = _scoped_candidates(
        session, dimension, list(info.get("candidates") or [])
    )
    if len(candidates) == 1:
        pick = candidates[0]
        session["linked"][dimension] = pick
        info = dict(info)
        info["status"] = "matched"
        info["value"] = pick
        info["method"] = "auto_sole"
        session["link_info"][dimension] = info
        print(
            f"[exact_match_entity_linker] auto-confirmed sole "
            f"{dimension}={pick!r}"
        )
        return _linker_clarify_response(session)

    label = {
        "population": "subtype",
        "organization": "payer",
    }.get(dimension, dimension.replace("_", " "))
    return _with_intent(
        session,
        {
            "status": "needs_input",
            "session_id": session["session_id"],
            "question": session["question"],
            "message": (
                f"I couldn't uniquely resolve {label} ({raw!r}). "
                f"Please select the correct value from the list:"
            ),
            "current_slot": dimension,
            "proposed_value": None,
            "candidates": candidates,
            "input_mode": "dropdown",
            "confirmed_slots": _confirmed_view(session),
            "allow_skip": not _is_lives(session),
            "cascade": None,
            "link_info": session["link_info"],
            "records": [],
        },
    )


def _after_identity_resolved(session: dict) -> dict:
    """Apply extracted cascade slots, then dropdown-clarify if needed."""
    slots = session.get("slots") or {}
    linked = session["linked"]

    if _is_lives(session):
        if not linked.get("book"):
            books = _distinct_for_session(session, "book", linked)
            if len(books) == 1:
                linked["book"] = books[0]
                session["link_info"]["book"] = {
                    "raw": None,
                    "status": "matched",
                    "value": books[0],
                    "method": "auto_sole",
                }
            elif len(books) > 1:
                session["link_info"]["book"] = {
                    "raw": slots.get("book"),
                    "status": "ambiguous",
                    "candidates": books,
                    "method": "cascade:clarify",
                }
                return _linker_clarify_response(session)
        if not linked.get("coverage"):
            return _present_lives_cascade(session)
        return _complete_from_session(session)

    for field in CASCADE_SLOTS:
        if linked.get(field):
            continue
        raw = slots.get(field)
        if raw:
            linked[field] = raw

    if cascade_needed(linked):
        prefill = {
            field: linked.get(field)
            for field in CASCADE_SLOTS
            if linked.get(field)
        }
        return _present_cascade(session, prefill)
    return _maybe_lives_coverage_then_complete(session)
