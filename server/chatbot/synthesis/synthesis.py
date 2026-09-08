from infrastructure.bedrock import invoke_claude
import json
import re
from typing import Optional

ACCESS_SYSTEM_PROMPT = """You are a payer data analyst assistant for a pharmaceutical market access consulting firm.

Answer questions about payer access data using ONLY the records provided. Never make up data.

Rules:
- Only use information from the provided records.
- If records are empty or insufficient, say so clearly. Do not guess.
- Copy the value from accessData.access verbatim, preserving its exact wording and capitalization.
- Never interpret, paraphrase, expand, or explain the access value.

Format (strict):
- One to two sentences only. No markdown, no headers, no bullets, no bold, no horizontal rules.
- State the access level directly. Do not mention the score or any numeric access score.
- End with source in parentheses.
- Example: Brukinsa has a Step Edit at UnitedHealth Group for CLL under Medicare. (Source: UnitedHealth Group, Brukinsa, CLL, Medicare)"""

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

JOINT_SYSTEM_PROMPT = """You are a payer data analyst assistant for a pharmaceutical market access consulting firm.

Answer a joint question with TWO separate facts for one payer: access status AND lives count. Use ONLY the records provided. Never make up data.

Rules:
- Records tagged "collection": "access" are quality-of-access. Records tagged "collection": "lives" are enrollment.
- Do not weight lives by access (do not say "lives with PA" unless both facts are stated separately).
- Copy the value from accessData.access verbatim, preserving its exact wording and capitalization.
- Never interpret, paraphrase, expand, or explain the access value.
- lives is a count. livesPercent is a 0-1 fraction; report it as a percentage.
- If one side has no records, say so and still report the other side. Do not guess.

Format (strict):
- Two sentences only. No markdown, no headers, no bullets, no bold, no horizontal rules.
- Sentence 1: access level for the drug at the payer. Do not mention the score or any numeric access score.
- Sentence 2: lives count (and percent if present) with payer, book, coverage, and geography.
- End each sentence with its source in parentheses.
- Example: Tregzi is Not Reviewed at Aetna for hematologic malignancies on Medicare. (Source: Aetna, Tregzi, Hematologic malignancies, Medicare) Aetna has 5,685,473 Medicare pharmacy lives nationally. (Source: Aetna, Medicare, Pharmacy, United States, DRG)"""

def clean_answer(text: str) -> str:
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"^---+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _without_score_fields(value):
    if isinstance(value, dict):
        return {
            key: _without_score_fields(child)
            for key, child in value.items()
            if not str(key).casefold().endswith("score")
        }
    if isinstance(value, list):
        return [_without_score_fields(child) for child in value]
    return value


def _is_lives_record(record: dict) -> bool:
    if record.get("collection") == "lives":
        return True
    if record.get("collection") == "access":
        return False
    return "lives" in record and not (record.get("accessData"))


def _citation_for_record(record: dict, *, lives: bool) -> Optional[str]:
    if lives:
        lives_count = record.get("lives")
        lives_label = None
        if lives_count is not None:
            try:
                lives_label = f"{int(lives_count):,} lives"
            except (TypeError, ValueError):
                lives_label = f"{lives_count} lives"
        parts = [
            p
            for p in [
                record.get("organization"),
                record.get("book"),
                record.get("coverage"),
                record.get("territoryName"),
                lives_label,
            ]
            if p
        ]
    else:
        access = record.get("accessData") or {}
        parts = [
            p
            for p in [
                record.get("organization"),
                record.get("regimen"),
                record.get("indication"),
                record.get("line"),
                record.get("book"),
                access.get("access"),
            ]
            if p
        ]
    if not parts:
        return None
    return " · ".join(str(p) for p in parts)


def synthesize_answer(
    question: str,
    records: list[dict],
    intent: str = "access",
    confirmed_slots: Optional[dict] = None,
) -> tuple[str, list[str]]:
    joint = intent == "joint"
    lives = (not joint) and (
        intent == "lives"
        or (bool(records) and all(_is_lives_record(r) for r in records))
    )
    if not records:
        if joint:
            return (
                "No matching access or lives records were found for this question. "
                "Please check the payer, product, indication, book of business, "
                "and coverage (Medical vs Pharmacy) and try again.",
                [],
            )
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
        synthesis_record = r if _is_lives_record(r) else _without_score_fields(r)
        context_parts.append(json.dumps(synthesis_record, default=str))

    context = "\n".join(context_parts)
    canonical_context = json.dumps(confirmed_slots or {}, default=str)
    request_context = f"""Original question: {question}

Confirmed canonical request slots:
{canonical_context}

Use the confirmed canonical slot values in the answer instead of aliases,
misspellings, or unresolved names from the original question."""
    if joint:
        system = JOINT_SYSTEM_PROMPT
        prompt = f"""Answer in two plain-text sentences only. No markdown. Access fact first, then lives fact. Copy accessData.access verbatim without interpreting it. Do not include the access score.

{request_context}

Records:
{context}"""
        max_tokens = 220
    elif lives:
        system = LIVES_SYSTEM_PROMPT
        prompt = f"""Answer in one to two plain-text sentences only. No markdown. State the lives count and percent directly.

{request_context}

Records:
{context}"""
        max_tokens = 150
    else:
        system = ACCESS_SYSTEM_PROMPT
        prompt = f"""Answer in one to two plain-text sentences only. No markdown. State accessData.access verbatim without interpreting it or including its score.

{request_context}

Records:
{context}"""
        max_tokens = 150

    answer = clean_answer(invoke_claude(prompt, system=system, max_tokens=max_tokens))

    citations = []
    for r in records:
        use_lives = _is_lives_record(r) if joint else lives
        label = _citation_for_record(r, lives=use_lives)
        if label:
            citations.append(label)

    return answer, list(dict.fromkeys(citations))
