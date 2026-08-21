"""Demo-user data scope for payerLatestAccess and payerLatestLives queries.

Access reads use data/orca_payer_demo_access_permissions.json.
Lives reads always AND data/orca_payer_demo_lives_permissions.json
(plus the access slug allowlist / book exclusion).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(__file__).resolve().parent / "data"
PERMISSIONS_PATH = DATA_DIR / "orca_payer_demo_access_permissions.json"
LIVES_PERMISSIONS_PATH = DATA_DIR / "orca_payer_demo_lives_permissions.json"


def _load_match_pipeline(path: Path) -> tuple[dict[str, Any], ...]:
    with open(path) as f:
        stages = json.load(f)
    if not isinstance(stages, list) or not stages:
        raise RuntimeError(f"Invalid permissions pipeline in {path}")
    for stage in stages:
        if not isinstance(stage, dict) or "$match" not in stage:
            raise RuntimeError(f"Permissions stages must be $match objects: {path}")
    return tuple(stages)


def _flatten_matches(stages: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    matches = [stage["$match"] for stage in stages]
    if len(matches) == 1:
        return matches[0]
    return {"$and": matches}


@lru_cache()
def permission_pipeline() -> tuple[dict[str, Any], ...]:
    """Ordered $match stages that define the allowed access data slice."""
    return _load_match_pipeline(PERMISSIONS_PATH)


@lru_cache()
def permission_filter() -> dict[str, Any]:
    """Flatten permission $match stages into a single find/distinct filter."""
    return _flatten_matches(permission_pipeline())


def scoped_query(query: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """AND an optional query onto the permission filter."""
    perm = permission_filter()
    if not query:
        return perm
    return {"$and": [perm, query]}


@lru_cache()
def _permission_slug_allowlist() -> tuple[str, ...]:
    """Slug $in list from the access permission pipeline (shared by lives)."""
    for stage in permission_pipeline():
        match = stage.get("$match") or {}
        slug = match.get("slug")
        if isinstance(slug, dict) and isinstance(slug.get("$in"), list):
            return tuple(str(s) for s in slug["$in"])
    raise RuntimeError(f"Permissions pipeline has no slug $in list: {PERMISSIONS_PATH}")


@lru_cache()
def lives_permission_pipeline() -> tuple[dict[str, Any], ...]:
    """Ordered $match stages applied to every payerLatestLives read."""
    return _load_match_pipeline(LIVES_PERMISSIONS_PATH)


@lru_cache()
def lives_permission_filter() -> dict[str, Any]:
    """Orca lives JSON + access slug allowlist and Federal/Other book exclusion."""
    return {
        "$and": [
            _flatten_matches(lives_permission_pipeline()),
            {
                "book": {"$ne": "Federal/Other"},
                "slug": {"$in": list(_permission_slug_allowlist())},
            },
        ]
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


def with_lives_permission_prefix(
    pipeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prepend lives permission $match stages (plus access slug/book scope)."""
    slug_book = {
        "$match": {
            "book": {"$ne": "Federal/Other"},
            "slug": {"$in": list(_permission_slug_allowlist())},
        }
    }
    return list(lives_permission_pipeline()) + [slug_book] + list(pipeline)
