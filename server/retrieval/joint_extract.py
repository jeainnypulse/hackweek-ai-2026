"""Slot extraction for joint single-payer questions (access + lives)."""

from __future__ import annotations

from retrieval.extract_utils import invoke_json_extract
from retrieval.lives_extract import coerce_lives_territory_slot

JOINT_SLOT_EXTRACTION_PROMPT = """Extract structured slots from this joint payer question.
The user wants TWO separate facts for ONE payer: drug access AND that payer's
enrollment / lives. Return ONLY valid JSON with no explanation or markdown.

Slots to extract:
- regimen: brand name (e.g. Tregzi, Brukinsa). Return null if not specified.
- indication: disease/indication only (e.g. "Hematologic malignancies", "CLL").
  Do NOT include line of therapy.
- line: line of therapy (e.g. "1L", "2L"). Return null if not specified.
- book: book of business (e.g. "Commercial", "Medicare", "Managed Medicaid").
  Return null if not specified. One book applies to both access and lives.
- organization: specific payer name (e.g. "Aetna", "UnitedHealth Group").
  Return null if not specified.
- coverage: "Medical" or "Pharmacy" if mentioned. Return null if not specified.
- population: patient population/subtype if mentioned. Return null if not specified.
- territoryName: 2-letter US state code if the question locates lives in a US
  state. Otherwise null.

Territory rules:
- Accept full state names ("California", "New Jersey") and 2-letter codes ("CA").
- Always return the 2-letter code (e.g. "CA" not "California").
- Only extract a state when it is a geographic constraint ("in California",
  "in FL"), not when the state is part of a payer name.
- "nationally", "across the US", "in the United States" → territoryName null.
- Do NOT extract cities or regions as a state — return null.

Return format:
{"regimen": "Tregzi", "indication": "Hematologic malignancies", "line": null, "book": "Medicare", "organization": "Aetna", "coverage": "Pharmacy", "population": null, "territoryName": null}

Question: """


def extract_joint_slots(question: str) -> dict:
    slots = invoke_json_extract(JOINT_SLOT_EXTRACTION_PROMPT, question)
    coerce_lives_territory_slot(question, slots)
    return slots
