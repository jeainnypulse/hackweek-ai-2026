"""Cascading coverage → subtype → line clarify (from conversational exact match).

Used after identity slots are resolved. Auto-confirms a slot when Mongo has
only one distinct value left under the current filter.
"""

from __future__ import annotations

from typing import Any, Optional

from db import get_db

IDENTITY_SLOTS = ("regimen", "indication", "book", "organization")
CASCADE_SLOTS = ("coverage", "population", "line")

SKIP_POPULATION_VALUES = frozenset({"no subtype specified", "n/a", "na", "none", ""})


def _normalize_key(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def clean_slot_value(value) -> Optional[str]:
    if value is None or value in ("", "null"):
        return None
    text = str(value).strip()
    return text or None


def distinct_values(field: str, filter_query: dict) -> list[str]:
    db = get_db()
    values = db.payerLatestAccess.distinct(field, filter_query)
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


def population_candidates(candidates: list[str]) -> list[str]:
    return [c for c in candidates if _normalize_key(c) not in SKIP_POPULATION_VALUES]


def population_dropdown_options(candidates: list[str]) -> list[str]:
    if not population_candidates(candidates):
        return []
    return [
        c
        for c in candidates
        if _normalize_key(c) not in {"", "n/a", "na", "none"}
    ]


def resolve_selection(value: Optional[str], options: list[str]) -> Optional[str]:
    if not value:
        return None
    if value in options:
        return value
    for option in options:
        if _normalize_key(option) == _normalize_key(value):
            return option
    return None


def parse_selections(value: Optional[Any]) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {k: clean_slot_value(v) for k, v in value.items()}
    text = str(value).strip()
    if not text:
        return {}
    try:
        import json

        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: clean_slot_value(v) for k, v in parsed.items()}


def build_cascade_state(
    confirmed: dict, selections: Optional[dict] = None
) -> dict:
    """Build cascading dropdown options for coverage → subtype → line."""
    selections = selections or {}
    identity_filter = {
        k: v for k, v in confirmed.items() if k in IDENTITY_SLOTS and v is not None
    }

    coverage_options = distinct_values("coverage", identity_filter)
    selected_coverage = confirmed.get("coverage") or resolve_selection(
        selections.get("coverage"), coverage_options
    )
    if not selected_coverage and len(coverage_options) == 1:
        selected_coverage = coverage_options[0]

    population_options: list[str] = []
    if selected_coverage:
        population_options = population_dropdown_options(
            distinct_values(
                "population", {**identity_filter, "coverage": selected_coverage}
            )
        )
    selected_population = None
    if "population" in confirmed and confirmed.get("population"):
        selected_population = confirmed["population"]
    else:
        selected_population = resolve_selection(
            selections.get("population"), population_options
        )
    if not selected_population and len(population_options) == 1:
        selected_population = population_options[0]

    line_scope = dict(identity_filter)
    if selected_coverage:
        line_scope["coverage"] = selected_coverage
    if selected_population:
        line_scope["population"] = selected_population
    scoped_lines = distinct_values("line", line_scope)

    line_options: list[str] = []
    cascade_ready_for_line = bool(
        selected_coverage and (selected_population or not population_options)
    )
    if cascade_ready_for_line:
        line_options = scoped_lines
    elif len(scoped_lines) == 1:
        line_options = scoped_lines

    selected_line = None
    if "line" in confirmed and confirmed.get("line"):
        selected_line = confirmed["line"]
    else:
        selected_line = resolve_selection(selections.get("line"), line_options)
    if not selected_line and len(line_options) == 1:
        selected_line = line_options[0]

    show_coverage = "coverage" not in confirmed or not confirmed.get("coverage")
    show_population = bool(population_options) and (
        "population" not in confirmed or not confirmed.get("population")
    )
    if show_coverage and not selected_coverage:
        has_any_subtype = any(
            population_candidates(
                distinct_values("population", {**identity_filter, "coverage": cov})
            )
            for cov in coverage_options
        )
        show_population = has_any_subtype

    line_already_confirmed = bool(confirmed.get("line"))
    show_line = (not line_already_confirmed) and len(scoped_lines) > 1

    pending_slots = []
    if show_coverage and coverage_options:
        pending_slots.append("coverage")
    if show_population:
        pending_slots.append("population")
    if show_line:
        pending_slots.append("line")

    return {
        "pending_slots": pending_slots,
        "selections": {
            "coverage": selected_coverage,
            "population": selected_population,
            "line": selected_line,
        },
        "options": {
            "coverage": coverage_options,
            "population": population_options,
            "line": line_options,
        },
        "allow_skip": {
            "population": True,
            "line": True,
        },
    }


def cascade_needed(confirmed: dict) -> bool:
    base = filter_from_confirmed(
        {k: v for k, v in confirmed.items() if k in IDENTITY_SLOTS}
    )
    if "coverage" not in confirmed or not confirmed.get("coverage"):
        if distinct_values("coverage", base):
            return True
    coverage = confirmed.get("coverage")
    if coverage and ("population" not in confirmed or not confirmed.get("population")):
        if population_candidates(
            distinct_values("population", {**base, "coverage": coverage})
        ):
            return True
    if coverage and ("line" not in confirmed or not confirmed.get("line")):
        line_filter = {**base, "coverage": coverage}
        if confirmed.get("population"):
            line_filter["population"] = confirmed["population"]
        if distinct_values("line", line_filter):
            return True
    if "coverage" not in confirmed or not confirmed.get("coverage"):
        for cov in distinct_values("coverage", base):
            if population_candidates(
                distinct_values("population", {**base, "coverage": cov})
            ):
                return True
            if distinct_values("line", {**base, "coverage": cov}):
                return True
    return False


def auto_confirm_sole_cascade_options(
    confirmed: dict, selections: Optional[dict] = None
) -> dict:
    """Move sole-option cascade fields into confirmed (don't ask the user)."""
    selections = {
        field: confirmed.get(field) or (selections or {}).get(field)
        for field in CASCADE_SLOTS
    }
    for _ in range(len(CASCADE_SLOTS) + 1):
        cascade = build_cascade_state(confirmed, selections)
        options = cascade["options"]
        picked = cascade["selections"]
        progressed = False
        for field in CASCADE_SLOTS:
            if confirmed.get(field):
                continue
            field_options = options.get(field) or []
            value = picked.get(field)
            if len(field_options) == 1 and value == field_options[0]:
                confirmed[field] = value
                selections[field] = value
                progressed = True
                print(
                    f"[cascade_clarify] auto-confirmed sole {field}={value!r}"
                )
        if not progressed:
            break
        for field in CASCADE_SLOTS:
            if picked.get(field):
                selections[field] = picked[field]
    return selections
