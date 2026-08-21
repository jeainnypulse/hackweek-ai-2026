"""Demo-user data scope for payerLatestAccess and payerLatestLives queries.

Loads the Mongo match pipeline from data/orca_payer_demo_permissions.json
and applies it to every read against the payer collections.
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


@lru_cache()
def _permission_slug_allowlist() -> tuple[str, ...]:
    """Slug $in list from the demo permission pipeline (shared by access and lives)."""
    for stage in permission_pipeline():
        match = stage.get("$match") or {}
        slug = match.get("slug")
        if isinstance(slug, dict) and isinstance(slug.get("$in"), list):
            return tuple(str(s) for s in slug["$in"])
    raise RuntimeError(
        f"Permissions pipeline has no slug $in list: {PERMISSIONS_PATH}"
    )


@lru_cache()
def lives_permission_filter() -> dict[str, Any]:
    """Slug allowlist + book exclusion only (lives docs have no regimen/indication)."""
    return {
        "book": {"$ne": "Federal/Other"},
        "slug": {"$in": list(_permission_slug_allowlist())},
    }


def scoped_lives_query(query: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """AND an optional query onto the lives permission filter."""
    perm = lives_permission_filter()
    if not query:
        return perm
    return {"$and": [perm, query]}


def with_permission_prefix(
    pipeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prepend permission $match stages (for aggregates where $match can lead)."""
    return list(permission_pipeline()) + list(pipeline)
