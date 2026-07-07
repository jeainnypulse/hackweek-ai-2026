from bedrock import invoke_claude
from db import get_db
import json

SLOT_EXTRACTION_PROMPT = """Extract structured slots from this payer question. Return ONLY valid JSON with no explanation or markdown.

Slots to extract:
- drug: brand name (Calquence, Brukinsa, Imbruvica, Imfinzi). Return null if not specified.
- indication: line of therapy + disease (e.g. 1L CLL, 2L CLL, Stage III NSCLC). Return null if not specified.
- book_of_business: "commercial" or "medicare". Default to "commercial" if not mentioned.
- payer: specific payer name if mentioned, otherwise null.

Drug mappings:
- cal, calquence, acalabrutinib -> Calquence
- brukinsa, zanubrutinib -> Brukinsa
- imbruvica, ibrutinib -> Imbruvica
- imfinzi, durvalumab -> Imfinzi

Indication mappings:
- first line CLL, 1L CLL -> 1L CLL
- second line CLL, 2L CLL -> 2L CLL
- stage 3 NSCLC, stage III NSCLC -> Stage III NSCLC

Return format:
{"drug": "Calquence", "indication": "1L CLL", "book_of_business": "commercial", "payer": null}

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
