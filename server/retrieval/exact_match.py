from __future__ import annotations

from typing import Any, Optional
import os
import uuid

from bedrock import invoke_claude
from db import get_db
from entity_linking import LINK_DIMENSIONS, link_slots, upsert_synonym
from permissions import scoped_query
from retrieval.cascade_clarify import (
    CASCADE_SLOTS,
    IDENTITY_SLOTS,
    auto_confirm_sole_cascade_options,
    build_cascade_state,
    cascade_needed,
    distinct_values,
    filter_from_confirmed,
    parse_selections,
)
import json

# none | catalog | synonym | fuzzy | cascade | hypothesize_verify
# cascade = synonym -> catalog -> LLM hypothesize+Mongo verify (production)
ENTITY_LINK_METHOD = os.getenv("ENTITY_LINK_METHOD", "cascade")

SLOT_EXTRACTION_PROMPT = """Extract structured slots from this payer question. Return ONLY valid JSON with no explanation or markdown.

Slots to extract:
- regimen: brand name (e.g. Spinraza, Calquence). Return null if not specified.
- indication: disease/indication only (e.g. "Spinal Muscular Atrophy", "CLL"). Do NOT include line of therapy.
- line: line of therapy (e.g. "1L", "2L"). Return null if not specified.
- book: book of business (e.g. "Commercial", "Medicare", "Managed Medicaid"). Return null if not specified.
- organization: specific payer name (e.g. "Health Net", "Aetna"). Return null if not specified.
- coverage: "Medical" or "Pharmacy" if mentioned. Return null if not specified.
- population: patient population/subtype if mentioned. Return null if not specified.

Return format:
{"regimen": "Spinraza", "indication": "Spinal Muscular Atrophy", "line": "1L", "book": "Managed Medicaid", "organization": "Health Net", "coverage": "Medical", "population": null}

Question: """

# session_id -> interactive clarify state
_sessions: dict[str, dict[str, Any]] = {}


def extract_slots(question: str) -> dict:
    response = invoke_claude(SLOT_EXTRACTION_PROMPT + question)
    clean = response.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


def extract_and_link(
    question: str, slots: Optional[dict] = None
) -> tuple[dict, dict]:
    """
    Extract slots then canonicalize via the entity linker (no Mongo retrieve).

    Returns (linked_slots, link_info). Used by batch eval (linker-scoped scoring).
    """
    if slots is None:
        slots = extract_slots(question)
    print(f"[exact_match_entity_linker] slots: {slots}")
    linked, link_info = link_slots(slots, method=ENTITY_LINK_METHOD)
    if link_info:
        print(f"[exact_match_entity_linker] linked ({ENTITY_LINK_METHOD}): {link_info}")
    return linked, link_info


def clarify_needed(link_info: Optional[dict]) -> bool:
    """True if any identity dimension is still ambiguous/unknown with candidates."""
    if not link_info:
        return False
    for dimension in LINK_DIMENSIONS:
        info = link_info.get(dimension) or {}
        if info.get("status") in ("ambiguous", "unknown") and (
            info.get("candidates") or []
        ):
            return True
    return False


def _query_records(slots: dict) -> list[dict]:
    query = {k: v for k, v in slots.items() if v is not None and v != ""}
    if not query:
        return []
    db = get_db()
    scoped = scoped_query(query)
    records = list(db.payerLatestAccess.find(scoped, {"_id": 0}).limit(50))
    print(f"[exact_match_entity_linker] {len(records)} records found for {query}")
    return records


def _confirmed_view(session: dict) -> dict:
    return {
        k: v
        for k, v in (session.get("linked") or {}).items()
        if v is not None and v != ""
    }


def _auto_confirm_sole_identity(session: dict) -> None:
    """If an unresolved link dimension has only one Mongo value left, take it."""
    linked = session["linked"]
    for dimension in LINK_DIMENSIONS:
        info = session["link_info"].get(dimension) or {}
        if info.get("status") not in ("ambiguous", "unknown"):
            continue
        if linked.get(dimension):
            continue
        filt = {
            k: v
            for k, v in linked.items()
            if k in IDENTITY_SLOTS and k != dimension and v
        }
        options = distinct_values(dimension, filt)
        cands = info.get("candidates") or []
        if cands:
            options = [o for o in options if o in cands] or options
        if len(options) == 1:
            pick = options[0]
            linked[dimension] = pick
            info = dict(info)
            info["status"] = "matched"
            info["value"] = pick
            info["method"] = "auto_sole"
            session["link_info"][dimension] = info
            print(
                f"[exact_match_entity_linker] auto-confirmed sole "
                f"{dimension}={pick!r}"
            )


def _first_pending_link(link_info: dict) -> Optional[tuple[str, dict]]:
    """Prefer ambiguous-with-candidates, then unknown-with-candidates."""
    for status in ("ambiguous", "unknown"):
        for dimension in LINK_DIMENSIONS:
            info = link_info.get(dimension) or {}
            if info.get("status") != status:
                continue
            cands = info.get("candidates") or []
            if cands:
                return dimension, info
    return None


def _scoped_candidates(session: dict, dimension: str, fallback: list[str]) -> list[str]:
    filt = {
        k: v
        for k, v in session["linked"].items()
        if k in IDENTITY_SLOTS and k != dimension and v
    }
    scoped = distinct_values(dimension, filt)
    if not scoped:
        return list(fallback)
    if fallback:
        overlap = [v for v in scoped if v in set(fallback)]
        return overlap or scoped
    return scoped


