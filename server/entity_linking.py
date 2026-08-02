"""
Entity linking / canonicalization for exact-match retrieval.

resolve(dimension, mention, method) ->
  {status: matched|ambiguous|unknown, value?, candidates?, scores?, method}

Methods:
  none                 — pass-through
  catalog              — casefold match against Mongo distinct catalog
  synonym              — curated alias table only
  fuzzy                — string similarity vs catalog
  cascade              — production: synonym -> catalog -> hypothesize+verify
                         (-> full-catalog dropdown clarify if still unresolved)
  hypothesize_verify   — LLM world-knowledge guesses, verified in catalog/Mongo
  phonetic / regex / embed / claude_* — bakeoff experiments
"""

from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(__file__).resolve().parent / "data"
SYNONYMS_PATH = DATA_DIR / "entity_synonyms.json"
SYNONYMS_COLLECTION = "entitySynonyms"

# Slot fields we attempt to link
LINK_DIMENSIONS = ("organization", "regimen", "indication", "book")

FUZZY_ACCEPT_THRESHOLD = 85.0
FUZZY_AMBIGUOUS_GAP = 5.0

# Tokens too common across Blues / payer names to count as distinctive overlap
_WEAK_TOKENS = frozenset(
    {
        "blue",
        "cross",
        "blueshield",
        "bluecross",
        "shield",
        "health",
        "healthcare",
        "group",
        "the",
        "of",
        "and",
        "plan",
        "plans",
    }
)


def _distinctive_tokens(text: str) -> set[str]:
    return {
        t for t in normalize_key(text).split() if t not in _WEAK_TOKENS and len(t) > 2
    }


def _shares_distinctive_token(mention: str, candidate: str) -> bool:
    """Reject fuzzy pairs that only share generic tokens (e.g. Blue/Shield)."""
    mt = _distinctive_tokens(mention)
    ct = _distinctive_tokens(candidate)
    if not mt:
        return True  # nothing distinctive to enforce
    return bool(mt & ct)


_catalog_cache: dict[str, list[str]] = {}
_catalog_loaded_at: float = 0.0
CATALOG_TTL_SEC = 300.0

_synonym_cache: dict[str, dict[str, str]] = {}
_synonym_loaded_at: float = 0.0
SYNONYM_TTL_SEC = 300.0
_synonym_indexes_ready = False

# dimension -> {canonical: embedding vector}
_embed_cache: dict[str, dict[str, list[float]]] = {}
EMBED_ACCEPT_THRESHOLD = 0.72
EMBED_AMBIGUOUS_GAP = 0.03


