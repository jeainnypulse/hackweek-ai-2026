from bedrock import invoke_claude
from db import get_db
import json

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


def extract_slots(question: str) -> dict:
    response = invoke_claude(SLOT_EXTRACTION_PROMPT + question)
    clean = response.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


def exact_match_retrieval(question: str) -> list[dict]:
    slots = extract_slots(question)
    print(f"[exact_match] slots: {slots}")
    query = {k: v for k, v in slots.items() if v is not None}
    if not query:
        return []
    db = get_db()
    records = list(db.payerLatestAccess.find(query, {"_id": 0}).limit(50))
    print(f"[exact_match] {len(records)} records found")
    return records
