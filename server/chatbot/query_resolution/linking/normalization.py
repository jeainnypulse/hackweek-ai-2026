from __future__ import annotations

import re

from chatbot.query_resolution.linking.config import WEAK_TOKENS


def normalize_key(text: str) -> str:
    """Normalize for comparison: casefold, collapse space/punct."""
    text = text.casefold().strip()
    text = re.sub(r"[^\w\s]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_dimension_key(dimension: str, text: str) -> str:
    """Normalize without erasing clinically meaningful plus modifiers."""
    key = normalize_key(text)
    if dimension in ("population", "line") and "+" in text:
        key = f"{key} plus"
    return key


def distinctive_tokens(text: str) -> set[str]:
    return {
        token
        for token in normalize_key(text).split()
        if token not in WEAK_TOKENS and len(token) > 2
    }


def shares_distinctive_token(mention: str, candidate: str) -> bool:
    """Reject fuzzy pairs that only share generic tokens (e.g. Blue/Shield)."""
    mention_tokens = distinctive_tokens(mention)
    candidate_tokens = distinctive_tokens(candidate)
    if not mention_tokens:
        return True
    return bool(mention_tokens & candidate_tokens)


def fuzzy_score(a: str, b: str) -> float:
    try:
        from rapidfuzz import fuzz

        return float(fuzz.WRatio(a, b))
    except ImportError:
        from difflib import SequenceMatcher

        return SequenceMatcher(None, a, b).ratio() * 100.0
