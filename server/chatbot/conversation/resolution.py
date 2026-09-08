from __future__ import annotations

from typing import Optional

from chatbot.conversation.completion import _complete_from_session
from chatbot.conversation.queries import _distinct_for_session
from chatbot.conversation.slot_plan import SLOT_LABELS, required_slots
from chatbot.conversation.state import _confirmed_view, _with_intent
from chatbot.query_resolution.cascade_clarify import resolve_selection
from chatbot.query_resolution.entity_linking import (
    normalize_key,
    territory_type_for_canonical,
)
from chatbot.query_resolution.linking import catalogs as catalog_store
from chatbot.slot_extract.lives_extract import apply_lives_defaults


def _is_confirmed(session: dict, field: str) -> bool:
    value = session["linked"].get(field)
    if value is None or value == "":
        return False
    info = session["link_info"].get(field)
    return not info or info.get("status") == "matched"


def _confirmed_filter(session: dict, exclude: Optional[str] = None) -> dict:
    fields = required_slots(session.get("intent") or "access")
    return {
        field: session["linked"][field]
        for field in fields
        if field != exclude and _is_confirmed(session, field)
    }


def _options_for(session: dict, field: str) -> list[str]:
    return _distinct_for_session(
        session,
        field,
        _confirmed_filter(session, exclude=field),
    )


def _scoped_candidates(
    session: dict,
    field: str,
    options: list[str],
) -> list[str]:
    info = session["link_info"].get(field) or {}
    raw = info.get("raw") or session["slots"].get(field)
    linker_candidates = info.get("candidates") or []
    if raw and linker_candidates:
        overlap = [value for value in options if value in set(linker_candidates)]
        if overlap:
            return overlap
    return list(options)


def _record_confirmation(
    session: dict,
    field: str,
    value: str,
    *,
    method: str,
) -> None:
    info = dict(session["link_info"].get(field) or {})
    session["linked"][field] = value
    info.update({"status": "matched", "value": value, "method": method})
    session["link_info"][field] = info

    if field == "territoryName":
        slots = session["slots"]
        slots["territoryName"] = value
        slots["territoryInvalid"] = None
        apply_lives_defaults(
            slots,
            session["linked"],
            territory_type_for_canonical(value),
        )


def _should_learn_confirmation(
    field: str,
    raw: str,
    canonical: str,
) -> bool:
    """Only learn organization shorthand when it identifies one global payer."""
    if field != "organization":
        return True

    raw_key = normalize_key(raw).replace(" ", "")
    if not raw_key:
        return False
    organizations = (
        catalog_store.load_catalogs_from_db().get("organization") or []
    )
    matches = [
        value
        for value in organizations
        if raw_key in normalize_key(str(value)).replace(" ", "")
    ]
    return len(matches) == 1 and matches[0] == canonical


def _needs_input(
    session: dict,
    field: str,
    options: list[str],
    *,
    message: Optional[str] = None,
) -> dict:
    info = session["link_info"].get(field) or {}
    raw = info.get("raw") or session["slots"].get(field)
    candidates = _scoped_candidates(session, field, options)

    session["current_slot"] = field
    label = SLOT_LABELS.get(field, field)
    if message is None:
        if raw:
            message = (
                f"I couldn't uniquely resolve {label} ({raw!r}). "
                "Please select the correct value:"
            )
        else:
            message = f"Please select a {label}:"
    return _with_intent(
        session,
        {
            "status": "needs_input",
            "session_id": session["session_id"],
            "question": session["question"],
            "message": message,
            "current_slot": field,
            "proposed_value": None,
            "candidates": candidates,
            "input_mode": "dropdown",
            "confirmed_slots": _confirmed_view(session),
            "allow_skip": False,
            "link_info": session["link_info"],
            "records": [],
        },
    )


def _resolution_error(session: dict, field: str) -> dict:
    session["current_slot"] = None
    label = SLOT_LABELS.get(field, field)
    return _with_intent(
        session,
        {
            "status": "error",
            "session_id": session["session_id"],
            "question": session["question"],
            "message": (
                f"No valid {label} values are available for the confirmed "
                "selections. Please start over with different values."
            ),
            "current_slot": None,
            "candidates": [],
            "confirmed_slots": _confirmed_view(session),
            "allow_skip": False,
            "link_info": session["link_info"],
            "records": [],
        },
    )


def _empty_result(session: dict) -> dict:
    """Return an empty completion when confirmed facets prove no tuple exists."""
    session["current_slot"] = None
    return _with_intent(
        session,
        {
            "status": "complete",
            "session_id": session["session_id"],
            "question": session["question"],
            "message": None,
            "current_slot": None,
            "candidates": [],
            "confirmed_slots": _confirmed_view(session),
            "allow_skip": False,
            "link_info": session["link_info"],
            "records": [],
        },
    )


def resolve_next_slot(session: dict) -> dict:
    """Confirm exactly one ordered facet at a time, then complete."""
    for field in required_slots(session.get("intent") or "access"):
        if _is_confirmed(session, field):
            continue

        options = _options_for(session, field)
        if not options:
            if _confirmed_filter(session, exclude=field):
                return _empty_result(session)
            return _resolution_error(session, field)

        candidates = _scoped_candidates(session, field, options)
        value = session["linked"].get(field)
        canonical = resolve_selection(value, options)
        info = session["link_info"].get(field) or {}

        if len(candidates) == 1:
            _record_confirmation(
                session,
                field,
                candidates[0],
                method="facet:auto_sole",
            )
            print(
                f"[conversation] auto-confirmed sole {field}={candidates[0]!r}"
            )
            continue

        if value and info.get("status") == "matched" and not canonical:
            session["linked"].pop(field, None)
            session["link_info"][field] = {
                **info,
                "status": "ambiguous",
                "candidates": options,
                "method": "facet:clarify",
            }
        return _needs_input(session, field, candidates)

    session["current_slot"] = None
    return _complete_from_session(session)


def provide_current_slot(session: dict, value: object) -> dict:
    field = session.get("current_slot")
    if not field or field not in required_slots(session.get("intent") or "access"):
        return _with_intent(
            session,
            {
                "status": "error",
                "session_id": session["session_id"],
                "message": "There is no field awaiting input.",
                "candidates": [],
                "records": [],
                "confirmed_slots": _confirmed_view(session),
            },
        )

    pick = str(value).strip() if value is not None else ""
    options = _options_for(session, field)
    if not options:
        return _resolution_error(session, field)
    candidates = _scoped_candidates(session, field, options)
    canonical = resolve_selection(pick, candidates)
    if not canonical:
        return _needs_input(
            session,
            field,
            candidates,
            message=f"Please select a valid {SLOT_LABELS.get(field, field)} value.",
        )

    info = session["link_info"].get(field) or {}
    raw = info.get("raw") or session["slots"].get(field)
    _record_confirmation(session, field, canonical, method="user_confirm")
    if (
        raw
        and normalize_key(str(raw)) != normalize_key(canonical)
        and _should_learn_confirmation(field, str(raw), canonical)
    ):
        session.setdefault("pending_writebacks", []).append(
            {"dimension": field, "alias": str(raw), "canonical": canonical}
        )
    session["current_slot"] = None
    return resolve_next_slot(session)
