from __future__ import annotations

from typing import Optional

from chatbot.conversation.config import LIVES_CORE_LINK_DIMENSIONS
from chatbot.conversation.queries import _distinct_for_session
from chatbot.conversation.state import _is_lives
from chatbot.query_resolution.cascade_clarify import IDENTITY_SLOTS
from chatbot.query_resolution.entity_linking import LINK_DIMENSIONS


def _resolved_identity_filter(
    session: dict, exclude: Optional[str] = None
) -> dict:
    """Identity values safe to use when scoping another unresolved field."""
    identity_slots = (
        LIVES_CORE_LINK_DIMENSIONS if _is_lives(session) else IDENTITY_SLOTS
    )
    filt = {}
    for dimension in identity_slots:
        if dimension == exclude:
            continue
        value = session["linked"].get(dimension)
        if not value:
            continue
        info = session["link_info"].get(dimension) or {}
        if info and info.get("status") not in ("matched", "skipped"):
            continue
        filt[dimension] = value
    return filt


def _refresh_missing_access_identity(session: dict) -> None:
    """Recompute missing identity fields after another identity value changes."""
    if _is_lives(session):
        return

    linked = session["linked"]
    progress = True
    while progress:
        progress = False
        for dimension in IDENTITY_SLOTS:
            if linked.get(dimension):
                continue

            existing = session["link_info"].get(dimension) or {}
            # A supplied but unresolved mention belongs to the entity-linker
            # clarification flow. This helper only fills omitted fields.
            if existing.get("raw") and existing.get("status") in (
                "ambiguous",
                "unknown",
            ):
                continue

            filt = _resolved_identity_filter(session, exclude=dimension)
            if not filt:
                continue
            options = _distinct_for_session(session, dimension, filt)
            if len(options) == 1:
                pick = options[0]
                linked[dimension] = pick
                session["link_info"][dimension] = {
                    "raw": None,
                    "status": "matched",
                    "value": pick,
                    "method": "cascade:auto_sole",
                }
                progress = True
                print(
                    f"[conversation] refreshed sole "
                    f"{dimension}={pick!r}"
                )
            elif len(options) > 1:
                session["link_info"][dimension] = {
                    "raw": None,
                    "status": "ambiguous",
                    "candidates": options,
                    "method": "cascade:clarify",
                }


def _auto_confirm_sole_identity(session: dict) -> None:
    """If an unresolved link dimension has only one Mongo value left, take it."""
    linked = session["linked"]
    dimensions = (
        LIVES_CORE_LINK_DIMENSIONS if _is_lives(session) else LINK_DIMENSIONS
    )
    for dimension in dimensions:
        info = session["link_info"].get(dimension) or {}
        if info.get("status") not in ("ambiguous", "unknown"):
            continue
        if linked.get(dimension):
            continue
        filt = _resolved_identity_filter(session, exclude=dimension)
        options = _distinct_for_session(session, dimension, filt)
        candidates = info.get("candidates") or []
        if candidates:
            options = [option for option in options if option in candidates] or options
        if len(options) == 1:
            pick = options[0]
            linked[dimension] = pick
            info = dict(info)
            info["status"] = "matched"
            info["value"] = pick
            info["method"] = "auto_sole"
            session["link_info"][dimension] = info
            print(
                f"[conversation] auto-confirmed sole "
                f"{dimension}={pick!r}"
            )


def _first_pending_link(
    link_info: dict, *, lives: bool = False
) -> Optional[tuple[str, dict]]:
    """Prefer ambiguous-with-candidates, then unknown-with-candidates."""
    dimensions = LIVES_CORE_LINK_DIMENSIONS if lives else LINK_DIMENSIONS
    for status in ("ambiguous", "unknown"):
        for dimension in dimensions:
            info = link_info.get(dimension) or {}
            if info.get("status") != status:
                continue
            candidates = info.get("candidates") or []
            if candidates:
                return dimension, info
    return None


def _scoped_candidates(
    session: dict, dimension: str, fallback: list[str]
) -> list[str]:
    filt = _resolved_identity_filter(session, exclude=dimension)
    scoped = _distinct_for_session(session, dimension, filt)
    if not scoped:
        return list(fallback)
    if fallback:
        overlap = [value for value in scoped if value in set(fallback)]
        return overlap or scoped
    return scoped
