"""Slot extraction for payerLatestAccess lookups."""

from __future__ import annotations

from retrieval.extract_utils import invoke_json_extract

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

# Back-compat alias used by older scripts
SLOT_EXTRACTION_PROMPT = ACCESS_SLOT_EXTRACTION_PROMPT


def extract_access_slots(question: str) -> dict:
    return invoke_json_extract(ACCESS_SLOT_EXTRACTION_PROMPT, question)
