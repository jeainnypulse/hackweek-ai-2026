from __future__ import annotations

from typing import Any, Optional

from chatbot.query_resolution.linking.cascade import resolve_cascade
from chatbot.query_resolution.linking.catalog_match import (
    resolve_catalog,
    resolve_fuzzy,
    resolve_fuzzy_synonym,
    resolve_none,
    resolve_synonym,
)
from chatbot.query_resolution.linking.hypothesis import (
    resolve_hypothesize_verify,
)
from chatbot.query_resolution.linking.results import result

METHODS = {
    "none": resolve_none,
    "catalog": resolve_catalog,
    "synonym": resolve_synonym,
    "fuzzy": resolve_fuzzy,
    "fuzzy_synonym": resolve_fuzzy_synonym,
    "cascade": resolve_cascade,
    "hypothesize_verify": resolve_hypothesize_verify,
}

CATALOG_METHODS = frozenset(
    {
        "catalog",
        "fuzzy",
        "fuzzy_synonym",
        "cascade",
        "hypothesize_verify",
    }
)


def resolve(
    dimension: str,
    mention: Optional[str],
    method: str = "cascade",
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    if mention is None or not str(mention).strip():
        return result("unknown", method=method)
    mention = str(mention).strip()
    if method not in METHODS:
        raise ValueError(f"Unknown linker method: {method}")
    resolver = METHODS[method]
    if method in ("none", "synonym"):
        return resolver(dimension, mention)
    return resolver(dimension, mention, catalogs)
