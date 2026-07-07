from bedrock import invoke_claude
import json
import re

SYSTEM_PROMPT = """You are a payer data analyst assistant for a pharmaceutical market access consulting firm.

Answer questions about payer access data using ONLY the records provided. Never make up data.

Rules:
- Only use information from the provided records.
- If records are empty or insufficient, say so clearly. Do not guess.

Format (strict):
- One to two sentences only. No markdown, no headers, no bullets, no bold, no horizontal rules.
- State the access level and score directly. Nothing else.
- End with source in parentheses.
- Example: Brukinsa has a Step Edit at UnitedHealth Group for CLL under Medicare with a score of 21. (Source: UnitedHealth Group, Brukinsa, CLL, Medicare)"""


def clean_answer(text: str) -> str:
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"^---+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def synthesize_answer(question: str, records: list[dict]) -> tuple[str, list[str]]:
    if not records:
        return (
            "No matching records were found for this question. "
            "Please check the regimen name, indication, and book of business and try again.",
            [],
        )

    payer_records = [r for r in records if "chunk_type" not in r]
    doc_chunks = [r for r in records if r.get("chunk_type") == "document"]

    context_parts = []
    if payer_records:
        context_parts.append("PAYER DATA RECORDS:")
        for r in payer_records:
            context_parts.append(json.dumps(r, default=str))
    if doc_chunks:
        context_parts.append("\nDOCUMENT EXCERPTS (supporting context):")
        for chunk in doc_chunks:
            label = f"[{chunk.get('source_file', 'unknown')} — slide/page {chunk.get('slide_or_page', '?')}]"
            context_parts.append(f"{label}\n{chunk.get('text', '')}")

    context = "\n".join(context_parts)
    prompt = f"""Answer in one to two plain-text sentences only. No markdown. State the access level and score directly.

Question: {question}

Records:
{context}"""

    answer = clean_answer(invoke_claude(prompt, system=SYSTEM_PROMPT, max_tokens=150))

    citations = []
    for r in payer_records:
        access = r.get("accessData") or {}
        parts = [
            p
            for p in [
                r.get("organization"),
                r.get("regimen"),
                r.get("indication"),
                r.get("line"),
                r.get("book"),
                access.get("access"),
            ]
            if p
        ]
        if parts:
            citations.append(" · ".join(str(p) for p in parts))

    return answer, list(dict.fromkeys(citations))
