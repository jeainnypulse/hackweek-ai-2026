from __future__ import annotations

from typing import Any, Optional

from chatbot.query_resolution.linking import (
    catalog_match,
    catalogs as catalog_store,
    config,
    hypothesis,
)
from chatbot.query_resolution.linking.results import result


def resolve_cascade(
    dimension: str,
    mention: str,
    catalogs: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    """Run the production synonym, catalog, and verified-hypothesis cascade."""
    catalogs = catalogs or catalog_store.load_catalogs_from_db()

    if config.USE_SYNONYM_TABLE:
        resolved = catalog_match.resolve_synonym(dimension, mention)
        if resolved["status"] == "matched":
            canonical = resolved["value"]
            catalog_result = catalog_match.resolve_catalog(
                dimension, canonical, catalogs
            )
            if catalog_result["status"] == "matched":
                return result(
                    "matched",
                    value=catalog_result["value"],
                    method="cascade:synonym",
                )
            resolved["method"] = "cascade:synonym"
            return resolved

    resolved = catalog_match.resolve_catalog(dimension, mention, catalogs)
    if resolved["status"] == "matched":
        resolved["method"] = "cascade:catalog"
        return resolved

    if dimension == config.TERRITORY_LINK_DIMENSION:
        resolved = catalog_match.resolve_fuzzy_synonym(
            dimension, mention, catalogs
        )
        if resolved["status"] == "matched":
            resolved["method"] = "cascade:fuzzy_synonym"
            return resolved
        if resolved["status"] == "ambiguous" and resolved.get("candidates"):
            return result(
                "ambiguous",
                candidates=list(resolved["candidates"]),
                scores=resolved.get("scores"),
                method="cascade:clarify",
            )
        values = sorted(
            catalogs.get(dimension) or [],
            key=lambda value: str(value).casefold(),
        )
        if values:
            return result(
                "ambiguous", candidates=values, method="cascade:clarify"
            )
        return result("unknown", method="cascade")

    resolved = hypothesis.resolve_hypothesize_verify(
        dimension, mention, catalogs
    )
    if resolved["status"] == "matched":
        resolved["method"] = "cascade:" + str(
            resolved.get("method", "hypothesize_verify")
        )
        return resolved
    if resolved["status"] == "ambiguous" and resolved.get("candidates"):
        return result(
            "ambiguous",
            candidates=list(resolved["candidates"]),
            method="cascade:clarify",
        )
    values = sorted(
        catalogs.get(dimension) or [], key=lambda value: str(value).casefold()
    )
    if values:
        return result("ambiguous", candidates=values, method="cascade:clarify")
    return result("unknown", method="cascade")
