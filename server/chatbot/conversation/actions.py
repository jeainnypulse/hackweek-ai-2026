from __future__ import annotations

from typing import Any, Optional

from chatbot.conversation.completion import _complete_from_session
from chatbot.conversation.queries import _distinct_for_session
from chatbot.conversation.state import (
    _confirmed_view,
    _is_joint,
    _is_lives,
    _sessions,
    _with_intent,
)
from chatbot.query_resolution.cascade_clarify import (
    CASCADE_SLOTS,
    auto_confirm_sole_cascade_options,
    build_cascade_state,
    distinct_values,
    filter_from_confirmed,
    parse_selections,
    resolve_selection,
)
from chatbot.query_resolution.entity_linking import (
    TERRITORY_LINK_DIMENSION,
    load_territory_catalog_from_db,
    normalize_key,
    territory_type_for_canonical,
)
from chatbot.slot_extract.lives_extract import (
    LIVES_DEFAULT_TERRITORY_NAME,
    LIVES_TERRITORY_OPTIONS,
    apply_lives_defaults,
    is_national_territory,
    normalized_state_code,
)


def _territory_needs_clarification(session: dict) -> bool:
    if not (_is_lives(session) or _is_joint(session)):
        return False
    slots = session.get("slots") or {}
    if slots.get("territoryInvalid"):
        return True
    info = (session.get("link_info") or {}).get(TERRITORY_LINK_DIMENSION) or {}
    return info.get("status") in ("ambiguous", "unknown")


def _territory_clarify_response(
    session: dict, message: Optional[str] = None
) -> dict:
    slots = session.get("slots") or {}
    existing_info = session["link_info"].get(TERRITORY_LINK_DIMENSION) or {}
    raw = str(
        slots.get("territoryInvalid")
        or existing_info.get("raw")
        or slots.get(TERRITORY_LINK_DIMENSION)
        or ""
    ).strip()
    session["current_slot"] = "territoryName"
    session["linked"].pop("territoryType", None)
    session["linked"].pop("territoryName", None)
    session["link_info"][TERRITORY_LINK_DIMENSION] = {
        **existing_info,
        "raw": raw,
        "status": "ambiguous",
        "candidates": list(LIVES_TERRITORY_OPTIONS),
        "method": "territory:clarify",
    }
    return _with_intent(
        session,
        {
            "status": "needs_input",
            "session_id": session["session_id"],
            "question": session["question"],
            "message": message
            or (
                f"I couldn't resolve {raw!r} to a valid U.S. state. "
                "Please select a state or National:"
            ),
            "current_slot": "territoryName",
            "proposed_value": None,
            "candidates": list(LIVES_TERRITORY_OPTIONS),
            "input_mode": "dropdown",
            "confirmed_slots": _confirmed_view(session),
            "allow_skip": False,
            "cascade": None,
            "link_info": session["link_info"],
            "records": [],
        },
    )


def _present_lives_cascade(
    session: dict, selections: Optional[dict] = None
) -> dict:
    options = _distinct_for_session(session, "coverage", session["linked"])
    selected = session["linked"].get("coverage") or resolve_selection(
        (selections or {}).get("coverage"), options
    )
    if not selected and len(options) == 1:
        selected = options[0]
    if selected:
        session["linked"]["coverage"] = selected
        return _complete_from_session(session)
    if not options:
        return _complete_from_session(session)

    cascade = {
        "pending_slots": ["coverage"],
        "selections": {
            "coverage": None,
            "population": None,
            "line": None,
        },
        "options": {
            "coverage": options,
            "population": [],
            "line": [],
        },
        "allow_skip": {"population": True, "line": True},
    }
    session["current_slot"] = None
    session["cascade"] = cascade
    session["lives_coverage_cascade"] = True
    return _with_intent(
        session,
        {
            "status": "needs_cascade_input",
            "session_id": session["session_id"],
            "question": session["question"],
            "message": (
                "I would need some additional information from you "
                "to give you an accurate response"
            ),
            "current_slot": None,
            "proposed_value": None,
            "candidates": options,
            "input_mode": "cascade",
            "confirmed_slots": _confirmed_view(session),
            "allow_skip": False,
            "cascade": cascade,
            "link_info": session["link_info"],
            "records": [],
        },
    )


def _present_cascade(
    session: dict, selections: Optional[dict] = None
) -> dict:
    if _is_lives(session):
        return _present_lives_cascade(session, selections)
    confirmed = session["linked"]
    selections = auto_confirm_sole_cascade_options(confirmed, selections)
    cascade = build_cascade_state(confirmed, selections)

    if not cascade["pending_slots"]:
        for field in CASCADE_SLOTS:
            if field not in confirmed:
                confirmed[field] = None
        return _maybe_lives_coverage_then_complete(session)

    session["current_slot"] = None
    session["cascade"] = cascade
    session["lives_coverage_cascade"] = False
    return _with_intent(
        session,
        {
            "status": "needs_cascade_input",
            "session_id": session["session_id"],
            "question": session["question"],
            "message": (
                "I would need some additional information from you "
                "to give you an accurate response"
            ),
            "current_slot": None,
            "proposed_value": None,
            "candidates": cascade["options"].get("coverage") or [],
            "input_mode": "cascade",
            "confirmed_slots": _confirmed_view(session),
            "allow_skip": False,
            "cascade": cascade,
            "link_info": session["link_info"],
            "records": [],
        },
    )


