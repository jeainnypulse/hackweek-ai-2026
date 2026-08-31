"""Compatibility facade for the entity-linking subsystem."""

from chatbot.query_resolution.linking.cascade import resolve_cascade
from chatbot.query_resolution.linking.catalog_match import (
    resolve_catalog,
    resolve_fuzzy,
    resolve_fuzzy_synonym,
    resolve_none,
    resolve_synonym,
)
from chatbot.query_resolution.linking.catalogs import (
    load_catalogs_from_db,
    load_territory_catalog_from_db,
    set_catalogs,
    territory_type_for_canonical,
)
from chatbot.query_resolution.linking.config import (
    CATALOG_TTL_SEC,
    FUZZY_ACCEPT_THRESHOLD,
    FUZZY_AMBIGUOUS_GAP,
    IDENTITY_LINK_DIMENSIONS,
    LINK_DIMENSIONS,
    SYNONYM_DIMENSIONS,
    SYNONYM_TTL_SEC,
    SYNONYMS_COLLECTION,
    SYNONYMS_PATH,
    TERRITORY_LINK_DIMENSION,
    TREATMENT_PLAN_LINK_DIMENSIONS,
    USE_SYNONYM_TABLE,
)
from chatbot.query_resolution.linking.hypothesis import (
    resolve_hypothesize_verify,
)
from chatbot.query_resolution.linking.normalization import (
    normalize_dimension_key,
    normalize_key,
)
from chatbot.query_resolution.linking.registry import METHODS, resolve
from chatbot.query_resolution.linking.slot_linking import link_slots
from chatbot.query_resolution.linking.synonyms import (
    ensure_synonym_indexes,
    load_synonyms,
    load_synonyms_from_json,
    reload_synonyms,
    upsert_synonym,
)

__all__ = [
    "CATALOG_TTL_SEC",
    "FUZZY_ACCEPT_THRESHOLD",
    "FUZZY_AMBIGUOUS_GAP",
    "IDENTITY_LINK_DIMENSIONS",
    "LINK_DIMENSIONS",
    "METHODS",
    "SYNONYM_DIMENSIONS",
    "SYNONYM_TTL_SEC",
    "SYNONYMS_COLLECTION",
    "SYNONYMS_PATH",
    "TERRITORY_LINK_DIMENSION",
    "TREATMENT_PLAN_LINK_DIMENSIONS",
    "USE_SYNONYM_TABLE",
    "ensure_synonym_indexes",
    "link_slots",
    "load_catalogs_from_db",
    "load_synonyms",
    "load_synonyms_from_json",
    "load_territory_catalog_from_db",
    "normalize_dimension_key",
    "normalize_key",
    "reload_synonyms",
    "resolve",
    "resolve_cascade",
    "resolve_catalog",
    "resolve_fuzzy",
    "resolve_fuzzy_synonym",
    "resolve_hypothesize_verify",
    "resolve_none",
    "resolve_synonym",
    "set_catalogs",
    "territory_type_for_canonical",
    "upsert_synonym",
]
