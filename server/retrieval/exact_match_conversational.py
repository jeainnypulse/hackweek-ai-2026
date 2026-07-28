"""Multi-step conversational slot extraction against live Mongo data.

Used when retrieval_method = "exact_match_conversational".

Flow:
1. Extract slots from the question.
2. Auto-confirm confident matches for identity slots (no Yes/No double-confirm).
3. Ask only for missing/unmatched identity slots.
4. For remaining coverage / subtype / line: present cascading dropdowns.
5. Once the user submits those selections, query payerLatestAccess.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from typing import Any, Optional, Union

from bedrock import invoke_claude
from db import get_db

# Identity slots are auto-confirmed when matched; cascade slots use dropdowns.
IDENTITY_SLOTS = [
    "regimen",
    "indication",
    "book",
    "organization",
]
CASCADE_SLOTS = [
    "coverage",
    "population",
    "line",
]
SLOT_ORDER = IDENTITY_SLOTS + CASCADE_SLOTS

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
    "regimen": "Product",
    "indication": "Indication",
    "book": "Book",
    "coverage": "Coverage",
    "organization": "Payer",
    "population": "Subtype",
    "line": "Line",
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

    # Shared synonym table + fuzzy (entity_linking) before Claude
    try:
        from entity_linking import resolve_fuzzy, resolve_synonym, set_catalogs

        syn = resolve_synonym(field, user_value)
        if syn.get("status") == "matched" and syn.get("value") in candidates:
            _store_alias(field, user_value, syn["value"], filter_query)
            return syn["value"]

        set_catalogs({field: candidates})
        fuzzy = resolve_fuzzy(field, user_value, {field: candidates})
        if fuzzy.get("status") == "matched" and fuzzy.get("value") in candidates:
            _store_alias(field, user_value, fuzzy["value"], filter_query)
            return fuzzy["value"]
    except Exception as exc:
        print(f"[exact_match_conversational] entity_linking fallback skipped: {exc}")

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


def _population_candidates(candidates: list[str]) -> list[str]:
    """Real subtype values (excludes placeholder 'No Subtype Specified', etc.)."""
    return [c for c in candidates if _normalize_key(c) not in SKIP_POPULATION_VALUES]


def _population_dropdown_options(candidates: list[str]) -> list[str]:
    """Options for the subtype dropdown.

    If any real subtype exists, include placeholders like 'No Subtype Specified'
    too — they are distinct data slices. If only placeholders exist, return []
    so the slot can be omitted.
    """
    if not _population_candidates(candidates):
        return []
    return [
        c
        for c in candidates
        if _normalize_key(c) not in {"", "n/a", "na", "none"}
    ]


def _optional_cascade_action(
    field: str, candidates: list[str], extracted_value: Optional[str]
) -> str:
    """How to handle population/line in the cascade form.

    Returns:
      - "resolve" — user mentioned a value; match/confirm it
      - "ask" — user did not mention it; include in cascade form
      - "omit" — safe to leave unfiltered
    """
    if extracted_value:
        return "resolve"

    if field == "population":
        if not _population_candidates(candidates):
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


def _ask_for_value_message(
    field: str,
    candidates: list[str],
) -> str:
    label = SLOT_LABELS.get(field, field)
    if not candidates:
        return (
            f"No {label} values found for the confirmed filters. "
            "Please start over with a different question."
        )
    preview = candidates[:12]
    listed = ", ".join(preview)
    suffix = "…" if len(candidates) > 12 else ""
    return f"Which {label}? Examples: {listed}{suffix}"


def _confirmed_summary_message(confirmed: dict) -> str:
    lines = ["Here's what I have confirmed", ""]
    for field in SLOT_ORDER:
        value = confirmed.get(field)
        if value:
            lines.append(f"- {SLOT_LABELS[field]}: {value}")
    return "\n".join(lines)


def _create_session(question: str, extracted: dict) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "question": question,
        "extracted": extracted,
        "confirmed": {},
        "current_slot": None,
        "proposed_value": None,
        "candidates": [],
        "cascade": None,
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


def _parse_selections(value: Optional[Union[str, dict]]) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {k: _clean_slot_value(v) for k, v in value.items()}
    text = str(value).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: _clean_slot_value(v) for k, v in parsed.items()}


def _resolve_selection(value: Optional[str], options: list[str]) -> Optional[str]:
    if not value:
        return None
    if value in options:
        return value
    for option in options:
        if _normalize_key(option) == _normalize_key(value):
            return option
    return None


def _build_cascade_state(
    confirmed: dict, selections: Optional[dict] = None
) -> dict:
    """Build cascading dropdown options for coverage → subtype → line."""
    selections = selections or {}
    # Identity filters only — cascade fields come from live selections
    identity_filter = {
        k: v for k, v in confirmed.items() if k in IDENTITY_SLOTS and v is not None
    }

    coverage_options = _distinct_values("coverage", identity_filter)
    selected_coverage = confirmed.get("coverage") or _resolve_selection(
        selections.get("coverage"), coverage_options
    )
    # Sole option → auto-select so downstream cascade can resolve
    if not selected_coverage and len(coverage_options) == 1:
        selected_coverage = coverage_options[0]

    population_options: list[str] = []
    if selected_coverage:
        population_options = _population_dropdown_options(
            _distinct_values(
                "population", {**identity_filter, "coverage": selected_coverage}
            )
        )
    selected_population = None
    if "population" in confirmed and confirmed.get("population"):
        selected_population = confirmed["population"]
    else:
        selected_population = _resolve_selection(
            selections.get("population"), population_options
        )
    if not selected_population and len(population_options) == 1:
        selected_population = population_options[0]

    # Lines under the widest resolved scope (identity → +coverage → +subtype).
    # If only one line exists at this scope, auto-confirm it — no need to wait
    # for subtype when every subtype shares the same line (e.g. always 1L+).
    line_scope = dict(identity_filter)
    if selected_coverage:
        line_scope["coverage"] = selected_coverage
    if selected_population:
        line_scope["population"] = selected_population
    scoped_lines = _distinct_values("line", line_scope)

    line_options: list[str] = []
    cascade_ready_for_line = bool(
        selected_coverage and (selected_population or not population_options)
    )
    if cascade_ready_for_line:
        line_options = scoped_lines
    elif len(scoped_lines) == 1:
        # Sole line across remaining combinations — treat as resolved options
        line_options = scoped_lines

    selected_line = None
    if "line" in confirmed and confirmed.get("line"):
        selected_line = confirmed["line"]
    else:
        selected_line = _resolve_selection(selections.get("line"), line_options)
    if not selected_line and len(line_options) == 1:
        selected_line = line_options[0]

    # Which controls to show
    show_coverage = "coverage" not in confirmed or not confirmed.get("coverage")
    show_population = bool(population_options) and (
        "population" not in confirmed or not confirmed.get("population")
    )
    # Show subtype dropdown placeholder until coverage is chosen if subtypes exist anywhere
    if show_coverage and not selected_coverage:
        has_any_subtype = any(
            _population_candidates(
                _distinct_values("population", {**identity_filter, "coverage": cov})
            )
            for cov in coverage_options
        )
        show_population = has_any_subtype

    # Only ask for Line when there is a real choice (>1 option)
    line_already_confirmed = bool(confirmed.get("line"))
    show_line = (not line_already_confirmed) and len(scoped_lines) > 1

    pending_slots = []
    if show_coverage and coverage_options:
        pending_slots.append("coverage")
    if show_population:
        pending_slots.append("population")
    if show_line:
        pending_slots.append("line")

    return {
        "pending_slots": pending_slots,
        "selections": {
            "coverage": selected_coverage,
            "population": selected_population,
            "line": selected_line,
        },
        "options": {
            "coverage": coverage_options,
            "population": population_options,
            "line": line_options,
        },
        "allow_skip": {
            "population": True,
            "line": True,
        },
    }


def _cascade_needed(confirmed: dict) -> bool:
    base = _filter_from_confirmed(confirmed)
    if "coverage" not in confirmed:
        if _distinct_values("coverage", base):
            return True
    coverage = confirmed.get("coverage")
    if coverage and "population" not in confirmed:
        if _population_candidates(
            _distinct_values("population", {**base, "coverage": coverage})
        ):
            return True
    if coverage and "line" not in confirmed:
        line_filter = {**base, "coverage": coverage}
        if confirmed.get("population"):
            line_filter["population"] = confirmed["population"]
        if _distinct_values("line", line_filter):
            return True
    # Coverage not yet chosen — check if subtypes/lines exist at all under base
    if "coverage" not in confirmed:
        for cov in _distinct_values("coverage", base):
            if _population_candidates(
                _distinct_values("population", {**base, "coverage": cov})
            ):
                return True
            if _distinct_values("line", {**base, "coverage": cov}):
                return True
    return False


def _auto_confirm_sole_cascade_options(
    session: dict, selections: Optional[dict] = None
) -> dict:
    """Move sole-option cascade fields into confirmed (don't ask the user)."""
    selections = {
        field: session["confirmed"].get(field) or (selections or {}).get(field)
        for field in CASCADE_SLOTS
    }
    # Iterate — confirming coverage can unlock a sole subtype/line, etc.
    for _ in range(len(CASCADE_SLOTS) + 1):
        cascade = _build_cascade_state(session["confirmed"], selections)
        options = cascade["options"]
        picked = cascade["selections"]
        progressed = False
        for field in CASCADE_SLOTS:
            if session["confirmed"].get(field):
                continue
            field_options = options.get(field) or []
            value = picked.get(field)
            if len(field_options) == 1 and value == field_options[0]:
                session["confirmed"][field] = value
                selections[field] = value
                progressed = True
                print(
                    f"[exact_match_conversational] auto-confirmed sole "
                    f"{field}={value!r}"
                )
        if not progressed:
            break
        # Refresh selections from latest picks for the next pass
        for field in CASCADE_SLOTS:
            if picked.get(field):
                selections[field] = picked[field]
    return selections


def _present_cascade(session: dict, selections: Optional[dict] = None) -> dict:
    selections = _auto_confirm_sole_cascade_options(session, selections)
    cascade = _build_cascade_state(session["confirmed"], selections)

    # Everything resolvable was sole-option — query immediately
    if not cascade["pending_slots"]:
        for field in CASCADE_SLOTS:
            if field not in session["confirmed"]:
                session["confirmed"][field] = None
        return _finish_session(session)

    session["status"] = "needs_cascade_input"
    session["current_slot"] = None
    session["proposed_value"] = None
    session["candidates"] = cascade["options"].get("coverage", [])
    session["cascade"] = cascade
    session["allow_skip"] = False
    session["message"] = (
        f"{_confirmed_summary_message(session['confirmed'])}\n\n"
        "I would need some additional information from you to give you an accurate response."
    )
    _save_session(session)
    return _session_response(session)


def _try_auto_confirm_field(session: dict, field: str) -> Optional[str]:
    """Attempt to auto-confirm a field from extraction. Returns outcome.

    Returns one of: "confirmed", "omit", "ask", "no_match"
    """
    filter_query = _filter_from_confirmed(session["confirmed"])
    candidates = _distinct_values(field, filter_query)
    extracted_value = session["extracted"].get(field)

    if field in ("population", "line"):
        action = _optional_cascade_action(field, candidates, extracted_value)
        if action == "omit":
            session["confirmed"][field] = None
            return "omit"
        if action == "ask":
            return "ask"

    if not candidates:
        if field in ("population", "line"):
            session["confirmed"][field] = None
            return "omit"
        return "ask"

    if not extracted_value:
        return "ask"

    match = find_closest_match(
        str(extracted_value), candidates, field, filter_query
    )
    if match is None:
        return "no_match"

    # Auto-confirm confident matches — no Yes/No double-confirm
    session["confirmed"][field] = match
    session["extracted"][field] = None
    print(f"[exact_match_conversational] auto-confirmed {field}={match!r}")
    return "confirmed"


def _ask_identity_slot(
    session: dict,
    field: str,
    *,
    no_match_value: Optional[str] = None,
) -> dict:
    filter_query = _filter_from_confirmed(session["confirmed"])
    candidates = _distinct_values(field, filter_query)
    session["status"] = "needs_input"
    session["current_slot"] = field
    session["proposed_value"] = None
    session["candidates"] = candidates[:50]
    session["cascade"] = None
    session["allow_skip"] = False
    if no_match_value:
        session["message"] = _no_match_message(field, no_match_value)
        session["extracted"][field] = None
    elif not candidates:
        session["status"] = "error"
        session["message"] = (
            f"No {SLOT_LABELS[field]} values found for the confirmed filters. "
            "Please start over."
        )
    else:
        session["message"] = _ask_for_value_message(field, candidates)
    _save_session(session)
    return _session_response(session)


def _advance_to_next_slot(session: dict) -> dict:
    """Auto-confirm matches, then ask for identity gaps or cascade dropdowns."""
    # 1) Auto-confirm EVERY matchable identity slot before asking for gaps.
    #    Otherwise a missing Book would hide an already-extracted Payer (Aetna)
    #    from "Here's what I have confirmed" until Book is selected.
    pending_identity: list[tuple[str, str, Optional[str]]] = []
    for field in IDENTITY_SLOTS:
        if field in session["confirmed"]:
            continue
        outcome = _try_auto_confirm_field(session, field)
        if outcome in ("ask", "no_match"):
            extracted = session["extracted"].get(field)
            pending_identity.append((field, outcome, extracted))

    if pending_identity:
        field, outcome, extracted = pending_identity[0]
        return _ask_identity_slot(
            session,
            field,
            no_match_value=str(extracted) if outcome == "no_match" and extracted else None,
        )

    # 2) Cascade slots — auto-confirm any extracted matches
    for field in CASCADE_SLOTS:
        if field in session["confirmed"]:
            continue
        outcome = _try_auto_confirm_field(session, field)
        if outcome == "no_match":
            # Clear bad extraction; user will pick from cascade dropdown
            session["extracted"][field] = None

    # 3) If cascade fields still needed, show cascading dropdowns
    if _cascade_needed(session["confirmed"]):
        prefill = {
            field: session["confirmed"].get(field)
            for field in CASCADE_SLOTS
            if session["confirmed"].get(field)
        }
        response = _present_cascade(session, prefill)
        # If nothing actually pending (e.g. empty option sets), finish
        if not (response.get("cascade") or {}).get("pending_slots"):
            for field in CASCADE_SLOTS:
                if field not in session["confirmed"]:
                    session["confirmed"][field] = None
            return _finish_session(session)
        return response

    return _finish_session(session)


def _mongo_shell_literal(value: Any) -> str:
    """Format a Python value as a mongosh / MongoDB shell literal."""
    return json.dumps(value, ensure_ascii=False)


def build_mongo_agg_pipeline(match_query: dict) -> str:
    """Return a copy-pasteable mongosh aggregate() call for the final lookup."""
    match_literal = _mongo_shell_literal(match_query)
    return (
        "db.payerLatestAccess.aggregate([\n"
        f"  {{ $match: {match_literal} }},\n"
        "  { $sort: { timestamp: -1 } },\n"
        "  {\n"
        "    $group: {\n"
        "      _id: {\n"
        "        regimen: \"$regimen\",\n"
        "        indication: \"$indication\",\n"
        "        book: \"$book\",\n"
        "        coverage: \"$coverage\",\n"
        "        organization: \"$organization\",\n"
        "        population: \"$population\",\n"
        "        line: \"$line\"\n"
        "      },\n"
        "      doc: { $first: \"$$ROOT\" }\n"
        "    }\n"
        "  },\n"
        "  { $replaceRoot: { newRoot: \"$doc\" } },\n"
        "  { $project: { _id: 0 } }\n"
        "])"
    )


def _finish_session(session: dict) -> dict:
    query = _filter_from_confirmed(session["confirmed"])
    db = get_db()
    print(f"[exact_match_conversational] final mongo query: {query}")
    records = list(db.payerLatestAccess.find(query, {"_id": 0}).limit(200))
    records = latest_timestamp_wins(records)
    mongo_pipeline = build_mongo_agg_pipeline(query)
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
    response["mongo_pipeline"] = mongo_pipeline
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
        "cascade": session.get("cascade"),
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
    value: Optional[Union[str, dict]] = None,
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
            "cascade": None,
        }

    action = (action or "").lower().strip()

    # Refresh cascading dropdown options as the user selects coverage/subtype
    if action == "cascade_options":
        selections = _parse_selections(value)
        return _present_cascade(session, selections)

    # Submit coverage / subtype / line selections, then query payerLatestAccess
    if action == "submit_cascade":
        selections = _parse_selections(value)
        cascade = _build_cascade_state(session["confirmed"], selections)
        options = cascade["options"]
        picked = cascade["selections"]

        def _cascade_error(msg: str) -> dict:
            session["status"] = "needs_cascade_input"
            session["cascade"] = cascade
            session["message"] = (
                f"{_confirmed_summary_message(session['confirmed'])}\n\n{msg}"
            )
            _save_session(session)
            return _session_response(session)

        coverage = picked.get("coverage")
        if not session["confirmed"].get("coverage"):
            if not coverage or coverage not in options["coverage"]:
                return _cascade_error("Please select a Coverage value.")
            session["confirmed"]["coverage"] = coverage

        # Subtype — required only when options exist under the chosen coverage
        pop_options = options["population"]
        if "population" not in session["confirmed"]:
            population = picked.get("population")
            if pop_options:
                if not population or population not in pop_options:
                    return _cascade_error(
                        "Please select a Subtype for the chosen Coverage."
                    )
                session["confirmed"]["population"] = population
            else:
                session["confirmed"]["population"] = None

        # Line — required only when options exist under coverage (+ subtype)
        if "line" not in session["confirmed"]:
            line = picked.get("line")
            line_filter = _filter_from_confirmed(session["confirmed"])
            line_options = _distinct_values("line", line_filter)
            if line_options:
                if not line or line not in line_options:
                    refreshed = _present_cascade(
                        session,
                        {
                            "coverage": session["confirmed"].get("coverage"),
                            "population": session["confirmed"].get("population"),
                            "line": line,
                        },
                    )
                    refreshed["message"] = (
                        f"{_confirmed_summary_message(session['confirmed'])}\n\n"
                        "Please select a Line of therapy."
                    )
                    return refreshed
                session["confirmed"]["line"] = line
            else:
                session["confirmed"]["line"] = None

        return _finish_session(session)

    field = session.get("current_slot")
    if not field:
        if session.get("status") == "needs_cascade_input":
            return _session_response(session)
        return start_conversation(session["question"])

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
                "cascade": session.get("cascade"),
            }
        session["confirmed"][field] = None
        session["extracted"][field] = None
        session["allow_skip"] = False
        return _advance_to_next_slot(session)

    # Legacy confirm/reject kept for safety — treat confirm as auto-accept
    if action == "confirm":
        proposed = session.get("proposed_value")
        if not proposed:
            return _ask_identity_slot(session, field)
        session["confirmed"][field] = proposed
        session["extracted"][field] = None
        session["allow_skip"] = False
        return _advance_to_next_slot(session)

    if action == "reject":
        session["extracted"][field] = None
        session["proposed_value"] = None
        return _ask_identity_slot(session, field)

    if action in ("provide", "submit"):
        user_value = (value if isinstance(value, str) else None) or ""
        user_value = user_value.strip()
        if not user_value:
            return _ask_identity_slot(session, field)

        filter_query = _filter_from_confirmed(session["confirmed"])
        candidates = _distinct_values(field, filter_query)
        match = find_closest_match(user_value, candidates, field, filter_query)
        if match is None:
            return _ask_identity_slot(session, field, no_match_value=user_value)

        # Auto-confirm — no second Yes/No step
        session["confirmed"][field] = match
        session["extracted"][field] = None
        session["allow_skip"] = False
        print(f"[exact_match_conversational] user-provided auto-confirmed {field}={match!r}")
        return _advance_to_next_slot(session)

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
        "cascade": session.get("cascade"),
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
