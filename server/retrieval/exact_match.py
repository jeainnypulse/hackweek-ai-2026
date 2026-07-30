from typing import Any, Optional
import os
import uuid

from bedrock import invoke_claude
from db import get_db
from entity_linking import LINK_DIMENSIONS, link_slots, upsert_synonym
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

# session_id -> clarify state for exact_match_entity_linker chips
_sessions: dict[str, dict[str, Any]] = {}


def extract_slots(question: str) -> dict:
    response = invoke_claude(SLOT_EXTRACTION_PROMPT + question)
    clean = response.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


def _query_records(slots: dict) -> list[dict]:
    query = {k: v for k, v in slots.items() if v is not None and v != ""}
    if not query:
        return []
    db = get_db()
    records = list(db.payerLatestAccess.find(query, {"_id": 0}).limit(50))
    print(f"[exact_match_entity_linker] {len(records)} records found for {query}")
    return records


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


def _clarify_response(session: dict) -> dict:
    pending = _first_pending_link(session["link_info"])
    if not pending:
        return _complete_from_session(session)

    dimension, info = pending
    session["current_slot"] = dimension
    raw = info.get("raw") or session["slots"].get(dimension) or ""
    return {
        "status": "needs_input",
        "session_id": session["session_id"],
        "question": session["question"],
        "message": (
            f"I found multiple possible matches for {dimension} "
            f"({raw!r}). Please pick one:"
        ),
        "current_slot": dimension,
        "proposed_value": None,
        "candidates": list(info.get("candidates") or [])[:20],
        "confirmed_slots": dict(session["linked"]),
        "allow_skip": True,
        "link_info": session["link_info"],
        "records": [],
    }


def _complete_from_session(session: dict) -> dict:
    records = _query_records(session["linked"])
    # Write back only after user confirm + successful retrieve
    if records and session.get("pending_writebacks"):
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
                print(f"[exact_match_entity_linker] synonym writeback failed: {exc}")
        session["pending_writebacks"] = []

    return {
        "status": "complete",
        "session_id": session["session_id"],
        "question": session["question"],
        "records": records,
        "confirmed_slots": dict(session["linked"]),
        "link_info": session["link_info"],
        "candidates": [],
        "message": None,
        "current_slot": None,
    }


def exact_match_retrieval(
    question: str,
    slots: Optional[dict] = None,
    *,
    clarify: bool = False,
) -> list[dict]:
    """
    One-shot retrieve (eval / batch). When clarify=False (default), ignores
    ambiguous linker chips and queries with best-effort linked slots.
    """
    if slots is None:
        slots = extract_slots(question)
    print(f"[exact_match_entity_linker] slots: {slots}")
    linked, link_info = link_slots(slots, method=ENTITY_LINK_METHOD)
    if link_info:
        print(f"[exact_match_entity_linker] linked ({ENTITY_LINK_METHOD}): {link_info}")
    if clarify and _first_pending_link(link_info):
        # Caller should use exact_match_turn for interactive clarify (entity linker)
        pass
    return _query_records(linked)


def exact_match_turn(
    question: str = "",
    session_id: Optional[str] = None,
    action: Optional[str] = None,
    value: Optional[Any] = None,
) -> dict:
    """
    Interactive exact_match_entity_linker with clarification chips.

    - New question: extract -> link -> maybe needs_input
    - action=provide: apply user pick for current_slot, queue synonym writeback
    - action=skip: drop current_slot from query and continue
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
            }

        slot = session.get("current_slot")
        if action == "provide" and slot:
            pick = str(value).strip() if value is not None else ""
            if not pick:
                return {
                    **_clarify_response(session),
                    "message": "Please choose a value.",
                }
            raw = (session["link_info"].get(slot) or {}).get("raw") or session[
                "slots"
            ].get(slot)
            session["linked"][slot] = pick
            # Clear pending for this field
            info = session["link_info"].get(slot) or {}
            info["status"] = "matched"
            info["value"] = pick
            info["method"] = "user_confirm"
            session["link_info"][slot] = info
            if raw and normalize_alias_different(raw, pick):
                session.setdefault("pending_writebacks", []).append(
                    {"dimension": slot, "alias": str(raw), "canonical": pick}
                )
            return _clarify_response(session)

        if action == "skip" and slot:
            # Leave raw mention; mark as skipped so we don't re-ask
            info = session["link_info"].get(slot) or {}
            info["status"] = "skipped"
            session["link_info"][slot] = info
            return _clarify_response(session)

        return {
            "status": "error",
            "session_id": session_id,
            "message": f"Unknown action: {action}",
            "candidates": [],
            "records": [],
            "confirmed_slots": session.get("linked") or {},
        }

    q = question.strip()
    if not q:
        return {
            "status": "error",
            "message": "Question cannot be empty",
            "candidates": [],
            "records": [],
            "confirmed_slots": {},
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
    }
    _sessions[sid] = session

    if _first_pending_link(link_info):
        return _clarify_response(session)
    return _complete_from_session(session)


def normalize_alias_different(alias: str, canonical: str) -> bool:
    from entity_linking import normalize_key

    return normalize_key(alias) != normalize_key(canonical)
