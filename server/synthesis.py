from bedrock import invoke_claude
import json
import re

ACCESS_SYSTEM_PROMPT = """You are a payer data analyst assistant for a pharmaceutical market access consulting firm.

Answer questions about payer access data using ONLY the records provided. Never make up data.

Rules:
- Only use information from the provided records.
- If records are empty or insufficient, say so clearly. Do not guess.

Format (strict):
- One to two sentences only. No markdown, no headers, no bullets, no bold, no horizontal rules.
- State the access level and score directly. Nothing else.
- End with source in parentheses.
- Example: Brukinsa has a Step Edit at UnitedHealth Group for CLL under Medicare with a score of 21. (Source: UnitedHealth Group, Brukinsa, CLL, Medicare)"""

LIVES_SYSTEM_PROMPT = """You are a payer data analyst assistant for a pharmaceutical market access consulting firm.

Answer questions about payer enrollment / covered lives using ONLY the records provided. Never make up data.

Rules:
- Only use information from the provided records.
- If records are empty or insufficient, say so clearly. Do not guess.
- lives is a count. livesPercent is a 0-1 fraction of national or state enrollment for that book and coverage; report it as a percentage.
- Do not mention quality of access, access level, or scores.

Format (strict):
- One to two sentences only. No markdown, no headers, no bullets, no bold, no horizontal rules.
- State the lives count (and percent if present) with payer, book, coverage, and geography.
- End with source in parentheses (payer, book, coverage, geography, data source, as-of date).
- Example: Aetna has 5,685,473 commercial pharmacy lives nationally (3.7% of commercial pharmacy lives). (Source: Aetna, Commercial, Pharmacy, United States, MMIT, 2025-05-01)"""

# Back-compat alias
SYSTEM_PROMPT = ACCESS_SYSTEM_PROMPT


def clean_answer(text: str) -> str:
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"^---+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_lives_record(record: dict) -> bool:
    return "lives" in record and not (record.get("accessData"))


def synthesize_answer(
    question: str,
    records: list[dict],
    intent: str = "access",
) -> tuple[str, list[str]]:
    lives = intent == "lives" or (bool(records) and all(_is_lives_record(r) for r in records))
    if not records:
        if lives or intent == "lives":
            return (
                "No matching lives records were found for this question. "
                "Please check the payer, book of business, and coverage "
                "(Medical vs Pharmacy) and try again.",
                [],
            )
        return (
            "No matching records were found for this question. "
            "Please check the regimen name, indication, and book of business and try again.",
            [],
        )

    context_parts = ["PAYER DATA RECORDS:"]
    for r in records:
        context_parts.append(json.dumps(r, default=str))

    context = "\n".join(context_parts)
    if lives:
        system = LIVES_SYSTEM_PROMPT
        prompt = f"""Answer in one to two plain-text sentences only. No markdown. State the lives count and percent directly.

Question: {question}

Records:
{context}"""
    else:
        system = ACCESS_SYSTEM_PROMPT
        prompt = f"""Answer in one to two plain-text sentences only. No markdown. State the access level and score directly.

Question: {question}

Records:
{context}"""

    answer = clean_answer(invoke_claude(prompt, system=system, max_tokens=150))

    citations = []
    for r in records:
        if lives:
            lives_count = r.get("lives")
            lives_label = None
            if lives_count is not None:
                try:
                    lives_label = f"{int(lives_count):,} lives"
                except (TypeError, ValueError):
                    lives_label = f"{lives_count} lives"
            parts = [
                p
                for p in [
                    r.get("organization"),
                    r.get("book"),
                    r.get("coverage"),
                    r.get("territoryName"),
                    lives_label,
                ]
                if p
            ]
        else:
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
