"""Reusable permission-scoped distinct and selection helpers."""

from __future__ import annotations

from typing import Optional

from infrastructure.db import get_db
from chatbot.permissions import scoped_query

def _normalize_key(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def distinct_values(
    field: str,
    filter_query: dict,
    *,
    collection: str = "payerLatestAccess",
    scoped_fn=None,
) -> list[str]:
    db = get_db()
    scoped_fn = scoped_fn or scoped_query
    values = db[collection].distinct(field, scoped_fn(filter_query))
    cleaned = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned.append(text)
    return sorted(set(cleaned), key=lambda v: v.lower())


def filter_from_confirmed(confirmed: dict) -> dict:
    return {k: v for k, v in confirmed.items() if v is not None and v != ""}


def resolve_selection(value: Optional[str], options: list[str]) -> Optional[str]:
    if not value:
        return None
    if value in options:
        return value
    for option in options:
        if _normalize_key(option) == _normalize_key(value):
            return option
    return None
