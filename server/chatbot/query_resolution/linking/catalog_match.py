from __future__ import annotations

from typing import Any, Optional

from chatbot.query_resolution.linking import catalogs as catalog_store
from chatbot.query_resolution.linking import config, synonyms
from chatbot.query_resolution.linking.normalization import (
    fuzzy_score,
    normalize_dimension_key,
    normalize_key,
    shares_distinctive_token,
)
from chatbot.query_resolution.linking.results import result


def resolve_none(dimension: str, mention: str) -> dict[str, Any]:
    return result("matched", value=mention, method="none")


def _doc_count(dimension: str, value: str) -> int:
    try:
        from infrastructure.db import get_db

        return int(get_db().payerLatestAccess.count_documents({dimension: value}))
    except Exception:
        return 0


def _related_canonicals(
    dimension: str,
    value: str,
    catalogs: dict[str, list[str]],
) -> list[str]:
    value_key = normalize_key(value)
    if not value_key:
        return []
    related: list[str] = []
    for candidate in catalogs.get(dimension) or []:
        if candidate == value:
            continue
        candidate_key = normalize_key(candidate)
        initials = "".join(
            word[0]
            for word in candidate_key.split()
            if word and word[0].isalpha()
        )
        if (
            initials == value_key
            or value_key in candidate_key.split()
            or candidate_key.startswith(value_key + " ")
            or candidate_key.startswith(value_key + "(")
        ):
            related.append(candidate)
    return related


def _prefer_dominant_canonical(
    dimension: str,
    value: str,
    catalogs: dict[str, list[str]],
) -> Optional[str]:
    related = _related_canonicals(dimension, value, catalogs)
    if not related:
        return None
    base_count = _doc_count(dimension, value)
    best = value
    best_count = base_count
    for candidate in related:
        count = _doc_count(dimension, candidate)
        if count > best_count:
            best, best_count = candidate, count
    if best == value:
        return None
    if best_count >= max(10, base_count * 5) or (
        base_count <= 5 and best_count >= 50
    ):
        return best
    return None


def resolve_catalog(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    catalogs = catalogs or catalog_store.load_catalogs_from_db()
    values = catalogs.get(dimension) or []
    key = normalize_dimension_key(dimension, mention)
    by_key = {
        normalize_dimension_key(dimension, value): value for value in values
    }
    if key in by_key:
        value = by_key[key]
        dominant = None
        if dimension in config.IDENTITY_LINK_DIMENSIONS:
            dominant = _prefer_dominant_canonical(dimension, value, catalogs)
        if dominant:
            return result(
                "matched", value=dominant, method="catalog:dominant"
            )
        return result("matched", value=value, method="catalog")
    return result("unknown", method="catalog")


def resolve_synonym(dimension: str, mention: str) -> dict[str, Any]:
    synonym_map = synonyms.load_synonyms().get(dimension) or {}
    key = normalize_dimension_key(dimension, mention)
    if key in synonym_map:
        return result("matched", value=synonym_map[key], method="synonym")
    return result("unknown", method="synonym")


def verify_in_catalog(
    dimension: str,
    guess: str,
    catalogs: dict[str, list[str]],
) -> Optional[str]:
    catalog_result = resolve_catalog(dimension, guess, catalogs)
    if catalog_result["status"] == "matched":
        return catalog_result["value"]
    return None


def resolve_fuzzy_synonym(
    dimension: str,
    mention: str,
    catalogs: dict[str, list[str]],
    *,
    accept_threshold: float = config.FUZZY_ACCEPT_THRESHOLD,
    ambiguous_gap: float = config.FUZZY_AMBIGUOUS_GAP,
) -> dict[str, Any]:
    mention_key = normalize_dimension_key(dimension, mention)
    synonym_map = synonyms.load_synonyms().get(dimension) or {}
    best_by_canonical: dict[str, tuple[float, str]] = {}
    for alias_key, canonical in synonym_map.items():
        verified = verify_in_catalog(dimension, canonical, catalogs)
        if not verified:
            continue
        score = fuzzy_score(mention_key, alias_key)
        previous = best_by_canonical.get(verified)
        if previous is None or score > previous[0]:
            best_by_canonical[verified] = (score, alias_key)

    ranked = sorted(
        best_by_canonical.items(), key=lambda item: item[1][0], reverse=True
    )
    if not ranked:
        return result("unknown", method="fuzzy_synonym")
    top = ranked[:3]
    candidates = [canonical for canonical, _ in top]
    scores = {
        canonical: round(score_alias[0], 2)
        for canonical, score_alias in top
    }
    best_canonical, (best_score, _) = top[0]
    second_score = top[1][1][0] if len(top) > 1 else 0.0
    if best_score < accept_threshold:
        return result(
            "unknown",
            candidates=candidates,
            scores=scores,
            method="fuzzy_synonym",
        )
    if len(top) > 1 and (best_score - second_score) < ambiguous_gap:
        return result(
            "ambiguous",
            candidates=candidates,
            scores=scores,
            method="fuzzy_synonym",
        )
    return result(
        "matched",
        value=best_canonical,
        candidates=candidates,
        scores=scores,
        method="fuzzy_synonym",
    )


def resolve_fuzzy(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
    *,
    accept_threshold: float = config.FUZZY_ACCEPT_THRESHOLD,
    ambiguous_gap: float = config.FUZZY_AMBIGUOUS_GAP,
) -> dict[str, Any]:
    catalogs = catalogs or catalog_store.load_catalogs_from_db()
    values = catalogs.get(dimension) or []
    if not values:
        return result("unknown", method="fuzzy")
    mention_key = normalize_dimension_key(dimension, mention)
    scored = [
        (
            value,
            fuzzy_score(
                mention_key, normalize_dimension_key(dimension, value)
            ),
        )
        for value in values
        if shares_distinctive_token(mention, value)
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    if not scored:
        return result("unknown", method="fuzzy")
    top = scored[:3]
    scores = {value: round(score, 2) for value, score in top}
    candidates = [value for value, _ in top]
    best_value, best_score = top[0]
    second_score = top[1][1] if len(top) > 1 else 0.0
    if best_score < accept_threshold:
        return result(
            "unknown", candidates=candidates, scores=scores, method="fuzzy"
        )
    if len(top) > 1 and (best_score - second_score) < ambiguous_gap:
        return result(
            "ambiguous", candidates=candidates, scores=scores, method="fuzzy"
        )
    return result(
        "matched",
        value=best_value,
        candidates=candidates,
        scores=scores,
        method="fuzzy",
    )
