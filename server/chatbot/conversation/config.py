from __future__ import annotations

import os

from chatbot.query_resolution.entity_linking import (
    TERRITORY_LINK_DIMENSION,
    TREATMENT_PLAN_LINK_DIMENSIONS,
)

# none | catalog | synonym | fuzzy | cascade | hypothesize_verify
# cascade = synonym -> catalog -> LLM hypothesize+Mongo verify (production)
ENTITY_LINK_METHOD = os.getenv("ENTITY_LINK_METHOD", "cascade")

LIVES_CORE_LINK_DIMENSIONS = ("organization", "book", "coverage")
LIVES_LINK_DIMENSIONS = (*LIVES_CORE_LINK_DIMENSIONS, TERRITORY_LINK_DIMENSION)
JOINT_LINK_DIMENSIONS = (*TREATMENT_PLAN_LINK_DIMENSIONS, TERRITORY_LINK_DIMENSION)
