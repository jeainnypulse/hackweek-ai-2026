"""Slot extraction for payerLatestLives lookups (org, book, coverage, territory)."""

from __future__ import annotations

import re
from typing import Optional

from retrieval.extract_utils import invoke_json_extract, slot_text

LIVES_DEFAULT_SOURCE = "DRG"
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
US_STATE_CODES = frozenset(US_STATE_TO_CODE.values())
_STATE_NAMES_LONGEST_FIRST = tuple(
    sorted(US_STATE_TO_CODE.keys(), key=len, reverse=True)
)

_CITY_PHRASES = (
    "new york city",
    "nyc",
    "los angeles",
    "san francisco",
    "chicago",
    "houston",
    "dallas",
    "miami",
    "boston",
    "philadelphia",
    "seattle",
    "atlanta",
    "denver",
    "phoenix",
)
_REGION_PHRASES = (
    "northeast",
    "southeast",
    "southwest",
    "midwest",
    "mid-atlantic",
    "mid atlantic",
    "west coast",
    "east coast",
    "pacific northwest",
    "new england",
)

LIVES_SLOT_EXTRACTION_PROMPT = """Extract structured slots from this payer LIVES / enrollment question. Return ONLY valid JSON with no explanation or markdown.

Slots to extract:
- organization: specific payer name (e.g. "Aetna", "UnitedHealth Group"). Return null if not specified.
- book: book of business (e.g. "Commercial", "Medicare", "Managed Medicaid", "Health Exchange"). Return null if not specified.
- coverage: "Medical" or "Pharmacy" if mentioned. Return null if not specified.
- territoryName: 2-letter US state code if the question locates lives in a US state. Otherwise null.

Territory rules:
- Accept full state names ("California", "New Jersey", "North Carolina") and 2-letter codes ("CA", "NJ", "NC").
- Always return the 2-letter code (e.g. "CA" not "California").
- Only extract a state when it is a geographic constraint ("in California", "in FL"), not when the state is part of a payer name ("Horizon BlueCross BlueShield New Jersey nationally" → territoryName null).
- "nationally", "across the US", "in the United States" → territoryName null (National default).
- Do NOT extract cities ("New York City", "Los Angeles") or regions ("the Northeast", "the Midwest", "the West Coast") as a state — return null.

Examples:
  "How many commercial medical lives does Aetna cover in California?" → territoryName: "CA"
  "What is BCBS Michigan's Medicare enrollment in Michigan?" → territoryName: "MI"
  "How many commercial medical lives does Cigna have nationally?" → territoryName: null
  "What is UnitedHealth Group's commercial medical lives count?" → territoryName: null
  "How many Medicare lives does Horizon BlueCross BlueShield New Jersey have nationally?" → territoryName: null

Do NOT extract product, indication, line of therapy, or population. Lives data is payer enrollment, not product access.

Return format:
{"organization": "Aetna", "book": "Commercial", "coverage": "Pharmacy", "territoryName": null}

Question: """


def _question_key(question: str) -> str:
    return " ".join(question.casefold().split())


def _has_phrase(key: str, phrases: tuple[str, ...]) -> bool:
    return any(p in key for p in phrases)


def territory_raw_from_slots(slots: dict) -> Optional[str]:
    for key in ("territoryName", "territory"):
        value = slot_text(slots.get(key))
        if value:
            return value
    return None


def state_code_from_in_clause(question: str) -> Optional[str]:
    """Geographic 'in <state>' only — does not use state words inside payer names."""
    key = _question_key(question)
    if _has_phrase(key, _CITY_PHRASES) or _has_phrase(key, _REGION_PHRASES):
        return None
    for name in _STATE_NAMES_LONGEST_FIRST:
        if f" in {name}" in key or f" in the {name}" in key:
            return US_STATE_TO_CODE[name]
    match = re.search(r"\bin\s+([A-Za-z]{2})\b", question)
    if match:
        code = match.group(1).upper()
        if code in US_STATE_CODES:
            return code
    return None


def coerce_lives_territory_slot(question: str, slots: dict) -> None:
    """Same extract pass. Only 'in <state>' is geographic; payer-name states are ignored."""
    key = _question_key(question)
    if _has_phrase(key, _CITY_PHRASES) or _has_phrase(key, _REGION_PHRASES):
        raw = None
    else:
        raw = state_code_from_in_clause(question)
    if raw:
        slots["territoryName"] = raw
        slots["territory"] = raw
    else:
        slots["territoryName"] = None
        slots["territory"] = None


def normalize_territory(raw: Optional[str]) -> tuple[str, str]:
    if raw is None or str(raw).strip() in ("", "null"):
        return LIVES_DEFAULT_TERRITORY_TYPE, LIVES_DEFAULT_TERRITORY_NAME
    text = str(raw).strip()
    key = " ".join(text.casefold().split())
    if key in {"us", "usa", "united states", "national", "nationwide"}:
        return LIVES_DEFAULT_TERRITORY_TYPE, LIVES_DEFAULT_TERRITORY_NAME
    if len(text) == 2 and text.isalpha():
        code = text.upper()
        if code in US_STATE_CODES:
            return "U.S. State", code
        return LIVES_DEFAULT_TERRITORY_TYPE, LIVES_DEFAULT_TERRITORY_NAME
    code = US_STATE_TO_CODE.get(key)
    if code:
        return "U.S. State", code
    return LIVES_DEFAULT_TERRITORY_TYPE, LIVES_DEFAULT_TERRITORY_NAME


def apply_lives_defaults(slots: dict, linked: dict) -> None:
    """Territory is resolved last and is never a clarify dimension."""
    territory_type, territory_name = normalize_territory(territory_raw_from_slots(slots))
    linked["territoryType"] = territory_type
    linked["territoryName"] = territory_name
    linked.pop("territory", None)
    linked["source"] = LIVES_DEFAULT_SOURCE
    coverage = slots.get("coverage")
    if coverage and not linked.get("coverage"):
        linked["coverage"] = coverage


def extract_lives_slots(question: str) -> dict:
    slots = invoke_json_extract(LIVES_SLOT_EXTRACTION_PROMPT, question)
    coerce_lives_territory_slot(question, slots)
    return slots
