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
US_TERRITORY_TO_CODE = {
    "american samoa": "AS",
    "guam": "GU",
    "northern mariana islands": "MP",
    "puerto rico": "PR",
    "u.s. minor outlying islands": "UM",
    "u.s. virgin islands": "VI",
}
US_GEOGRAPHY_TO_CODE = {
    **US_STATE_TO_CODE,
    **US_TERRITORY_TO_CODE,
}
US_STATE_CODES = frozenset(US_GEOGRAPHY_TO_CODE.values())
LIVES_TERRITORY_OPTIONS = (
    "National",
    *(name.title() for name in sorted(US_GEOGRAPHY_TO_CODE)),
)
_STATE_NAMES_LONGEST_FIRST = tuple(
    sorted(US_GEOGRAPHY_TO_CODE.keys(), key=len, reverse=True)
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
_NATIONAL_PHRASES = (
    "nationally",
    "nationwide",
    "across the us",
    "across the u.s.",
    "in the united states",
    "in the usa",
)

LIVES_SLOT_EXTRACTION_PROMPT = """Extract structured slots from this payer LIVES / enrollment question. Return ONLY valid JSON with no explanation or markdown.

Slots to extract:
- organization: specific payer name (e.g. "Aetna", "UnitedHealth Group"). Return null if not specified.
- book: book of business (e.g. "Commercial", "Medicare", "Managed Medicaid", "Health Exchange"). Return null if not specified.
- coverage: "Medical" or "Pharmacy" if mentioned. Return null if not specified.
- territoryName: preserve the exact state/territory mention from the question
  (e.g. "California", "CA", "Puerto Rico", "Californa"). Return null when no
  state-like geography is requested.
- territoryInvalid: the exact requested geography when it is clearly a city,
  region, or non-state location (e.g. "Los Angeles", "Midwest", "Atlantis").
  Keep state-like misspellings in territoryName. Otherwise null.

Territory rules:
- Preserve full names, two-letter codes, and state-like misspellings exactly as
  written so the entity linker can canonicalize them.
- Only extract a state when it is a geographic constraint ("in California", "in FL"), not when the state is part of a payer name ("Horizon BlueCross BlueShield New Jersey nationally" → territoryName null).
- "nationally", "across the US", "in the United States" → territoryName null (National default).
- Cities, regions, and clearly non-state locations go in territoryInvalid so
  the user can clarify. State-like misspellings stay in territoryName so the
  entity linker can try deterministic typo matching.

Examples:
  "How many commercial medical lives does Aetna cover in California?" → territoryName: "California"
  "What is BCBS Michigan's Medicare enrollment in MI?" → territoryName: "MI"
  "How many commercial medical lives does Cigna have nationally?" → territoryName: null
  "What is UnitedHealth Group's commercial medical lives count?" → territoryName: null
  "How many Medicare lives does Horizon BlueCross BlueShield New Jersey have nationally?" → territoryName: null

Do NOT extract product, indication, line of therapy, or population. Lives data is payer enrollment, not product access.

Return format:
{"organization": "Aetna", "book": "Commercial", "coverage": "Pharmacy", "territoryName": "California", "territoryInvalid": null}

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


def normalized_state_code(raw: Optional[str]) -> Optional[str]:
    """Validate an extracted state name/code against the supported US states."""
    if raw is None or str(raw).strip() in ("", "null"):
        return None
    text = str(raw).strip()
    if len(text) == 2 and text.isalpha():
        code = text.upper()
        return code if code in US_STATE_CODES else None
    key = " ".join(text.casefold().split())
    return US_GEOGRAPHY_TO_CODE.get(key)


def is_national_territory(raw: Optional[str]) -> bool:
    if raw is None:
        return False
    key = " ".join(str(raw).casefold().split())
    return key in {"us", "u.s.", "usa", "united states", "national", "nationwide"}


def state_code_from_in_clause(question: str) -> Optional[str]:
    """Fallback for an explicit geographic ``in <state>`` phrase."""
    key = _question_key(question)
    if _has_phrase(key, _CITY_PHRASES) or _has_phrase(key, _REGION_PHRASES):
        return None
    for name in _STATE_NAMES_LONGEST_FIRST:
        if f" in {name}" in key or f" in the {name}" in key:
            return US_GEOGRAPHY_TO_CODE[name]
    match = re.search(r"\bin\s+([A-Za-z]{2})\b", question)
    if match:
        code = match.group(1).upper()
        if code in US_STATE_CODES:
            return code
    return None


def coerce_lives_territory_slot(question: str, slots: dict) -> None:
    """Prepare raw LLM geography for entity linking.

    Explicit national wording produces the missing-state national path. Cities
    and regions are preserved for clarification. Otherwise the raw state-like
    mention is retained; ``in <state>`` parsing is only a fallback.
    """
    key = _question_key(question)
    raw = territory_raw_from_slots(slots)
    extracted_invalid = slot_text(slots.get("territoryInvalid"))
    explicit_invalid = next(
        (
            phrase
            for phrase in (*_CITY_PHRASES, *_REGION_PHRASES)
            if phrase in key
        ),
        None,
    )

    if _has_phrase(key, _NATIONAL_PHRASES) or is_national_territory(raw):
        mention = None
        invalid = None
    elif extracted_invalid or explicit_invalid:
        mention = None
        invalid = extracted_invalid or explicit_invalid
    else:
        mention = str(raw).strip() if raw else state_code_from_in_clause(question)
        invalid = None

    if mention:
        slots["territoryName"] = mention
        slots["territory"] = mention
        slots["territoryInvalid"] = None
    else:
        slots["territoryName"] = None
        slots["territory"] = None
        slots["territoryInvalid"] = invalid


def normalize_territory(raw: Optional[str]) -> tuple[str, str]:
    if raw is None or str(raw).strip() in ("", "null"):
        return LIVES_DEFAULT_TERRITORY_TYPE, LIVES_DEFAULT_TERRITORY_NAME
    text = str(raw).strip()
    if is_national_territory(text):
        return LIVES_DEFAULT_TERRITORY_TYPE, LIVES_DEFAULT_TERRITORY_NAME
    code = normalized_state_code(text)
    if code:
        return "U.S. State", code
    return LIVES_DEFAULT_TERRITORY_TYPE, LIVES_DEFAULT_TERRITORY_NAME


def apply_lives_defaults(
    slots: dict,
    linked: dict,
    territory_type: Optional[str] = None,
) -> None:
    """Apply final territory fields, defaulting missing geography to National."""
    linked_territory = slot_text(linked.get("territoryName"))
    if linked_territory and territory_type:
        resolved_type = territory_type
        territory_name = linked_territory
    else:
        resolved_type, territory_name = normalize_territory(
            linked_territory or territory_raw_from_slots(slots)
        )
    linked["territoryType"] = resolved_type
    linked["territoryName"] = territory_name
    linked.pop("territory", None)
    linked.pop("territoryInvalid", None)
    linked["source"] = LIVES_DEFAULT_SOURCE
    coverage = slots.get("coverage")
    if coverage and not linked.get("coverage"):
        linked["coverage"] = coverage


def extract_lives_slots(question: str) -> dict:
    slots = invoke_json_extract(LIVES_SLOT_EXTRACTION_PROMPT, question)
    coerce_lives_territory_slot(question, slots)
    return slots
