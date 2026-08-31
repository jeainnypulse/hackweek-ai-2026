"""Classify a question as access, lives, joint (both for one payer), or out of scope."""

from __future__ import annotations

import json
from typing import Optional

from infrastructure.bedrock import invoke_claude

INTENTS = ("access", "lives", "joint", "out_of_scope")

OUT_OF_SCOPE_MESSAGE = (
    "This question is out of scope. I can answer simple lookup questions about "
    "payer access, payer lives, or both for a single payer, but not this one."
)

CLASSIFY_PROMPT = """Classify this user question for a payer market-access chatbot.

Pick exactly one intent. Missing details (coverage, line, subtype) are fine —
the app will clarify later. That does NOT make the question out of scope.

access — ONLY a simple lookup of fields on payerLatestAccess: access status /
quality of access (QoA) / score / whether PA or step edit is required (yes/no)
/ unrestricted vs restricted, for a DRUG or PRODUCT at a payer (optionally
indication, book, coverage). Lives counts are not the point.
A drug + payer mention is NOT enough to pick access if they want something else.
Access is NOT: the text of PA / step-edit / additional criteria; documentation
required for a PA; NCCN or other clinical guidelines; formulary tier (not
accessData); pathways; PBM.
Examples that MUST be access:
- "What is Tregzi access at Aetna for hematologic malignancies on Medicare?"
- "Is Brukinsa unrestricted at UHC on commercial?"
- "What's the QoA for Tregzi at Humana?"
- "Does Aetna require PA for Tregzi?"  (yes/no PA required — still access)

lives — the user wants enrollment / covered lives / lives percent for a PAYER.
No drug access restrictions. No "lives with PA / step edit / unrestricted".
A missing US state is fine (defaults to National). That does NOT make it out of scope.
Examples that MUST be lives:
- "How many commercial pharmacy lives does Aetna have?"
- "What percent of national Medicare medical lives does UnitedHealth have?"
- "How many commercial medical lives does Aetna cover in California?"

joint — TWO separate simple facts for ONE named payer in the same question:
  (1) access / QoA / score / PA-required yes-no / unrestricted for a DRUG, AND
  (2) that payer's enrollment / lives count or percent (not weighted by access).
Do NOT pick joint for access-only or lives-only questions.
Examples that MUST be joint:
- "What is Tregzi access at Aetna on Medicare, and how many Medicare pharmacy lives do they have?"
- "Is Brukinsa unrestricted at UHC on commercial, and what is UHC's commercial medical enrollment?"
- "What's the QoA for Tregzi at Humana, and how many Medicare pharmacy lives does Humana have?"

out_of_scope — if ANY of these apply, even when a drug and payer are named:
- access-weighted lives (lives that HAVE a restriction), e.g.
  "% of commercial lives with PA for Tregzi"
- lives attributed to a product without asking access as a separate fact, e.g.
  "How many lives does Aetna cover for Tregzi?"
- access and lives for TWO or more payers, or compare / rank payers
- rankings / comparisons across many payers / trends over time
- PA, step-edit, or additional CRITERIA text (not whether PA/SE is required)
- documentation required for prior authorization
- NCCN / clinical guidelines
- formulary tier / copay / benefit design
- pathways, PBM, weather, or any other domain
- lives by region that is not a single US state (Northeast, Southeast, Midwest,
  West Coast, East Coast)
- lives in a city (New York City, Los Angeles, Chicago) — cities are not a
  state-level lives record
- you truly cannot tell access vs lives vs joint

Return ONLY JSON:
{"intent": "access" | "lives" | "joint" | "out_of_scope", "reason": "short"}

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
        "both": "joint",
        "access_and_lives": "joint",
        "access+lives": "joint",
        "oos": "out_of_scope",
        "out-of-scope": "out_of_scope",
        "unsure": "out_of_scope",
    }
    intent = aliases.get(intent, intent)
    if intent not in INTENTS:
        raise ValueError(f"Invalid intent: {intent!r} raw={raw!r}")
    return intent


def classify_intent(question: str) -> str:
    """Return access | lives | joint | out_of_scope.

    Unsure / unparseable model output → out_of_scope.
    Bedrock/auth failures propagate so the API does not fake a refusal.
    """
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            raw = invoke_claude(CLASSIFY_PROMPT + question, max_tokens=160)
            intent = _parse_intent(raw)
            print(f"[intent] {intent} (attempt {attempt + 1}) raw={raw!r}")
            return intent
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            print(f"[intent] parse failed (attempt {attempt + 1}): {exc}")
    print(f"[intent] treating as out_of_scope after parse failures: {last_error}")
    return "out_of_scope"
