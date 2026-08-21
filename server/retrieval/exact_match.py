from __future__ import annotations

from typing import Any, Optional
import os
import uuid

from bedrock import invoke_claude
from db import get_db
from entity_linking import (
    LINK_DIMENSIONS,
    catalogs_with_lives_books,
    link_slots,
    upsert_synonym,
)
from permissions import scoped_lives_query, scoped_query
from retrieval.cascade_clarify import (
    CASCADE_SLOTS,
    IDENTITY_SLOTS,
    auto_confirm_sole_cascade_options,
    build_cascade_state,
    cascade_needed,
    distinct_values,
    filter_from_confirmed,
    parse_selections,
    resolve_selection,
)
from retrieval.intent import OUT_OF_SCOPE_MESSAGE, classify_intent
import json

# none | catalog | synonym | fuzzy | cascade | hypothesize_verify
# cascade = synonym -> catalog -> LLM hypothesize+Mongo verify (production)
ENTITY_LINK_METHOD = os.getenv("ENTITY_LINK_METHOD", "cascade")

LIVES_LINK_DIMENSIONS = ("organization", "book")
LIVES_DEFAULT_SOURCE = "MMIT"
LIVES_DEFAULT_TERRITORY_TYPE = "National"
LIVES_DEFAULT_TERRITORY_NAME = "United States"

US_STATE_TO_CODE = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

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

LIVES_SLOT_EXTRACTION_PROMPT = """Extract structured slots from this payer LIVES / enrollment question. Return ONLY valid JSON with no explanation or markdown.

Slots to extract:
- organization: specific payer name (e.g. "Aetna", "UnitedHealth Group"). Return null if not specified.
- book: book of business (e.g. "Commercial", "Medicare", "Managed Medicaid", "Medicaid"). Return null if not specified.
- coverage: "Medical" or "Pharmacy" if mentioned. Return null if not specified.
- territory: 2-letter US state code if a state is mentioned (e.g. "FL", "CA"). Return null for national / unspecified.

Do NOT extract product, indication, line of therapy, or population. Lives data is payer enrollment, not product access.

Return format:
{"organization": "Aetna", "book": "Commercial", "coverage": "Pharmacy", "territory": null}

Question: """

# session_id -> interactive clarify state
_sessions: dict[str, dict[str, Any]] = {}


def extract_slots(question: str, intent: str = "access") -> dict:
    prompt = (
        LIVES_SLOT_EXTRACTION_PROMPT
        if intent == "lives"
        else SLOT_EXTRACTION_PROMPT
    )
    response = invoke_claude(prompt + question)
    clean = response.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


def _is_lives(session: dict) -> bool:
    return session.get("intent") == "lives"


def _with_intent(session: dict, payload: dict) -> dict:
    payload["intent"] = session.get("intent") or "access"
    return payload


def _normalize_territory(raw: Optional[str]) -> tuple[str, str]:
    if raw is None or str(raw).strip() in ("", "null"):
        return LIVES_DEFAULT_TERRITORY_TYPE, LIVES_DEFAULT_TERRITORY_NAME
    text = str(raw).strip()
    key = " ".join(text.casefold().split())
    if key in {"us", "usa", "united states", "national", "nationwide"}:
        return LIVES_DEFAULT_TERRITORY_TYPE, LIVES_DEFAULT_TERRITORY_NAME
    if len(text) == 2 and text.isalpha():
        return "U.S. State", text.upper()
    code = US_STATE_TO_CODE.get(key)
    if code:
        return "U.S. State", code
    return "U.S. State", text


def _apply_lives_defaults(slots: dict, linked: dict) -> None:
    territory_type, territory_name = _normalize_territory(slots.get("territory"))
    linked["territoryType"] = territory_type
    linked["territoryName"] = territory_name
    linked["source"] = LIVES_DEFAULT_SOURCE
    coverage = slots.get("coverage")
    if coverage and not linked.get("coverage"):
        linked["coverage"] = coverage


def _organization_to_slug(organization: str) -> Optional[str]:
    if not organization:
        return None
    slugs = [
        s
        for s in get_db().payerLatestAccess.distinct(
            "slug", {"organization": organization}
        )
        if isinstance(s, str) and s.strip()
    ]
    if not slugs:
        return None
    if len(slugs) > 1:
        print(
            f"[exact_match_entity_linker] multiple slugs for {organization!r}: {slugs}"
        )
    return slugs[0]


def _orgs_for_slugs(slugs: list[str]) -> list[str]:
    if not slugs:
        return []
    orgs = [
        o
        for o in get_db().payerLatestAccess.distinct(
            "organization", {"slug": {"$in": slugs}}
        )
        if isinstance(o, str) and o.strip()
    ]
    return sorted(set(orgs), key=str.casefold)


def _lives_mongo_filter(slots: dict, drop_fields: Optional[set[str]] = None) -> dict:
    drop = set(drop_fields or ())
    query: dict[str, Any] = {}
    if "slug" not in drop and "organization" not in drop:
        org = slots.get("organization")
        if org:
            slug = _organization_to_slug(str(org))
            if slug:
                query["slug"] = slug
    for field in ("book", "coverage", "territoryType", "territoryName", "source"):
        if field in drop:
            continue
        value = slots.get(field)
        if value is not None and value != "":
            query[field] = value
    if "source" not in query and "source" not in drop:
        query["source"] = LIVES_DEFAULT_SOURCE
    if "territoryType" not in query and "territoryType" not in drop:
        query["territoryType"] = LIVES_DEFAULT_TERRITORY_TYPE
    if "territoryName" not in query and "territoryName" not in drop:
        query["territoryName"] = LIVES_DEFAULT_TERRITORY_NAME
    return query


