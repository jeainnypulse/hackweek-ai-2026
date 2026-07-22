"""Multi-step conversational slot extraction against live Mongo data.

Used when retrieval_method = "exact_match_conversational".
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from typing import Any, Optional

from bedrock import invoke_claude
from db import get_db

SLOT_ORDER = [
    "regimen",
    "indication",
    "book",
    "coverage",
    "organization",
    "population",
    "line",
]

# benefit is an extraction alias for coverage (Medical/Pharmacy) in this dataset
HINT_FIELDS = ("benefit",)

BOOK_VALUES = {
    "commercial",
    "medicare",
    "managed medicaid",
    "ffs medicaid",
    "health exchange",
    "mac",
    "medicaid",
}

SLOT_LABELS = {
    "regimen": "product",
    "indication": "indication",
    "book": "book of business",
    "coverage": "coverage (benefit)",
    "organization": "payer",
    "population": "subtype",
    "line": "line of therapy",
}

SKIP_POPULATION_VALUES = {"no subtype specified", "n/a", "na", "none", ""}

SLOT_EXTRACTION_PROMPT = """Extract structured slots from this payer question. Return ONLY valid JSON with no explanation or markdown.

IMPORTANT — these are SEPARATE Mongo fields:
- book = Book of Business (Commercial, Medicare, Managed Medicaid, FFS Medicaid, Health Exchange, MAC, etc.)
- coverage = benefit type only (Medical or Pharmacy). NOT book. NOT "Commercial Medical".
- Never put "Commercial" / "Medicare" into coverage. That belongs in book.
- Never put "Medical" / "Pharmacy" into book. That belongs in coverage.

Field mappings (user language → fields):
- product / drug / brand → regimen
- subtype → population
- "Commercial plans", "under Medicare", "Medicaid" → book
- "Medical" / "Pharmacy" / "pharmacy benefit" → coverage
- payer / account → organization
- line of therapy → line

Slots to extract:
- regimen: brand/product name. null if not specified.
- indication: disease/indication only. Do NOT include line of therapy.
- population: subtype/patient population. null if not specified.
- book: book of business only. null if not specified.
- coverage: "Medical" or "Pharmacy" only. null if not specified.
- organization: specific payer name. null if not specified.
- line: line of therapy (e.g. "1L", "2L"). null if not specified.

Example — "Brukinsa QoA at Aetna for CLL under Commercial plans":
{"regimen": "Brukinsa", "indication": "CLL", "population": null, "book": "Commercial", "coverage": null, "organization": "Aetna", "line": null}

Example — "Calquence Pharmacy access under Medicare":
{"regimen": "Calquence", "indication": null, "population": null, "book": "Medicare", "coverage": "Pharmacy", "organization": null, "line": null}

Question: """

MATCH_PROMPT = """You are matching a user-provided value to a canonical value from a live database list.

Field: {field} ({label})
User said: {user_value}

Canonical candidates (choose ONLY from this list, copying the string exactly):
{candidates}

Rules:
- Return the closest semantic match if you are confident they refer to the same thing (including common aliases, abbreviations, typos).
- book is Book of Business (e.g. Commercial, Medicare). coverage is benefit type (Medical or Pharmacy). Do not confuse them.
- If nothing is a confident match, return null.