def normalize_key(text: str) -> str:
    """Normalize for comparison: casefold, collapse space/punct."""
    text = text.casefold().strip()
    text = re.sub(r"[^\w\s]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _fuzzy_score(a: str, b: str) -> float:
    try:
        from rapidfuzz import fuzz

        return float(fuzz.WRatio(a, b))
    except ImportError:
        from difflib import SequenceMatcher

        return SequenceMatcher(None, a, b).ratio() * 100.0


def _empty_synonym_map() -> dict[str, dict[str, str]]:
    return {d: {} for d in LINK_DIMENSIONS}


def load_synonyms_from_json(path: Path = SYNONYMS_PATH) -> dict[str, dict[str, str]]:
    """Load seed synonyms from JSON file."""
    out = _empty_synonym_map()
    if not path.exists():
        return out
    raw = json.loads(path.read_text())
    for dimension, pairs in raw.items():
        out.setdefault(dimension, {})
        for alias, canonical in pairs.items():
            out[dimension][normalize_key(alias)] = canonical
    return out


def ensure_synonym_indexes() -> None:
    global _synonym_indexes_ready
    if _synonym_indexes_ready:
        return
    from db import get_db

    col = get_db()[SYNONYMS_COLLECTION]
    col.create_index(
        [("dimension", 1), ("alias_key", 1)],
        unique=True,
        name="dimension_alias_key_unique",
    )
    _synonym_indexes_ready = True


def load_synonyms(force: bool = False) -> dict[str, dict[str, str]]:
    """
    Returns {dimension: {normalized_alias: canonical}}.

    Prefers Mongo entitySynonyms; falls back to JSON seed if collection empty
    or Mongo is unavailable.
    """
    global _synonym_cache, _synonym_loaded_at
    now = time.time()
    if (
        not force
        and _synonym_cache
        and (now - _synonym_loaded_at) < SYNONYM_TTL_SEC
    ):
        return _synonym_cache

    out = _empty_synonym_map()
    try:
        from db import get_db

        ensure_synonym_indexes()
        col = get_db()[SYNONYMS_COLLECTION]
        docs = list(col.find({}, {"dimension": 1, "alias_key": 1, "canonical": 1}))
        if docs:
            for doc in docs:
                dimension = doc.get("dimension")
                alias_key = doc.get("alias_key")
                canonical = doc.get("canonical")
                if (
                    dimension in out
                    and isinstance(alias_key, str)
                    and isinstance(canonical, str)
                    and canonical.strip()
                ):
                    out[dimension][alias_key] = canonical
            _synonym_cache = out
            _synonym_loaded_at = now
            return out
    except Exception as exc:
        print(f"[entity_linking] Mongo synonyms unavailable ({exc}); using JSON seed")

    out = load_synonyms_from_json()
    _synonym_cache = out
    _synonym_loaded_at = now
    return out


def reload_synonyms() -> None:
    global _synonym_cache, _synonym_loaded_at
    _synonym_cache = {}
    _synonym_loaded_at = 0.0
    load_synonyms(force=True)


def upsert_synonym(
    dimension: str,
    alias: str,
    canonical: str,
    *,
    source: str = "user_confirm",
) -> dict[str, str]:
    """
    Persist alias -> canonical in Mongo entitySynonyms and refresh cache.

    Only call after explicit user confirmation (not silent hyp accepts).
    """
    if dimension not in LINK_DIMENSIONS:
        raise ValueError(f"Unsupported synonym dimension: {dimension}")
    alias_key = normalize_key(alias)
    canonical = str(canonical).strip()
    if not alias_key or not canonical:
        raise ValueError("alias and canonical are required")

    from db import get_db

    ensure_synonym_indexes()
    now = datetime.utcnow()
    get_db()[SYNONYMS_COLLECTION].update_one(
        {"dimension": dimension, "alias_key": alias_key},
        {
            "$set": {
                "dimension": dimension,
                "alias_key": alias_key,
                "alias": str(alias).strip(),
                "canonical": canonical,
                "source": source,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    # Hot-update cache so the next resolve sees the write immediately
    global _synonym_cache, _synonym_loaded_at
    if not _synonym_cache:
        _synonym_cache = _empty_synonym_map()
    _synonym_cache.setdefault(dimension, {})[alias_key] = canonical
    _synonym_loaded_at = time.time()
    return {"dimension": dimension, "alias_key": alias_key, "canonical": canonical}


def load_catalogs_from_db(force: bool = False) -> dict[str, list[str]]:
    """Load distinct canonical values from payerLatestAccess (cached)."""
    global _catalog_cache, _catalog_loaded_at
    now = time.time()
    if not force and _catalog_cache and (now - _catalog_loaded_at) < CATALOG_TTL_SEC:
        return _catalog_cache

    from db import get_db
    from permissions import scoped_query

    db = get_db()
    col = db.payerLatestAccess
    catalogs: dict[str, list[str]] = {}
    for dimension in LINK_DIMENSIONS:
        values = [
            v
            for v in col.distinct(dimension, scoped_query())
            if isinstance(v, str) and v.strip()
        ]
        catalogs[dimension] = sorted(set(values), key=str.casefold)
    _catalog_cache = catalogs
    _catalog_loaded_at = now
    return catalogs


def set_catalogs(catalogs: dict[str, list[str]]) -> None:
    """Inject catalogs (for offline bakeoff without Mongo)."""
    global _catalog_cache, _catalog_loaded_at
    _catalog_cache = {
        d: sorted(set(vs), key=str.casefold) for d, vs in catalogs.items()
    }
    _catalog_loaded_at = time.time()


def _result(
    status: str,
    *,
    value: Optional[str] = None,
    candidates: Optional[list[str]] = None,
    scores: Optional[dict[str, float]] = None,
    method: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status, "method": method}
    if value is not None:
        out["value"] = value
    if candidates is not None:
        out["candidates"] = candidates
    if scores is not None:
        out["scores"] = scores
    return out


def resolve_none(dimension: str, mention: str) -> dict[str, Any]:
    return _result("matched", value=mention, method="none")


def resolve_catalog(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    catalogs = catalogs or load_catalogs_from_db()
    values = catalogs.get(dimension) or []
    key = normalize_key(mention)
    by_key = {normalize_key(v): v for v in values}
    if key in by_key:
        return _result("matched", value=by_key[key], method="catalog")
    return _result("unknown", method="catalog")


def resolve_synonym(dimension: str, mention: str) -> dict[str, Any]:
    synonyms = load_synonyms().get(dimension) or {}
    key = normalize_key(mention)
    if key in synonyms:
        return _result("matched", value=synonyms[key], method="synonym")
    return _result("unknown", method="synonym")


def resolve_fuzzy(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
    *,
    accept_threshold: float = FUZZY_ACCEPT_THRESHOLD,
    ambiguous_gap: float = FUZZY_AMBIGUOUS_GAP,
) -> dict[str, Any]:
    catalogs = catalogs or load_catalogs_from_db()
    values = catalogs.get(dimension) or []
    if not values:
        return _result("unknown", method="fuzzy")

    mention_key = normalize_key(mention)
    scored = []
    for v in values:
        if not _shares_distinctive_token(mention, v):
            continue
        scored.append((v, _fuzzy_score(mention_key, normalize_key(v))))
    scored.sort(key=lambda x: x[1], reverse=True)
    if not scored:
        return _result("unknown", method="fuzzy")
    top = scored[:3]
    scores = {v: round(s, 2) for v, s in top}
    candidates = [v for v, _ in top]

    best_val, best_score = top[0]
    second_score = top[1][1] if len(top) > 1 else 0.0

    if best_score < accept_threshold:
        return _result(
            "unknown",
            candidates=candidates,
            scores=scores,
            method="fuzzy",
        )
    if len(top) > 1 and (best_score - second_score) < ambiguous_gap:
        return _result(
            "ambiguous",
            candidates=candidates,
            scores=scores,
            method="fuzzy",
        )
    return _result(
        "matched",
        value=best_val,
        candidates=candidates,
        scores=scores,
        method="fuzzy",
    )


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _verify_in_catalog(
    dimension: str,
    guess: str,
    catalogs: dict[str, list[str]],
) -> Optional[str]:
    """Return canonical catalog string if guess casefolds to a catalog value."""
    cat = resolve_catalog(dimension, guess, catalogs)
    if cat["status"] == "matched":
        return cat["value"]
    return None


def resolve_hypothesize_verify(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    """
    Cursor-style linker: LLM hypothesizes canonical names from world knowledge,
    then we only accept values that exist in the Mongo-backed catalog.

    Guess → exact catalog verify only. No regex/pattern branch (see claude_regex).
    Does NOT shortlist via fuzzy similarity.
    """
    catalogs = catalogs or load_catalogs_from_db()
    if not (catalogs.get(dimension) or []):
        return _result("unknown", method="hypothesize_verify")

    from bedrock import invoke_claude

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
        return _result("unknown", method="hypothesize_verify")

    guesses_raw = parsed.get("guesses") or []
    confidence = str(parsed.get("confidence") or "").lower()
    guesses = [str(g).strip() for g in guesses_raw if str(g).strip()]

    verified: list[str] = []
    seen: set[str] = set()
    for guess in guesses:
        hit = _verify_in_catalog(dimension, guess, catalogs)
        if hit and hit not in seen:
            verified.append(hit)
            seen.add(hit)

    candidates = verified[:5] or None
    pick = _select_verified_canonical(mention, verified, confidence)
    if pick:
        return _result(
            "matched",
            value=pick,
            candidates=candidates,
            method="hypothesize_verify",
        )
    if verified:
        return _result(
            "ambiguous",
            candidates=candidates,
            method="hypothesize_verify",
        )

    return _result("unknown", method="hypothesize_verify")


def _is_long_phrase_to_acronym(mention: str, canonical: str) -> bool:
    """Refuse mantle cell lymphoma → MCL style leaps."""
    words = normalize_key(mention).split()
    canon = canonical.strip()
    return len(words) >= 3 and len(canon) <= 4 and canon.isalpha()


def _select_verified_canonical(
    mention: str,
    verified: list[str],
    confidence: str,
) -> Optional[str]:
    """Pick a single catalog-verified guess, or None if unsafe to auto-accept."""
    if not verified:
        return None
    if confidence == "low":
        return None

    mention_key = normalize_key(mention)
    for v in verified:
        if normalize_key(v) == mention_key:
            return v

    # Prefer first LLM-ordered guess that verified, unless it's an acronym leap
    for v in verified:
        if _is_long_phrase_to_acronym(mention, v):
            continue
        return v
    return None


def resolve_cascade(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    """
    Production cascade (Cursor path productized):
      synonym -> catalog -> hypothesize+Mongo-verify
      -> full-catalog dropdown clarify (never fuzzy chips)

    Synonym runs first so curated aliases win over literal catalog strings
    (e.g. Mongo contains both "Anthem" and "Anthem (Elevance Health)").

    LLM never auto-accepts without a catalog/Mongo hit. If still unresolved,
    return ambiguous with the full dimension catalog for UI dropdown clarify.
    """
    catalogs = catalogs or load_catalogs_from_db()

    r = resolve_synonym(dimension, mention)
    if r["status"] == "matched":
        # Prefer catalog casing if synonym target exists in catalog
        canon = r["value"]
        cat = resolve_catalog(dimension, canon, catalogs)
        if cat["status"] == "matched":
            return _result(
                "matched",
                value=cat["value"],
                method="cascade:synonym",
            )
        r["method"] = "cascade:synonym"
        return r

    r = resolve_catalog(dimension, mention, catalogs)
    if r["status"] == "matched":
        r["method"] = "cascade:catalog"
        return r

    r = resolve_hypothesize_verify(dimension, mention, catalogs)
    if r["status"] == "matched":
        r["method"] = "cascade:" + str(r.get("method", "hypothesize_verify"))
        return r

    # Unresolved (hyp unknown/ambiguous): full catalog for dropdown clarify
    values = sorted(catalogs.get(dimension) or [], key=lambda v: str(v).casefold())
    if values:
        return _result(
            "ambiguous",
            candidates=values,
            method="cascade:clarify",
        )
    return _result("unknown", method="cascade")


def _soundex(word: str) -> str:
    """American Soundex (letters only)."""
    word = re.sub(r"[^a-z]", "", word.casefold())
    if not word:
        return ""
    first = word[0].upper()
    mapping = {
        **dict.fromkeys(list("bfpv"), "1"),
        **dict.fromkeys(list("cgjkqsxz"), "2"),
        **dict.fromkeys(list("dt"), "3"),
        **dict.fromkeys(list("l"), "4"),
        **dict.fromkeys(list("mn"), "5"),
        **dict.fromkeys(list("r"), "6"),
    }
    digits = []
    prev = mapping.get(word[0], "0")
    for ch in word[1:]:
        code = mapping.get(ch)
        if code is None:
            prev = "0"
            continue
        if code != prev:
            digits.append(code)
            prev = code
        if len(digits) == 3:
            break
    return (first + "".join(digits) + "000")[:4]


def resolve_phonetic(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    """Match catalog values whose Soundex codes overlap mention tokens."""
    catalogs = catalogs or load_catalogs_from_db()
    values = catalogs.get(dimension) or []
    if not values:
        return _result("unknown", method="phonetic")

    # Short acronyms collide hard under Soundex (UHC == USA -> U200).
    mention_key = normalize_key(mention)
    tokens = mention_key.split()
    if len(tokens) == 1 and len(tokens[0]) <= 4:
        return _result("unknown", method="phonetic")

    mention_codes = {
        _soundex(t)
        for t in _distinctive_tokens(mention) or set(tokens)
        if _soundex(t) and len(t) > 3
    }
    if not mention_codes:
        return _result("unknown", method="phonetic")

    hits = []
    for v in values:
        cand_codes = {
            _soundex(t)
            for t in _distinctive_tokens(v) or set(normalize_key(v).split())
            if _soundex(t) and len(t) > 3
        }
        overlap = mention_codes & cand_codes
        if overlap == mention_codes and overlap:
            hits.append((v, len(overlap)))

    if not hits:
        return _result("unknown", method="phonetic")
    hits.sort(key=lambda x: (-x[1], x[0]))
    candidates = [v for v, _ in hits[:3]]
    if len(hits) > 1 and hits[0][1] == hits[1][1]:
        return _result(
            "ambiguous",
            candidates=candidates,
            method="phonetic",
        )
    return _result(
        "matched", value=hits[0][0], candidates=candidates, method="phonetic"
    )


def _templated_regex(mention: str) -> re.Pattern:
    """Non-AI regex from mention tokens (wave-app-style patterns)."""
    key = normalize_key(mention)
    tokens = [t for t in key.split() if t]
    alts: list[str] = []
    if tokens:
        alts.append(r"\s+".join(re.escape(t) for t in tokens))
        alts.append(r"[\s\-]*".join(re.escape(t) for t in tokens))
        alts.append("".join(re.escape(t) for t in tokens))
        # compact form for 2+ tokens (bcbs.?mi style) — never lone geography tokens
        if len(tokens) >= 2:
            alts.append(
                re.escape(tokens[0])
                + r".?"
                + r"[\s\-]*".join(re.escape(t) for t in tokens[1:])
            )
            # require at least two distinctive-ish tokens when present
            strong = [t for t in tokens if t not in _WEAK_TOKENS and len(t) >= 3]
            if len(strong) >= 2:
                alts.append(r"[\s\-\w]*".join(re.escape(t) for t in strong))
        elif len(tokens) == 1 and len(tokens[0]) >= 4:
            alts.append(re.escape(tokens[0]))
    if not alts:
        alts = [re.escape(key)]
    pattern = "|".join(f"(?:{a})" for a in dict.fromkeys(alts))
    return re.compile(pattern, re.IGNORECASE)


def resolve_regex(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    catalogs = catalogs or load_catalogs_from_db()
    values = catalogs.get(dimension) or []
    if not values:
        return _result("unknown", method="regex")

    try:
        pattern = _templated_regex(mention)
    except re.error:
        return _result("unknown", method="regex")

    mention_len = len(normalize_key(mention))
    hits = []
    for v in values:
        m = pattern.search(normalize_key(v)) or pattern.search(v)
        if not m:
            continue
        # Require distinctive token overlap unless the match covers most of the mention
        if not _shares_distinctive_token(mention, v) and len(m.group(0)) < max(
            4, mention_len - 1
        ):
            continue
        hits.append((v, len(m.group(0))))

    if not hits:
        return _result("unknown", method="regex")
    hits.sort(key=lambda x: (-x[1], x[0]))
    candidates = [v for v, _ in hits[:3]]
    # Prefer unique clear hit
    if len(hits) == 1 or (len(hits) > 1 and hits[0][1] > hits[1][1] + 2):
        return _result(
            "matched",
            value=hits[0][0],
            candidates=candidates,
            method="regex",
        )
    return _result("ambiguous", candidates=candidates, method="regex")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _ensure_catalog_embeddings(
    dimension: str, values: list[str]
) -> dict[str, list[float]]:
    from bedrock import get_embedding

    cache = _embed_cache.setdefault(dimension, {})
    missing = [v for v in values if v not in cache]
    for i, v in enumerate(missing):
        cache[v] = get_embedding(v)
        if (i + 1) % 25 == 0:
            print(f"[embed] {dimension}: embedded {i + 1}/{len(missing)}")
    return cache


def resolve_embed(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    catalogs = catalogs or load_catalogs_from_db()
    values = catalogs.get(dimension) or []
    if not values:
        return _result("unknown", method="embed")

    from bedrock import get_embedding

    vectors = _ensure_catalog_embeddings(dimension, values)
    q = get_embedding(mention)
    scored = [(v, _cosine(q, vectors[v])) for v in values if v in vectors]
    scored.sort(key=lambda x: x[1], reverse=True)
    if not scored:
        return _result("unknown", method="embed")
    top = scored[:3]
    scores = {v: round(s, 4) for v, s in top}
    candidates = [v for v, _ in top]
    best_val, best_score = top[0]
    second = top[1][1] if len(top) > 1 else 0.0

    if best_score < EMBED_ACCEPT_THRESHOLD:
        return _result("unknown", candidates=candidates, scores=scores, method="embed")
    if len(top) > 1 and (best_score - second) < EMBED_AMBIGUOUS_GAP:
        return _result(
            "ambiguous", candidates=candidates, scores=scores, method="embed"
        )
    if not _shares_distinctive_token(mention, best_val):
        # abbreviations like UHC often nearest-wrong; require token guard
        return _result(
            "ambiguous", candidates=candidates, scores=scores, method="embed"
        )
    return _result(
        "matched",
        value=best_val,
        candidates=candidates,
        scores=scores,
        method="embed",
    )


def resolve_claude_regex(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    catalogs = catalogs or load_catalogs_from_db()
    values = catalogs.get(dimension) or []
    if not values:
        return _result("unknown", method="claude_regex")

    from bedrock import invoke_claude

    prompt = f"""Write a single Python/JavaScript-style regular expression (no slashes, no flags)
that would match the canonical database value for this user mention in a list of {dimension} names.

Mention: {mention!r}

Return ONLY the regex pattern string, nothing else.
Prefer case-insensitive token/alternation patterns (e.g. michigan|bcbs.?mi).
Do not use lookbehind. Keep it under 120 characters."""
    try:
        raw = invoke_claude(prompt, max_tokens=80).strip()
        raw = raw.strip("`").strip()
        if raw.lower().startswith("regex"):
            raw = raw.split(":", 1)[-1].strip()
        # strip /pattern/ flags if model returns them
        if raw.startswith("/") and raw.rfind("/") > 0:
            raw = raw[1 : raw.rfind("/")]
        pattern = re.compile(raw, re.IGNORECASE)
    except Exception as exc:
        print(f"[claude_regex] failed for {mention!r}: {exc}")
        return _result("unknown", method="claude_regex")

    hits = []
    for v in values:
        text = normalize_key(v)
        m = pattern.search(text) or pattern.search(v)
        if m:
            hits.append((v, len(m.group(0))))
    if not hits:
        return _result("unknown", method="claude_regex")
    hits.sort(key=lambda x: (-x[1], x[0]))
    candidates = [v for v, _ in hits[:3]]
    if len(hits) == 1 or (len(hits) > 1 and hits[0][1] > hits[1][1]):
        if not _shares_distinctive_token(mention, hits[0][0]):
            return _result("ambiguous", candidates=candidates, method="claude_regex")
        return _result(
            "matched",
            value=hits[0][0],
            candidates=candidates,
            method="claude_regex",
        )
    return _result("ambiguous", candidates=candidates, method="claude_regex")


def resolve_claude_pick(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    """Fuzzy shortlist (top 5, no accept threshold) then Claude structured pick."""
    catalogs = catalogs or load_catalogs_from_db()
    values = catalogs.get(dimension) or []
    if not values:
        return _result("unknown", method="claude_pick")

    mention_key = normalize_key(mention)
    scored = [(v, _fuzzy_score(mention_key, normalize_key(v))) for v in values]
    scored.sort(key=lambda x: x[1], reverse=True)
    shortlist = [v for v, _ in scored[:5]]
    if not shortlist:
        return _result("unknown", method="claude_pick")

    from bedrock import invoke_claude

    options = "\n".join(f"- {c}" for c in shortlist)
    prompt = f"""Given the user mention for dimension '{dimension}', choose the exact canonical match
from the candidate list, or null if none is correct / mention is out of set / ambiguous.

Mention: {mention!r}

Candidates:
{options}

Return ONLY JSON: {{"match": "<exact candidate string or null>", "confidence": "high"|"medium"|"low"}}
If unsure or multiple could fit, set match to null and confidence to low."""
    try:
        raw = invoke_claude(prompt, max_tokens=120).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
    except Exception as exc:
        print(f"[claude_pick] failed for {mention!r}: {exc}")
        return _result("ambiguous", candidates=shortlist, method="claude_pick")

    match = parsed.get("match")
    confidence = (parsed.get("confidence") or "").lower()
    if not match or confidence == "low":
        return _result(
            "ambiguous" if shortlist else "unknown",
            candidates=shortlist,
            method="claude_pick",
        )
    match = str(match).strip()
    if match not in shortlist:
        for c in shortlist:
            if normalize_key(c) == normalize_key(match):
                match = c
                break
        else:
            return _result("unknown", candidates=shortlist, method="claude_pick")
    return _result("matched", value=match, candidates=shortlist, method="claude_pick")


METHODS = {
    "none": resolve_none,
    "catalog": resolve_catalog,
    "synonym": resolve_synonym,
    "fuzzy": resolve_fuzzy,
    "cascade": resolve_cascade,
    "hypothesize_verify": resolve_hypothesize_verify,
    "phonetic": resolve_phonetic,
    "regex": resolve_regex,
    "embed": resolve_embed,
    "claude_regex": resolve_claude_regex,
    "claude_pick": resolve_claude_pick,
}

# Methods that need a catalog argument
_CATALOG_METHODS = frozenset(
    {
        "catalog",
        "fuzzy",
        "cascade",
        "hypothesize_verify",
        "phonetic",
        "regex",
        "embed",
        "claude_regex",
        "claude_pick",
    }
)


def resolve(
    dimension: str,
    mention: Optional[str],
    method: str = "cascade",
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    if mention is None or not str(mention).strip():
        return _result("unknown", method=method)
    mention = str(mention).strip()
    if method not in METHODS:
        raise ValueError(f"Unknown linker method: {method}")
    fn = METHODS[method]
    if method in ("none", "synonym"):
        return fn(dimension, mention)
    return fn(dimension, mention, catalogs)


def link_slots(
    slots: dict,
    method: str = "cascade",
    catalogs: Optional[dict[str, list[str]]] = None,
    dimensions: tuple[str, ...] = LINK_DIMENSIONS,
) -> tuple[dict, dict[str, dict]]:
    """
    Canonicalize linkable slots. Returns (linked_slots, per_field_resolve_info).
    Ambiguous/unknown fields keep the original mention (exact find may miss).
    """
    if method == "none":
        return dict(slots), {}

    if catalogs is None and method in _CATALOG_METHODS:
        catalogs = load_catalogs_from_db()

    linked = dict(slots)
    info: dict[str, dict] = {}
    for dimension in dimensions:
        raw = slots.get(dimension)
        if raw is None or raw == "":
            continue
        result = resolve(dimension, raw, method=method, catalogs=catalogs)
        info[dimension] = {"raw": raw, **result}
        if result["status"] == "matched" and result.get("value"):
            linked[dimension] = result["value"]
    return linked, info