def _distinct_for_session(session: dict, field: str, filt: dict) -> list[str]:
    if not _is_lives(session):
        return distinct_values(field, filt)
    if field == "organization":
        lives_filt = _lives_mongo_filter(filt, drop_fields={"organization", "slug"})
        slugs = distinct_values(
            "slug",
            lives_filt,
            collection="payerLatestLives",
            scoped_fn=scoped_lives_query,
        )
        return _orgs_for_slugs(slugs)
    lives_filt = _lives_mongo_filter(filt, drop_fields={field, "organization"})
    if field != "slug" and filt.get("organization"):
        slug = _organization_to_slug(str(filt["organization"]))
        if slug:
            lives_filt["slug"] = slug
    return distinct_values(
        field,
        lives_filt,
        collection="payerLatestLives",
        scoped_fn=scoped_lives_query,
    )


def _query_lives_records(slots: dict) -> list[dict]:
    query = _lives_mongo_filter(slots)
    if "slug" not in query:
        print("[exact_match_entity_linker] lives query skipped: no slug")
        return []
    db = get_db()
    scoped = scoped_lives_query(query)
    records = list(db.payerLatestLives.find(scoped, {"_id": 0}).limit(50))
    org = slots.get("organization")
    if org:
        for record in records:
            record.setdefault("organization", org)
    print(f"[exact_match_entity_linker] {len(records)} lives records for {query}")
    return records


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


def _query_records(slots: dict, intent: str = "access") -> list[dict]:
    if intent == "lives":
        return _query_lives_records(slots)
    query = {
        k: v
        for k, v in slots.items()
        if v is not None
        and v != ""
        and k not in ("territory", "territoryType", "territoryName", "source")
    }
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
    dimensions = LIVES_LINK_DIMENSIONS if _is_lives(session) else LINK_DIMENSIONS
    identity_slots = LIVES_LINK_DIMENSIONS if _is_lives(session) else IDENTITY_SLOTS
    for dimension in dimensions:
        info = session["link_info"].get(dimension) or {}
        if info.get("status") not in ("ambiguous", "unknown"):
            continue
        if linked.get(dimension):
            continue
        filt = {
            k: v
            for k, v in linked.items()
            if k in identity_slots and k != dimension and v
        }
        options = _distinct_for_session(session, dimension, filt)
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


def _first_pending_link(link_info: dict, *, lives: bool = False) -> Optional[tuple[str, dict]]:
    """Prefer ambiguous-with-candidates, then unknown-with-candidates."""
    dimensions = LIVES_LINK_DIMENSIONS if lives else LINK_DIMENSIONS
    for status in ("ambiguous", "unknown"):
        for dimension in dimensions:
            info = link_info.get(dimension) or {}
            if info.get("status") != status:
                continue
            cands = info.get("candidates") or []
            if cands:
                return dimension, info
    return None


def _scoped_candidates(session: dict, dimension: str, fallback: list[str]) -> list[str]:
    identity_slots = LIVES_LINK_DIMENSIONS if _is_lives(session) else IDENTITY_SLOTS
    filt = {
        k: v
        for k, v in session["linked"].items()
        if k in identity_slots and k != dimension and v
    }
    scoped = _distinct_for_session(session, dimension, filt)
    if not scoped:
        return list(fallback)
    if fallback:
        overlap = [v for v in scoped if v in set(fallback)]
        return overlap or scoped
    return scoped


def _linker_clarify_response(session: dict) -> dict:
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


def _present_lives_cascade(session: dict, selections: Optional[dict] = None) -> dict:
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


def _present_cascade(session: dict, selections: Optional[dict] = None) -> dict:
    if _is_lives(session):
        return _present_lives_cascade(session, selections)
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
    records = _query_records(session["linked"], intent=session.get("intent") or "access")
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
            if _is_lives(session):
                return _present_lives_cascade(session, selections)

            cascade = build_cascade_state(session["linked"], selections)
            options = cascade["options"]
            picked = cascade["selections"]

            def _cascade_error(msg: str) -> dict:
                session["cascade"] = cascade
                return _with_intent(
                    session,
                    {
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
                    },
                )

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
            "intent": session.get("intent"),
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
            "intent": None,
        }

    intent = classify_intent(q)
    if intent == "out_of_scope":
        return {
            "status": "complete",
            "session_id": None,
            "question": q,
            "records": [],
            "confirmed_slots": {},
            "link_info": {},
            "candidates": [],
            "cascade": None,
            "message": OUT_OF_SCOPE_MESSAGE,
            "current_slot": None,
            "intent": "out_of_scope",
        }

    slots = extract_slots(q, intent=intent)
    print(f"[exact_match_entity_linker] intent={intent} slots: {slots}")
    if intent == "lives":
        catalogs = catalogs_with_lives_books()
        linked, link_info = link_slots(
            slots,
            method=ENTITY_LINK_METHOD,
            catalogs=catalogs,
            dimensions=LIVES_LINK_DIMENSIONS,
            fill_missing=False,
        )
        _apply_lives_defaults(slots, linked)
    else:
        linked, link_info = link_slots(slots, method=ENTITY_LINK_METHOD)
    if link_info:
        print(f"[exact_match_entity_linker] linked ({ENTITY_LINK_METHOD}): {link_info}")

    sid = str(uuid.uuid4())
    session = {
        "session_id": sid,
        "question": q,
        "intent": intent,
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
