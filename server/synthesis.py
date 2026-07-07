from bedrock import invoke_claude
import json

SYSTEM_PROMPT = """You are a payer data analyst assistant for a pharmaceutical market access consulting firm.

Answer questions about payer Quality of Access (QoA) data using ONLY the records provided. Never make up data.

Rules:
- Only use information from the provided records.
- Cite every claim with its source (payer, drug, indication, report period).
- If records are empty or insufficient, say so clearly. Do not guess.
- If data is partial, say so explicitly.
- Be concise. Lead with the answer, then support with data.
- Format lives percentages clearly (e.g. 14% of commercial lives)."""

def synthesize_answer(question: str, records: list[dict]) -> tuple[str, list[str]]:
    if not records:
        return (
            "No matching records were found for this question. "
            "Please check the drug name, indication, and book of business and try again.",
            []
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
    prompt = f"""Answer this question using only the records below. Cite your sources.

Question: {question}

Records:
{context}"""

    answer = invoke_claude(prompt, system=SYSTEM_PROMPT)

    citations = []
    for r in payer_records:
        parts = [p for p in [
            r.get("payer"), r.get("drug"), r.get("indication"),
            r.get("book_of_business"), r.get("report_period"),
        ] if p]
        if parts:
            citations.append(" · ".join(str(p) for p in parts))

    return answer, list(dict.fromkeys(citations))
