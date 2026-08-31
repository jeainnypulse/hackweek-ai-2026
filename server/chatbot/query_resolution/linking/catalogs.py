from __future__ import annotations

import time
from typing import Optional

from chatbot.query_resolution.linking import config

_catalog_cache: dict[str, list[str]] = {}
_catalog_loaded_at: float = 0.0
_territory_catalog_cache: dict[str, list[str]] = {}
_territory_type_cache: dict[str, str] = {}
_territory_catalog_loaded_at: float = 0.0


def load_catalogs_from_db(force: bool = False) -> dict[str, list[str]]:
    """Load unscoped canonical access values in one Mongo aggregation."""
    global _catalog_cache, _catalog_loaded_at
    now = time.time()
    if (
        not force
        and _catalog_cache
        and (now - _catalog_loaded_at) < config.CATALOG_TTL_SEC
    ):
        return _catalog_cache

    from infrastructure.db import get_db

    pairs = [
        {"dimension": dimension, "value": f"${dimension}"}
        for dimension in config.LINK_DIMENSIONS
    ]
    rows = list(
        get_db().payerLatestAccess.aggregate(
            [
                {"$project": {"_id": 0, "pairs": pairs}},
                {"$unwind": "$pairs"},
                {
                    "$match": {
                        "pairs.value": {"$type": "string", "$nin": ["", None]}
                    }
                },
                {
                    "$group": {
                        "_id": "$pairs.dimension",
                        "values": {"$addToSet": "$pairs.value"},
                    }
                },
            ]
        )
    )
    catalogs: dict[str, list[str]] = {
        dimension: [] for dimension in config.LINK_DIMENSIONS
    }
    for row in rows:
        dimension = row.get("_id")
        if dimension not in catalogs:
            continue
        values = [
            value
            for value in row.get("values") or []
            if isinstance(value, str) and value.strip()
        ]
        catalogs[dimension] = sorted(set(values), key=str.casefold)
    _catalog_cache = catalogs
    _catalog_loaded_at = now
    return catalogs


def load_territory_catalog_from_db(force: bool = False) -> dict[str, list[str]]:
    global _territory_catalog_cache
    global _territory_type_cache
    global _territory_catalog_loaded_at

    now = time.time()
    if (
        not force
        and _territory_catalog_cache
        and (now - _territory_catalog_loaded_at) < config.CATALOG_TTL_SEC
    ):
        return _territory_catalog_cache

    from infrastructure.db import get_db

    rows = list(
        get_db().payerLatestLives.aggregate(
            [
                {
                    "$match": {
                        "territoryName": {
                            "$type": "string",
                            "$nin": ["", None],
                        },
                        "territoryType": {
                            "$type": "string",
                            "$nin": ["", None],
                        },
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "name": "$territoryName",
                            "type": "$territoryType",
                        }
                    }
                },
            ]
        )
    )
    territory_types = {
        row["_id"]["name"]: row["_id"]["type"]
        for row in rows
        if isinstance((row.get("_id") or {}).get("name"), str)
        and isinstance((row.get("_id") or {}).get("type"), str)
    }
    _territory_catalog_cache = {
        config.TERRITORY_LINK_DIMENSION: sorted(territory_types, key=str.casefold)
    }
    _territory_type_cache = territory_types
    _territory_catalog_loaded_at = now
    return _territory_catalog_cache


def territory_type_for_canonical(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    load_territory_catalog_from_db()
    return _territory_type_cache.get(value)


def set_catalogs(catalogs: dict[str, list[str]]) -> None:
    global _catalog_cache, _catalog_loaded_at
    _catalog_cache = {
        dimension: sorted(set(values), key=str.casefold)
        for dimension, values in catalogs.items()
    }
    _catalog_loaded_at = time.time()