def _linker_clarify_response(session: dict) -> dict:
    _auto_confirm_sole_identity(session)
    pending = _first_pending_link(session["link_info"])
    if not pending:
        return _after_identity_resolved(session)

    dimension, info = pending
    session["current_slot"] = dimension
    raw = info.get("raw") or session["slots"].get(dimension) or ""
    candidates = _scoped_candidates(
        session, dimension, list(info.get("candidates") or [])
    )
    # Sole option after scoping — auto-confirm and continue
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
        return _after_identity_resolved(session)

    label = dimension.replace("_", " ")
    return {
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
        "allow_skip": True,
        "cascade": None,
        "link_info": session["link_info"],
        "records": [],
    }


def _present_cascade(session: dict, selections: Optional[dict] = None) -> dict:
    confirmed = session["linked"]
    selections = auto_confirm_sole_cascade_options(confirmed, selections)
    cascade = build_cascade_state(confirmed, selections)

    if not cascade["pending_slots"]:
        for field in CASCADE_SLOTS:
            if field not in confirmed:
                confirmed[field] = None
        return _complete_from_session(session)

    session["current_slot"] = None
    session["cascade"] = cascade
    return {
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
    }


def _after_identity_resolved(session: dict) -> dict:
    """Apply extracted cascade slots, then dropdown-clarify if needed."""
    slots = session.get("slots") or {}
    linked = session["linked"]

    # Prefill cascade fields from extraction when present
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
    return _complete_from_session(session)


def _complete_from_session(session: dict) -> dict:
    records = _query_records(session["linked"])
    if records and session.get("pending_writebacks"):
        from entity_linking import USE_SYNONYM_TABLE

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
                        f"[exact_match_entity_linker] synonym writeback "
                        f"{item['dimension']}: {item['alias']!r} -> {item['canonical']!r}"
                    )
                except Exception as exc:
                    print(
                        f"[exact_match_entity_linker] synonym writeback failed: {exc}"
                    )
        session["pending_writebacks"] = []

    return {
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
    }


def exact_match_retrieval(
    question: str,
    slots: Optional[dict] = None,
    *,
    clarify: bool = False,
    return_linked: bool = False,
):
    """
    One-shot retrieve (eval / batch). When clarify=False (default), ignores
    interactive clarify prompts and queries with best-effort linked slots.

    If return_linked=True, returns (records, linked_slots) where linked_slots
    are the final canonical slots used for the Mongo query.
    """
    if slots is None:
        slots = extract_slots(question)
    print(f"[exact_match_entity_linker] slots: {slots}")
    linked, link_info = link_slots(slots, method=ENTITY_LINK_METHOD)
    if link_info:
        print(f"[exact_match_entity_linker] linked ({ENTITY_LINK_METHOD}): {link_info}")
    if clarify and _first_pending_link(link_info):
        pass
    records = _query_records(linked)
    if return_linked:
        return records, linked
    return records


def exact_match_turn(
    question: str = "",
    session_id: Optional[str] = None,
    action: Optional[str] = None,
    value: Optional[Any] = None,
) -> dict:
    """
    Interactive exact_match_entity_linker:
      extract → entity link → identity dropdown if needed
      → coverage/subtype/line cascade (auto-confirm sole options)
      → query
    """
    if session_id and action:
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

        action = (action or "").lower().strip()

        if action == "cascade_options":
            selections = parse_selections(value)
            return _present_cascade(session, selections)

        if action == "submit_cascade":
            selections = parse_selections(value)
            cascade = build_cascade_state(session["linked"], selections)
            options = cascade["options"]
            picked = cascade["selections"]

            def _cascade_error(msg: str) -> dict:
                session["cascade"] = cascade
                return {
                    "status": "needs_cascade_input",
                    "session_id": session["session_id"],
                    "question": session["question"],
                    "message": msg,
                    "current_slot": None,
                    "candidates": options.get("coverage") or [],
                    "confirmed_slots": _confirmed_view(session),
                    "allow_skip": False,
                    "cascade": cascade,
                    "link_info": session["link_info"],
                    "records": [],
                }

            coverage = picked.get("coverage")
            if not session["linked"].get("coverage"):
                if not coverage or coverage not in options["coverage"]:
                    return _cascade_error("Please select a Coverage value.")
                session["linked"]["coverage"] = coverage

            pop_options = options["population"]
            if "population" not in session["linked"]:
                population = picked.get("population")
                if pop_options:
                    if not population or population not in pop_options:
                        return _cascade_error(
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
                        return _cascade_error("Please select a Line of therapy.")
                    else:
                        session["linked"]["line"] = line
                else:
                    session["linked"]["line"] = None

            return _complete_from_session(session)

        slot = session.get("current_slot")
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
            if raw and normalize_alias_different(raw, pick):
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
        }

    q = question.strip()
    if not q:
        return {
            "status": "error",
            "message": "Question cannot be empty",
            "candidates": [],
            "records": [],
            "confirmed_slots": {},
            "cascade": None,
        }

    slots = extract_slots(q)
    print(f"[exact_match_entity_linker] slots: {slots}")
    linked, link_info = link_slots(slots, method=ENTITY_LINK_METHOD)
    if link_info:
        print(f"[exact_match_entity_linker] linked ({ENTITY_LINK_METHOD}): {link_info}")

    sid = str(uuid.uuid4())
    session = {
        "session_id": sid,
        "question": q,
        "slots": slots,
        "linked": linked,
        "link_info": link_info,
        "pending_writebacks": [],
        "current_slot": None,
        "cascade": None,
    }
    _sessions[sid] = session

    return _linker_clarify_response(session)


def normalize_alias_different(alias: str, canonical: str) -> bool:
    from entity_linking import normalize_key

    return normalize_key(alias) != normalize_key(canonical)
