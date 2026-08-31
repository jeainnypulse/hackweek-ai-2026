from __future__ import annotations

from typing import Any, Optional

from infrastructure.db import get_db
from chatbot.conversation.state import _is_lives
from chatbot.permissions import scoped_lives_query, scoped_query
from chatbot.query_resolution.cascade_clarify import distinct_values
from chatbot.slot_extract.lives_extract import (
    LIVES_DEFAULT_SOURCE,
    LIVES_DEFAULT_TERRITORY_NAME,
    LIVES_DEFAULT_TERRITORY_TYPE,
)


def _organization_to_slug(organization: str) -> Optional[str]:
    if not organization:
        return None
    slugs = [
        slug
        for slug in get_db().payerLatestAccess.distinct(
            "slug", {"organization": organization}
        )
        if isinstance(slug, str) and slug.strip()
    ]
    if not slugs:
        return None
    if len(slugs) > 1:
        print(
            f"[conversation] multiple slugs for {organization!r}: {slugs}"
        )
    return slugs[0]


def _orgs_for_slugs(slugs: list[str]) -> list[str]:
    if not slugs:
        return []
    orgs = [
        organization
        for organization in get_db().payerLatestAccess.distinct(
            "organization", {"slug": {"$in": slugs}}
        )
        if isinstance(organization, str) and organization.strip()
    ]
    return sorted(set(orgs), key=str.casefold)


def _lives_mongo_filter(
    slots: dict, drop_fields: Optional[set[str]] = None
) -> dict:
    drop = set(drop_fields or ())
    query: dict[str, Any] = {}
    if "slug" not in drop and "organization" not in drop:
        organization = slots.get("organization")
        if organization:
            slug = _organization_to_slug(str(organization))
            if slug:
                query["slug"] = slug
    for field in ("book", "coverage", "territoryType", "territoryName", "source"):
        if field in drop:
            continue
        value = slots.get(field)
        if value is not None and value != "":
            query[field] = value
    if "source" not in query and "source" not in drop:
        query["source"] = LIVES_DEFAULT_SOURCE
    if "territoryType" not in query and "territoryType" not in drop:
        query["territoryType"] = LIVES_DEFAULT_TERRITORY_TYPE
    if "territoryName" not in query and "territoryName" not in drop:
        query["territoryName"] = LIVES_DEFAULT_TERRITORY_NAME
    return query


def _distinct_for_session(session: dict, field: str, filt: dict) -> list[str]:
    if not _is_lives(session):
        return distinct_values(field, filt)
    if field == "organization":
        lives_filter = _lives_mongo_filter(
            filt, drop_fields={"organization", "slug"}
        )
        slugs = distinct_values(
            "slug",
            lives_filter,
            collection="payerLatestLives",
            scoped_fn=scoped_lives_query,
        )
        return _orgs_for_slugs(slugs)
    lives_filter = _lives_mongo_filter(
        filt, drop_fields={field, "organization"}
    )
    if field != "slug" and filt.get("organization"):
        slug = _organization_to_slug(str(filt["organization"]))
        if slug:
            lives_filter["slug"] = slug
    return distinct_values(
        field,
        lives_filter,
        collection="payerLatestLives",
        scoped_fn=scoped_lives_query,
    )


def _query_lives_records(slots: dict) -> list[dict]:
    query = _lives_mongo_filter(slots)
    if "slug" not in query:
        print("[conversation] lives query skipped: no slug")
        return []
    records = list(
        get_db().payerLatestLives.find(scoped_lives_query(query), {"_id": 0}).limit(50)
    )
    organization = slots.get("organization")
    if organization:
        for record in records:
            record.setdefault("organization", organization)
    print(f"[conversation] {len(records)} lives records for {query}")
    return records


def _query_access_records(slots: dict) -> list[dict]:
    query = {
        key: value
        for key, value in slots.items()
        if value is not None
        and value != ""
        and key
        not in (
            "territory",
            "territoryType",
            "territoryName",
            "source",
            "collection",
        )
    }
    if not query:
        return []
    records = list(
        get_db().payerLatestAccess.find(scoped_query(query), {"_id": 0}).limit(50)
    )
    print(f"[conversation] {len(records)} access records for {query}")
    return records


def _query_joint_records(slots: dict) -> list[dict]:
    access = [{**row, "collection": "access"} for row in _query_access_records(slots)]
    lives = [{**row, "collection": "lives"} for row in _query_lives_records(slots)]
    print(
        f"[conversation] joint records: "
        f"access={len(access)} lives={len(lives)}"
    )
    return access + lives


def _query_records(slots: dict, intent: str = "access") -> list[dict]:
    if intent == "lives":
        return _query_lives_records(slots)
    if intent == "joint":
        return _query_joint_records(slots)
    return _query_access_records(slots)