def _maybe_lives_coverage_then_complete(session: dict) -> dict:
    """Joint still needs Medical vs Pharmacy if access cascade left it empty."""
    if _is_joint(session) and not session["linked"].get("coverage"):
        return _present_lives_cascade(session)
    return _complete_from_session(session)


def _normalize_alias_different(alias: str, canonical: str) -> bool:
    return normalize_key(alias) != normalize_key(canonical)


def handle_continuation(
    session_id: str,
    action: str,
    value: Optional[Any] = None,
) -> dict:
    """Validate and apply an action to an existing conversation session."""
    from chatbot.conversation.flow import _linker_clarify_response

    session = _sessions.get(session_id)
    if not session:
        return {
            "status": "error",
            "session_id": session_id,
            "message": "Session expired. Please ask again.",
            "candidates": [],
            "records": [],
            "confirmed_slots": {},
            "cascade": None,
        }

    action = action.lower().strip()

    if action == "cascade_options":
        selections = parse_selections(value)
        if session.get("lives_coverage_cascade"):
            return _present_lives_cascade(session, selections)
        return _present_cascade(session, selections)

    if action == "submit_cascade":
        selections = parse_selections(value)
        if _is_lives(session) or session.get("lives_coverage_cascade"):
            return _present_lives_cascade(session, selections)

        cascade = build_cascade_state(session["linked"], selections)
        options = cascade["options"]
        picked = cascade["selections"]

        def cascade_error(message: str) -> dict:
            session["cascade"] = cascade
            return _with_intent(
                session,
                {
                    "status": "needs_cascade_input",
                    "session_id": session["session_id"],
                    "question": session["question"],
                    "message": message,
                    "current_slot": None,
                    "candidates": options.get("coverage") or [],
                    "confirmed_slots": _confirmed_view(session),
                    "allow_skip": False,
                    "cascade": cascade,
                    "link_info": session["link_info"],
                    "records": [],
                },
            )

        coverage = picked.get("coverage")
        if not session["linked"].get("coverage"):
            if not coverage or coverage not in options["coverage"]:
                return cascade_error("Please select a Coverage value.")
            session["linked"]["coverage"] = coverage

        population_options = options["population"]
        if "population" not in session["linked"]:
            population = picked.get("population")
            if population_options:
                if not population or population not in population_options:
                    return cascade_error(
                        "Please select a Subtype for the chosen Coverage."
                    )
                session["linked"]["population"] = population
            else:
                session["linked"]["population"] = None

        if "line" not in session["linked"]:
            line = picked.get("line")
            line_filter = filter_from_confirmed(session["linked"])
            line_options = distinct_values("line", line_filter)
            if line_options:
                if len(line_options) == 1:
                    session["linked"]["line"] = line_options[0]
                elif not line or line not in line_options:
                    return cascade_error("Please select a Line of therapy.")
                else:
                    session["linked"]["line"] = line
            else:
                session["linked"]["line"] = None

        return _maybe_lives_coverage_then_complete(session)

    slot = session.get("current_slot")
    if (
        action == "provide"
        and slot == TERRITORY_LINK_DIMENSION
        and _territory_needs_clarification(session)
    ):
        pick = str(value).strip() if value is not None else ""
        if is_national_territory(pick):
            canonical = LIVES_DEFAULT_TERRITORY_NAME
        else:
            canonical = normalized_state_code(pick)
            if canonical is None:
                return _territory_clarify_response(
                    session,
                    "Please select a valid U.S. state or National.",
                )

        territory_catalog = load_territory_catalog_from_db()[
            TERRITORY_LINK_DIMENSION
        ]
        if canonical not in territory_catalog:
            return _territory_clarify_response(
                session,
                "That geography is not available in the lives data. "
                "Please select another state or National.",
            )

        slots = session["slots"]
        slots[TERRITORY_LINK_DIMENSION] = canonical
        slots["territory"] = canonical
        slots["territoryInvalid"] = None
        session["linked"][TERRITORY_LINK_DIMENSION] = canonical
        apply_lives_defaults(
            slots,
            session["linked"],
            territory_type_for_canonical(canonical),
        )
        session["current_slot"] = None
        info = session["link_info"].get(TERRITORY_LINK_DIMENSION) or {}
        info["status"] = "matched"
        info["value"] = canonical
        info["method"] = "user_confirm"
        session["link_info"][TERRITORY_LINK_DIMENSION] = info
        return _linker_clarify_response(session)

    if action == "provide" and slot:
        pick = str(value).strip() if value is not None else ""
        if not pick:
            return {
                **_linker_clarify_response(session),
                "message": "Please choose a value.",
            }
        raw = (session["link_info"].get(slot) or {}).get("raw") or session[
            "slots"
        ].get(slot)
        session["linked"][slot] = pick
        info = session["link_info"].get(slot) or {}
        info["status"] = "matched"
        info["value"] = pick
        info["method"] = "user_confirm"
        session["link_info"][slot] = info
        if raw and _normalize_alias_different(raw, pick):
            session.setdefault("pending_writebacks", []).append(
                {"dimension": slot, "alias": str(raw), "canonical": pick}
            )
        return _linker_clarify_response(session)

    if action == "skip" and slot:
        info = session["link_info"].get(slot) or {}
        info["status"] = "skipped"
        session["link_info"][slot] = info
        return _linker_clarify_response(session)

    return {
        "status": "error",
        "session_id": session_id,
        "message": f"Unknown action: {action}",
        "candidates": [],
        "records": [],
        "confirmed_slots": session.get("linked") or {},
        "cascade": session.get("cascade"),
        "intent": session.get("intent"),
    }
