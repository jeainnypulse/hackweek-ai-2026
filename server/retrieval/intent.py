"""Classify a question as a simple access lookup, simple lives lookup, or out of scope."""

from __future__ import annotations

import json
from typing import Optional

from bedrock import invoke_claude

INTENTS = ("access", "lives", "out_of_scope")

OUT_OF_SCOPE_MESSAGE = (
    "This question is out of scope. I can answer simple lookup questions about "
    "payer access or payer lives, but not this one."
)

CLASSIFY_PROMPT = """Classify this user question for a payer market-access chatbot.

Pick exactly one intent. Missing details (coverage, line, subtype) are fine —
the app will clarify later. That does NOT make the question out of scope.

access — the user wants quality of access / coverage / PA / restriction / score
for a DRUG or PRODUCT at a payer (optionally indication, book, coverage).
Lives counts are not the point.
Examples that MUST be access:
- "What is Tregzi access at Aetna for hematologic malignancies on Medicare?"
- "Is Brukinsa unrestricted at UHC on commercial?"
- "What's the QoA for Tregzi at Humana?"

lives — the user wants enrollment / covered lives / lives percent for a PAYER.
No drug access restrictions. No "lives with PA / step edit / unrestricted".
Examples that MUST be lives:
- "How many commercial pharmacy lives does Aetna have?"
- "What percent of national Medicare medical lives does UnitedHealth have?"

out_of_scope — ONLY if:
- the question needs BOTH access status AND lives in one answer
  (e.g. "% of commercial lives with PA for Tregzi")
- rankings / comparisons across many payers / trends over time
- a different domain (pathways, PBM, weather, etc.)
- you truly cannot tell access vs lives

Return ONLY JSON:
{"intent": "access" | "lives" | "out_of_scope", "reason": "short"}

Question: """


def _parse_intent(raw: str) -> str:
    text = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    intent = str(parsed.get("intent") or "").strip().lower().replace(" ", "_")
    aliases = {
        "qoa": "access",
        "coverage": "access",
        "payerlatestaccess": "access",
        "enrollment": "lives",
        "payerlatestlives": "lives",
        "oos": "out_of_scope",
        "out-of-scope": "out_of_scope",
        "unsure": "out_of_scope",
    }
    intent = aliases.get(intent, intent)
    if intent not in INTENTS:
        raise ValueError(f"Invalid intent: {intent!r} raw={raw!r}")
    return intent


def classify_intent(question: str) -> str:
    """Return access | lives | out_of_scope.

    Unsure / unparseable model output → out_of_scope.
    Bedrock/auth failures propagate so the API does not fake a refusal.
    """
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            raw = invoke_claude(CLASSIFY_PROMPT + question, max_tokens=120)
            intent = _parse_intent(raw)
            print(f"[intent] {intent} (attempt {attempt + 1}) raw={raw!r}")
            return intent
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            print(f"[intent] parse failed (attempt {attempt + 1}): {exc}")
    print(f"[intent] treating as out_of_scope after parse failures: {last_error}")
    return "out_of_scope"