Return ONLY valid JSON:
{{"match": "<exact candidate string or null>", "confidence": "high"|"low"}}
"""

_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


def _clean_slot_value(value) -> Optional[str]:
    if value is None or value in ("", "null"):
        return None
    text = str(value).strip()
    return text or None


def _normalize_key(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def extract_slots(question: str) -> dict:
    response = invoke_claude(SLOT_EXTRACTION_PROMPT + question)
    clean = response.strip().replace("```json", "").replace("```", "").strip()
    raw = json.loads(clean)
    slots = {
        field: _clean_slot_value(raw.get(field))
        for field in (*SLOT_ORDER, *HINT_FIELDS)
    }

    # benefit alias → coverage (Medical/Pharmacy)
    if slots.get("benefit") and not slots.get("coverage"):
        slots["coverage"] = slots["benefit"]

    coverage = slots.get("coverage")
    book = slots.get("book")
    if coverage:
        coverage_norm = _normalize_key(coverage)
        # Misplaced book value in coverage → move to book
        if coverage_norm in BOOK_VALUES:
            if not book:
                slots["book"] = coverage
            slots["coverage"] = None
        # Combined strings like "Commercial Medical" → split
        elif " " in coverage_norm:
            parts = coverage_norm.split()
            maybe_book = " ".join(parts[:-1]) if parts[-1] in {"medical", "pharmacy"} else None
            maybe_benefit = parts[-1] if parts[-1] in {"medical", "pharmacy"} else None
            if maybe_book in BOOK_VALUES and maybe_benefit:
                if not book:
                    slots["book"] = coverage.rsplit(" ", 1)[0]
                slots["coverage"] = maybe_benefit.title()

    slots.pop("benefit", None)
    return slots


def _lookup_alias(field: str, alias: str, filter_query: dict) -> Optional[str]:
    db = get_db()
    doc = db.alias_cache.find_one(
        {
            "field": field,
            "alias_norm": _normalize_key(alias),
            "filter": filter_query,
        }
    )
    return doc.get("canonical") if doc else None


def _store_alias(field: str, alias: str, canonical: str, filter_query: dict) -> None:
    db = get_db()
    db.alias_cache.update_one(
        {
            "field": field,
            "alias_norm": _normalize_key(alias),
            "filter": filter_query,
        },
        {
            "$set": {
                "field": field,
                "alias": alias,
                "alias_norm": _normalize_key(alias),
                "canonical": canonical,
                "filter": filter_query,
                "updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )


def _distinct_values(field: str, filter_query: dict) -> list[str]:
    db = get_db()
    values = db.payerLatestAccess.distinct(field, filter_query)
    cleaned = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned.append(text)
    return sorted(set(cleaned), key=lambda v: v.lower())


def find_closest_match(
    user_value: str,
    candidates: list[str],
    field: str,
    filter_query: dict,
) -> Optional[str]:
    if not user_value or not candidates:
        return None

    user_norm = _normalize_key(user_value)
    for candidate in candidates:
        if _normalize_key(candidate) == user_norm:
            return candidate

    cached = _lookup_alias(field, user_value, filter_query)
    if cached and cached in candidates:
        return cached

    # Keep prompt size manageable for large organization lists
    candidate_text = "\n".join(f"- {c}" for c in candidates[:400])
    prompt = MATCH_PROMPT.format(
        field=field,
        label=SLOT_LABELS.get(field, field),
        user_value=user_value,
        candidates=candidate_text,
    )
    response = invoke_claude(prompt, max_tokens=200)
    clean = response.strip().replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        return None

    match = parsed.get("match")
    confidence = (parsed.get("confidence") or "").lower()
    if not match or confidence == "low":
        return None
    match = str(match).strip()
    if match not in candidates:
        # Case-insensitive fallback
        for candidate in candidates:
            if _normalize_key(candidate) == _normalize_key(match):
                match = candidate
                break
        else:
            return None

    _store_alias(field, user_value, match, filter_query)
    return match


def _filter_from_confirmed(confirmed: dict) -> dict:
    return {k: v for k, v in confirmed.items() if v is not None}


def _optional_slot_action(
    field: str, candidates: list[str], extracted_value: Optional[str]
) -> str:
    """How to handle population/line when advancing.

    Returns:
      - "resolve" — user mentioned a value; match/confirm it
      - "ask" — user did not mention it; ask explicitly (never assume)
      - "omit" — safe to leave unfiltered (no real candidates / only null-like subtype)
    """
    if extracted_value:
        return "resolve"

    if field == "population":
        real = [
            c for c in candidates if _normalize_key(c) not in SKIP_POPULATION_VALUES
        ]
        if not real:
            return "omit"
        return "ask"

    if field == "line":
        if not candidates:
            return "omit"
        return "ask"

    return "resolve"


def latest_timestamp_wins(records: list[dict]) -> list[dict]:
    """Keep the latest timestamp record per confirmed-slice key."""
    if not records:
        return []

    best: dict[tuple, dict] = {}
    for record in records:
        key = (
            record.get("regimen"),
            record.get("indication"),
            record.get("book"),
            record.get("coverage"),
            record.get("organization"),
            record.get("population"),
            record.get("line"),
        )
        current = best.get(key)
        if current is None:
            best[key] = record
            continue
        if _timestamp_value(record) >= _timestamp_value(current):
            best[key] = record
    return list(best.values())


def _timestamp_value(record: dict) -> datetime:
    value = record.get("timestamp")
    if isinstance(value, datetime):
        return value
    if value is None:
        return datetime.min
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return datetime.min


def _no_match_message(field: str, user_value: str) -> str:
    label = SLOT_LABELS.get(field, field)
    return (
        f'No match found for {label} "{user_value}" — '
        "please check the name and try again."
    )


def _confirm_message(field: str, proposed: str) -> str:
    label = SLOT_LABELS.get(field, field)
    if field == "regimen":
        return f'Did you mean "{proposed}"?'
    return f'Did you mean {label} "{proposed}"?'


def _ask_for_value_message(
    field: str,
    candidates: list[str],
    extracted: Optional[dict] = None,
) -> str:
    label = SLOT_LABELS.get(field, field)
    if not candidates:
        return f"No {label} values found for the confirmed filters. Please start over with a different question."
    preview = candidates[:12]
    listed = ", ".join(preview)
    suffix = "…" if len(candidates) > 12 else ""

    return f"Which {label}? Examples: {listed}{suffix}"


def _create_session(question: str, extracted: dict) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "question": question,
        "extracted": extracted,
        "confirmed": {},
        "current_slot": None,
        "proposed_value": None,
        "candidates": [],
        "status": "in_progress",
        "message": "",
    }


def _get_session(session_id: str) -> Optional[dict]:
    with _sessions_lock:
        return _sessions.get(session_id)


def _save_session(session: dict) -> None:
    with _sessions_lock:
        _sessions[session["id"]] = session


def _delete_session(session_id: str) -> None:
    with _sessions_lock:
        _sessions.pop(session_id, None)


def _advance_to_next_slot(session: dict) -> dict:
    """Move to the next unconfirmed slot that needs user input, or finish."""
    while True:
        next_field = None
        for field in SLOT_ORDER:
            if field not in session["confirmed"]:
                next_field = field
                break

        if next_field is None:
            return _finish_session(session)

        filter_query = _filter_from_confirmed(session["confirmed"])
        candidates = _distinct_values(next_field, filter_query)
        extracted_value = session["extracted"].get(next_field)

        if next_field in ("population", "line"):
            action = _optional_slot_action(next_field, candidates, extracted_value)
            if action == "omit":
                # Leave unfiltered — do not invent a subtype/line
                session["confirmed"][next_field] = None
                continue
            if action == "ask":
                session["status"] = "needs_input"
                session["current_slot"] = next_field
                session["proposed_value"] = None
                session["candidates"] = candidates[:50]
                session["allow_skip"] = True
                label = SLOT_LABELS[next_field]
                session["message"] = (
                    f"Your question did not specify a {label}. "
                    f"Which {label} should I use? {', '.join(candidates[:12])}"
                    + ("…" if len(candidates) > 12 else "")
                    + ' Or skip to leave it unfiltered.'
                )
                _save_session(session)
                return _session_response(session)

        if not candidates:
            if next_field in ("population", "line"):
                session["confirmed"][next_field] = None
                continue
            session["status"] = "error"
            session["current_slot"] = next_field
            session["proposed_value"] = None
            session["candidates"] = []
            session["message"] = (
                f"No {SLOT_LABELS[next_field]} values found for the confirmed filters. "
                "Please start over."
            )
            _save_session(session)
            return _session_response(session)

        if extracted_value:
            match = find_closest_match(
                str(extracted_value), candidates, next_field, filter_query
            )
            if match is None:
                session["status"] = "needs_input"
                session["current_slot"] = next_field
                session["proposed_value"] = None
                session["candidates"] = candidates[:50]
                session["allow_skip"] = next_field in ("population", "line")
                session["message"] = _no_match_message(next_field, str(extracted_value))
                # Clear extracted so retry uses user-provided value
                session["extracted"][next_field] = None
                _save_session(session)
                return _session_response(session)

            session["status"] = "needs_confirmation"
            session["current_slot"] = next_field
            session["proposed_value"] = match
            session["candidates"] = candidates[:50]
            session["allow_skip"] = False
            session["message"] = _confirm_message(next_field, match)
            _save_session(session)
            return _session_response(session)

        # Required slot with nothing extracted — ask the user
        session["status"] = "needs_input"
        session["current_slot"] = next_field
        session["proposed_value"] = None
        session["candidates"] = candidates[:50]
        session["allow_skip"] = False
        session["message"] = _ask_for_value_message(
            next_field, candidates, session["extracted"]
        )
        _save_session(session)
        return _session_response(session)


def _finish_session(session: dict) -> dict:
    query = _filter_from_confirmed(session["confirmed"])
    db = get_db()
    print(f"[exact_match_conversational] final mongo query: {query}")
    records = list(db.payerLatestAccess.find(query, {"_id": 0}).limit(200))
    records = latest_timestamp_wins(records)
    print(f"[exact_match_conversational] confirmed slots: {session['confirmed']}")
    print(f"[exact_match_conversational] {len(records)} records after latest-timestamp-wins")
    if records:
        sample = records[0]
        print(
            "[exact_match_conversational] sample record keys: "
            f"book={sample.get('book')!r} coverage={sample.get('coverage')!r} "
            f"access={(sample.get('accessData') or {}).get('access')!r}"
        )

    session["status"] = "complete"
    session["records"] = records
    session["message"] = ""
    _save_session(session)
    response = _session_response(session)
    response["records"] = records
    response["confirmed_slots"] = {
        k: v for k, v in session["confirmed"].items() if v is not None
    }
    # Session can be cleared after client receives the final answer
    _delete_session(session["id"])
    return response



def _session_response(session: dict) -> dict:
    confirmed = {
        k: v for k, v in session.get("confirmed", {}).items() if v is not None
    }
    return {
        "status": session["status"],
        "session_id": session["id"],
        "message": session.get("message", ""),
        "current_slot": session.get("current_slot"),
        "proposed_value": session.get("proposed_value"),
        "candidates": session.get("candidates", []),
        "confirmed_slots": confirmed,
        "allow_skip": bool(session.get("allow_skip")),
        "question": session.get("question"),
    }


def start_conversation(question: str) -> dict:
    extracted = extract_slots(question)
    print(f"[exact_match_conversational] extracted: {extracted}")
    session = _create_session(question, extracted)
    _save_session(session)
    return _advance_to_next_slot(session)


def continue_conversation(
    session_id: str,
    action: str,
    value: Optional[str] = None,
) -> dict:
    session = _get_session(session_id)
    if not session:
        return {
            "status": "error",
            "session_id": session_id,
            "message": "Session expired. Please ask your question again.",
            "current_slot": None,
            "proposed_value": None,
            "candidates": [],
            "confirmed_slots": {},
        }

    field = session.get("current_slot")
    if not field:
        return start_conversation(session["question"])

    action = (action or "").lower().strip()

    if action == "skip":
        if field not in ("population", "line"):
            return {
                "status": "error",
                "session_id": session_id,
                "message": f"Cannot skip {SLOT_LABELS.get(field, field)}.",
                "current_slot": field,
                "proposed_value": session.get("proposed_value"),
                "candidates": session.get("candidates", []),
                "confirmed_slots": {
                    k: v
                    for k, v in session.get("confirmed", {}).items()
                    if v is not None
                },
                "allow_skip": False,
            }
        session["confirmed"][field] = None
        session["extracted"][field] = None
        session["allow_skip"] = False
        return _advance_to_next_slot(session)

    if action == "confirm":
        proposed = session.get("proposed_value")
        if not proposed:
            session["status"] = "needs_input"
            session["message"] = _ask_for_value_message(
                field,
                session.get("candidates") or [],
                session.get("extracted"),
            )
            _save_session(session)
            return _session_response(session)
        session["confirmed"][field] = proposed
        session["extracted"][field] = None
        session["allow_skip"] = False
        return _advance_to_next_slot(session)

    if action == "reject":
        session["extracted"][field] = None
        session["proposed_value"] = None
        session["status"] = "needs_input"
        filter_query = _filter_from_confirmed(session["confirmed"])
        candidates = session.get("candidates") or _distinct_values(field, filter_query)
        session["candidates"] = candidates[:50]
        session["allow_skip"] = field in ("population", "line")
        session["message"] = (
            f'Okay — what {SLOT_LABELS.get(field, field)} did you mean?'
        )
        _save_session(session)
        return _session_response(session)

    if action in ("provide", "submit"):
        user_value = (value or "").strip()
        if not user_value:
            session["status"] = "needs_input"
            session["message"] = _ask_for_value_message(
                field,
                session.get("candidates") or [],
                session.get("extracted"),
            )
            _save_session(session)
            return _session_response(session)

        filter_query = _filter_from_confirmed(session["confirmed"])
        candidates = _distinct_values(field, filter_query)
        match = find_closest_match(user_value, candidates, field, filter_query)
        if match is None:
            session["status"] = "needs_input"
            session["proposed_value"] = None
            session["candidates"] = candidates[:50]
            session["allow_skip"] = field in ("population", "line")
            session["message"] = _no_match_message(field, user_value)
            _save_session(session)
            return _session_response(session)

        session["status"] = "needs_confirmation"
        session["proposed_value"] = match
        session["candidates"] = candidates[:50]
        session["allow_skip"] = False
        session["message"] = _confirm_message(field, match)
        _save_session(session)
        return _session_response(session)

    return {
        "status": "error",
        "session_id": session_id,
        "message": f"Unknown action: {action}",
        "current_slot": field,
        "proposed_value": session.get("proposed_value"),
        "candidates": session.get("candidates", []),
        "confirmed_slots": {
            k: v for k, v in session.get("confirmed", {}).items() if v is not None
        },
        "allow_skip": False,
    }


def exact_match_conversational_retrieval(
    question: str, slots: Optional[dict] = None
) -> list[dict]:
    """Non-interactive path for eval: auto-accept closest matches."""
    extracted = slots if slots is not None else extract_slots(question)

    confirmed: dict[str, Any] = {}
    for field in SLOT_ORDER:
        filter_query = _filter_from_confirmed(confirmed)
        candidates = _distinct_values(field, filter_query)
        user_value = extracted.get(field)

        # Never invent subtype / line of therapy when the question omitted them
        if field in ("population", "line") and not user_value:
            continue

        if user_value:
            match = find_closest_match(str(user_value), candidates, field, filter_query)
            if match:
                confirmed[field] = match
                continue
            confirmed[field] = user_value

    query = _filter_from_confirmed(confirmed)
    if not query:
        return []

    print(f"[exact_match_conversational] auto query: {query}")
    db = get_db()
    records = list(db.payerLatestAccess.find(query, {"_id": 0}).limit(200))
    records = latest_timestamp_wins(records)
    print(f"[exact_match_conversational] {len(records)} records found")
    return records

