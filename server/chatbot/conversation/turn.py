from __future__ import annotations

from typing import Any, Optional
import uuid

from chatbot.conversation.actions import handle_continuation
from chatbot.conversation.config import (
    ENTITY_LINK_METHOD,
    JOINT_LINK_DIMENSIONS,
    LIVES_LINK_DIMENSIONS,
)
from chatbot.conversation.flow import _linker_clarify_response
from chatbot.conversation.state import _sessions
from chatbot.intent_classifier import OUT_OF_SCOPE_MESSAGE, classify_intent
from chatbot.query_resolution.entity_linking import (
    TERRITORY_LINK_DIMENSION,
    TREATMENT_PLAN_LINK_DIMENSIONS,
    link_slots,
    load_catalogs_from_db,
    load_territory_catalog_from_db,
    territory_type_for_canonical,
)
from chatbot.slot_extract.access_extract import extract_access_slots
from chatbot.slot_extract.joint_extract import extract_joint_slots
from chatbot.slot_extract.lives_extract import (
    apply_lives_defaults,
    extract_lives_slots,
)


def extract_slots(question: str, intent: str = "access") -> dict:
    """Dispatch to the access, lives, or joint extractor (one LLM call)."""
    if intent == "lives":
        return extract_lives_slots(question)
    if intent == "joint":
        return extract_joint_slots(question)
    return extract_access_slots(question)


def handle_conversation_turn(
    question: str = "",
    session_id: Optional[str] = None,
    action: Optional[str] = None,
    value: Optional[Any] = None,
) -> dict:
    """Start a question or delegate an action for an existing session."""
    if session_id and action:
        return handle_continuation(session_id, action, value)

    question = question.strip()
    if not question:
        return {
            "status": "error",
            "message": "Question cannot be empty",
            "candidates": [],
            "records": [],
            "confirmed_slots": {},
            "cascade": None,
            "intent": None,
        }

    intent = classify_intent(question)
    if intent == "out_of_scope":
        return {
            "status": "complete",
            "session_id": None,
            "question": question,
            "records": [],
            "confirmed_slots": {},
            "link_info": {},
            "candidates": [],
            "cascade": None,
            "message": OUT_OF_SCOPE_MESSAGE,
            "current_slot": None,
            "intent": "out_of_scope",
        }

    slots = extract_slots(question, intent=intent)
    print(f"[exact_match_entity_linker] intent={intent} slots: {slots}")
    if intent == "lives":
        catalogs = {
            dimension: list(values)
            for dimension, values in load_catalogs_from_db().items()
        }
        catalogs.update(load_territory_catalog_from_db())
        linked, link_info = link_slots(
            slots,
            method=ENTITY_LINK_METHOD,
            catalogs=catalogs,
            dimensions=LIVES_LINK_DIMENSIONS,
            fill_missing=False,
        )
        apply_lives_defaults(
            slots,
            linked,
            territory_type_for_canonical(
                linked.get(TERRITORY_LINK_DIMENSION)
            ),
        )
    else:
        dimensions = TREATMENT_PLAN_LINK_DIMENSIONS
        catalogs = None
        if intent == "joint":
            dimensions = JOINT_LINK_DIMENSIONS
            catalogs = {
                dimension: list(values)
                for dimension, values in load_catalogs_from_db().items()
            }
            catalogs.update(load_territory_catalog_from_db())
        linked, link_info = link_slots(
            slots,
            method=ENTITY_LINK_METHOD,
            catalogs=catalogs,
            dimensions=dimensions,
        )
        if intent == "joint":
            apply_lives_defaults(
                slots,
                linked,
                territory_type_for_canonical(
                    linked.get(TERRITORY_LINK_DIMENSION)
                ),
            )
    if link_info:
        print(
            f"[exact_match_entity_linker] linked "
            f"({ENTITY_LINK_METHOD}): {link_info}"
        )

    session_id = str(uuid.uuid4())
    session = {
        "session_id": session_id,
        "question": question,
        "intent": intent,
        "slots": slots,
        "linked": linked,
        "link_info": link_info,
        "pending_writebacks": [],
        "current_slot": None,
        "cascade": None,
    }
    _sessions[session_id] = session

    return _linker_clarify_response(session)
