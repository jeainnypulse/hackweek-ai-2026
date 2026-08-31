from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
SYNONYMS_PATH = DATA_DIR / "entity_synonyms.json"
SYNONYMS_COLLECTION = "entitySynonyms"

USE_SYNONYM_TABLE = os.getenv("ENTITY_LINK_USE_SYNONYMS", "0").lower() in (
    "1",
    "true",
    "yes",
)

IDENTITY_LINK_DIMENSIONS = ("organization", "regimen", "indication", "book")
TREATMENT_PLAN_LINK_DIMENSIONS = (
    *IDENTITY_LINK_DIMENSIONS,
    "coverage",
    "population",
    "line",
)
LINK_DIMENSIONS = TREATMENT_PLAN_LINK_DIMENSIONS
TERRITORY_LINK_DIMENSION = "territoryName"
SYNONYM_DIMENSIONS = (*LINK_DIMENSIONS, TERRITORY_LINK_DIMENSION)

FUZZY_ACCEPT_THRESHOLD = 85.0
FUZZY_AMBIGUOUS_GAP = 5.0
CATALOG_TTL_SEC = 300.0
SYNONYM_TTL_SEC = 300.0

WEAK_TOKENS = frozenset(
    {
        "blue",
        "cross",
        "blueshield",
        "bluecross",
        "shield",
        "health",
        "healthcare",
        "group",
        "the",
        "of",
        "and",
        "plan",
        "plans",
    }
)
