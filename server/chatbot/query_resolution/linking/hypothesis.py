from __future__ import annotations

import json
import re
from typing import Any, Optional

from chatbot.query_resolution.linking import catalogs as catalog_store
from chatbot.query_resolution.linking.catalog_match import verify_in_catalog
from chatbot.query_resolution.linking.normalization import normalize_key
from chatbot.query_resolution.linking.results import result


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _is_long_phrase_to_acronym(mention: str, canonical: str) -> bool:
    words = normalize_key(mention).split()
    canonical = canonical.strip()
    return len(words) >= 3 and len(canonical) <= 4 and canonical.isalpha()


def _select_verified_canonical(
    mention: str,
    verified: list[str],
    confidence: str,
) -> Optional[str]:
    if not verified or confidence == "low":
        return None
    mention_key = normalize_key(mention)
    for value in verified:
        if normalize_key(value) == mention_key:
            return value
    for value in verified:
        if not _is_long_phrase_to_acronym(mention, value):
            return value
    return None


def _catalog_name_family(
    canonical: str,
    catalogs: dict[str, list[str]],
    dimension: str,
) -> list[str]:
    base = normalize_key(canonical)
    if not base:
        return []
    family: list[str] = []
    for value in catalogs.get(dimension) or []:
        raw = str(value).strip()
        if re.search(r"[&+]|\bplus\b|\bwith\b", raw, re.I):
            continue
        key = normalize_key(raw)
        if key == base or key.startswith(base + " ") or key.startswith(base + "("):
            family.append(raw)
    return family


def resolve_hypothesize_verify(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    """Hypothesize with Bedrock, then accept only exact catalog verification."""
    catalogs = catalogs or catalog_store.load_catalogs_from_db()
    if not (catalogs.get(dimension) or []):
        return result("unknown", method="hypothesize_verify")

    from infrastructure.bedrock import invoke_claude

    prompt = f"""You resolve a user mention to a canonical value in a US payer oncology
access database (Pulse). The database stores exact strings for dimension "{dimension}".

User mention: {mention!r}

Use industry knowledge (payer acronyms, Blues plans, brand↔generic drugs, indication
aliases). Propose the exact database strings you expect — do NOT rely on string
similarity to the mention.

Return ONLY JSON:
{{
  "guesses": ["most likely canonical", "..."],
  "confidence": "high" | "medium" | "low",
  "reason": "short"
}}

Rules:
- guesses: 0–3 strings, most likely first. Prefer names like
  "UnitedHealth Group", "BlueCross BlueShield Michigan",
  "Anthem (Elevance Health)", "Brukinsa", "CLL", "Commercial".
- If the mention is already a short catalog-style token (CVS, Aetna, Humana),
  put that exact token first in guesses.
- For Anthem / Elevance, prefer "Anthem (Elevance Health)" over bare "Anthem".
- For drugs, prefer brand name in DB (zanubrutinib → Brukinsa).
- SLL / CLL/SLL → CLL when that is the stored indication.
- Blue Shield of California is a distinct entity from BlueCross BlueShield plans;
  if you are not sure it is in-DB, return guesses=[] .
- For bare ambiguous mentions (e.g. just "BCBS") return guesses=[] confidence="low".
- Do not collapse a multi-word disease name into a short acronym guess unless the
  user already used that acronym (mantle cell lymphoma should NOT guess MCL).
"""
    try:
        parsed = _parse_llm_json(invoke_claude(prompt, max_tokens=220))
    except Exception as exc:
        print(f"[hypothesize_verify] LLM failed for {mention!r}: {exc}")
        return result("unknown", method="hypothesize_verify")

    guesses_raw = parsed.get("guesses") or []
    confidence = str(parsed.get("confidence") or "").lower()
    guesses = [str(guess).strip() for guess in guesses_raw if str(guess).strip()]
    verified: list[str] = []
    seen: set[str] = set()
    for guess in guesses:
        hit = verify_in_catalog(dimension, guess, catalogs)
        if hit and hit not in seen:
            verified.append(hit)
            seen.add(hit)

    pick = _select_verified_canonical(mention, verified, confidence)
    if pick:
        family = _catalog_name_family(pick, catalogs, dimension)
        if len(family) > 1:
            return result(
                "ambiguous",
                candidates=family,
                method="hypothesize_verify",
            )
        return result(
            "matched",
            value=pick,
            candidates=verified[:5] or None,
            method="hypothesize_verify",
        )
    if verified:
        return result(
            "ambiguous",
            candidates=verified[:5],
            method="hypothesize_verify",
        )
    return result("unknown", method="hypothesize_verify")
