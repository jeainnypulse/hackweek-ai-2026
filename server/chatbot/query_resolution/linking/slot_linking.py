from __future__ import annotations

from typing import Optional

from chatbot.query_resolution.linking import catalogs as catalog_store
from chatbot.query_resolution.linking import config, registry


def _permissioned_distinct_for_slot(
    dimension: str,
    linked: dict,
    *,
    dimensions: tuple[str, ...] = config.IDENTITY_LINK_DIMENSIONS,
) -> list[str]:
    filt = {
        key: linked[key]
        for key in dimensions
        if key != dimension and linked.get(key)
    }
    if not filt:
        return []
    try:
        from chatbot.query_resolution.cascade_clarify import distinct_values

        return distinct_values(dimension, filt)
    except Exception as exc:
        print(f"[entity_linking] permissioned distinct({dimension}) failed: {exc}")
        return []


def _fill_missing_slots_from_permissioned_distinct(
    linked: dict,
    info: dict[str, dict],
    dimensions: tuple[str, ...] = config.IDENTITY_LINK_DIMENSIONS,
) -> None:
    progress = True
    while progress:
        progress = False
        for dimension in dimensions:
            if linked.get(dimension) or dimension in info:
                continue
            available = _permissioned_distinct_for_slot(
                dimension, linked, dimensions=dimensions
            )
            if len(available) > 1:
                info[dimension] = {
                    "raw": None,
                    "status": "ambiguous",
                    "candidates": available,
                    "method": "cascade:clarify",
                }
            elif len(available) == 1:
                linked[dimension] = available[0]
                info[dimension] = {
                    "raw": None,
                    "status": "matched",
                    "value": available[0],
                    "method": "cascade:auto_sole",
                }
                progress = True


def link_slots(
    slots: dict,
    method: str = "cascade",
    catalogs: Optional[dict[str, list[str]]] = None,
    dimensions: tuple[str, ...] = config.LINK_DIMENSIONS,
    fill_missing: bool = True,
    fill_missing_dimensions: Optional[tuple[str, ...]] = None,
) -> tuple[dict, dict[str, dict]]:
    """Canonicalize supplied slots and infer permission-scoped identity slots."""
    if method == "none":
        return dict(slots), {}
    if catalogs is None and method in registry.CATALOG_METHODS:
        catalogs = catalog_store.load_catalogs_from_db()

    linked = dict(slots)
    info: dict[str, dict] = {}
    for dimension in dimensions:
        raw = slots.get(dimension)
        if raw is None or raw == "":
            continue
        resolved = registry.resolve(
            dimension, raw, method=method, catalogs=catalogs
        )
        info[dimension] = {"raw": raw, **resolved}
        if resolved["status"] == "matched" and resolved.get("value"):
            linked[dimension] = resolved["value"]

    if fill_missing:
        if fill_missing_dimensions is None:
            fill_missing_dimensions = tuple(
                dimension
                for dimension in dimensions
                if dimension in config.IDENTITY_LINK_DIMENSIONS
            )
        _fill_missing_slots_from_permissioned_distinct(
            linked, info, fill_missing_dimensions
        )
    return linked, info
