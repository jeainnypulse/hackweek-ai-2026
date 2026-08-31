from __future__ import annotations

ACCESS_SLOTS = (
    "regimen",
    "indication",
    "book",
    "organization",
    "coverage",
    "population",
    "line",
)
LIVES_SLOTS = ("organization", "book", "territoryName", "coverage")
JOINT_SLOTS = (*ACCESS_SLOTS, "territoryName")

SLOT_LABELS = {
    "regimen": "product",
    "indication": "indication",
    "book": "book",
    "organization": "payer",
    "coverage": "coverage",
    "population": "subtype",
    "line": "line of therapy",
    "territoryName": "territory",
}


def required_slots(intent: str) -> tuple[str, ...]:
    if intent == "lives":
        return LIVES_SLOTS
    if intent == "joint":
        return JOINT_SLOTS
    return ACCESS_SLOTS
