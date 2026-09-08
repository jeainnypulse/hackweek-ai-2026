"""Slot extraction for payerLatestAccess lookups."""

from __future__ import annotations

from typing import Optional

from chatbot.query_resolution.linking.catalogs import load_catalogs_from_db
from chatbot.query_resolution.linking.normalization import fuzzy_score, normalize_key
from chatbot.slot_extract.utils import invoke_json_extract

ACCESS_SLOT_EXTRACTION_PROMPT = """Extract structured slots from this payer question. Return ONLY valid JSON with no explanation or markdown.

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


def _exact_catalog_value_from_question(
    question: str,
    dimension: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> Optional[str]:
    """Return one unambiguous, longest catalog value named in the question."""
    catalogs = catalogs or load_catalogs_from_db()
    question_key = normalize_key(question)
    if not question_key:
        return None
    padded_question = f" {question_key} "

    matches: list[tuple[str, str]] = []
    for raw_value in catalogs.get(dimension) or []:
        value = str(raw_value).strip()
        key = normalize_key(value)
        if key and f" {key} " in padded_question:
            matches.append((value, key))
    if not matches:
        return None

    maximal = [
        match
        for match in matches
        if not any(
            match[1] != other[1] and f" {match[1]} " in f" {other[1]} "
            for other in matches
        )
    ]
    by_key: dict[str, list[str]] = {}
    for value, key in maximal:
        by_key.setdefault(key, []).append(value)
    if len(by_key) != 1:
        return None

    values = next(iter(by_key.values()))
    return values[0] if len(values) == 1 else None


def _organization_without_book_suffix(
    organization: object,
    book: object,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> Optional[str]:
    """Split a trailing book only when the remainder is one exact payer."""
    catalogs = catalogs or load_catalogs_from_db()
    organization_key = normalize_key(str(organization or ""))
    if not organization_key:
        return None

    organizations_by_key: dict[str, list[str]] = {}
    for raw_value in catalogs.get("organization") or []:
        value = str(raw_value).strip()
        key = normalize_key(value)
        if key:
            organizations_by_key.setdefault(key, []).append(value)

    book_keys = {
        normalize_key(str(raw_value))
        for raw_value in [*(catalogs.get("book") or []), book]
        if normalize_key(str(raw_value or ""))
    }
    for book_key in sorted(
        book_keys,
        key=lambda key: (len(key.split()), len(key)),
        reverse=True,
    ):
        suffix = f" {book_key}"
        if not organization_key.endswith(suffix):
            continue
        payer_key = organization_key[: -len(suffix)].strip()
        payer_matches = organizations_by_key.get(payer_key) or []
        if len(payer_matches) == 1:
            return payer_matches[0]
    return None


def _question_supports_value(question: str, value: str) -> bool:
    """Return whether a same-length question phrase closely matches a value."""
    question_tokens = normalize_key(question).split()
    value_key = normalize_key(value)
    value_length = len(value_key.split())
    if not value_key or value_length > len(question_tokens):
        return False
    return any(
        fuzzy_score(
            " ".join(question_tokens[index : index + value_length]),
            value_key,
        )
        >= 85.0
        for index in range(len(question_tokens) - value_length + 1)
    )


def extract_access_slots(question: str) -> dict:
    slots = invoke_json_extract(ACCESS_SLOT_EXTRACTION_PROMPT, question)
    catalogs = load_catalogs_from_db()
    exact_regimen = _exact_catalog_value_from_question(
        question,
        "regimen",
        catalogs,
    )
    if exact_regimen:
        slots["regimen"] = exact_regimen
    exact_book = _exact_catalog_value_from_question(
        question,
        "book",
        catalogs,
    )
    extracted_book_key = normalize_key(str(slots.get("book") or ""))
    exact_book_key = normalize_key(exact_book or "")
    exact_is_shorter_part = (
        exact_book_key
        and exact_book_key != extracted_book_key
        and f" {exact_book_key} " in f" {extracted_book_key} "
    )
    preserve_supported_longer_book = exact_is_shorter_part and _question_supports_value(
        question,
        str(slots.get("book") or ""),
    )
    if exact_book and not preserve_supported_longer_book:
        slots["book"] = exact_book
    exact_organization = _organization_without_book_suffix(
        slots.get("organization"),
        slots.get("book"),
        catalogs,
    )
    if exact_organization:
        slots["organization"] = exact_organization
    return slots
