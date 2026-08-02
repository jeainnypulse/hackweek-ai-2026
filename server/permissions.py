"""Demo-user data scope for payerLatestAccess queries.

Loads the Mongo match pipeline from data/orca_payer_demo_permissions.json
and applies it to every read against the payer collection.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

PERMISSIONS_PATH = (
    Path(__file__).resolve().parent / "data" / "orca_payer_demo_permissions.json"
)


@lru_cache()
def permission_pipeline() -> tuple[dict[str, Any], ...]:
    """Ordered $match stages that define the allowed data slice."""
    with open(PERMISSIONS_PATH) as f:
        stages = json.load(f)
    if not isinstance(stages, list) or not stages:
        raise RuntimeError(f"Invalid permissions pipeline in {PERMISSIONS_PATH}")
    for stage in stages:
        if not isinstance(stage, dict) or "$match" not in stage:
            raise RuntimeError(
                f"Permissions stages must be $match objects: {PERMISSIONS_PATH}"
            )
    return tuple(stages)


@lru_cache()
def permission_filter() -> dict[str, Any]:
    """Flatten permission $match stages into a single find/distinct filter."""
    matches = [stage["$match"] for stage in permission_pipeline()]
    if len(matches) == 1:
        return matches[0]
    return {"$and": matches}


def scoped_query(query: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """AND an optional query onto the permission filter."""
    perm = permission_filter()
    if not query:
        return perm
    return {"$and": [perm, query]}


def with_permission_prefix(
    pipeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prepend permission $match stages (for aggregates where $match can lead)."""
    return list(permission_pipeline()) + list(pipeline)
