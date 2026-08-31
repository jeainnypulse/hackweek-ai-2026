from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from chatbot.query_resolution.linking import config
from chatbot.query_resolution.linking.normalization import normalize_dimension_key

_synonym_cache: dict[str, dict[str, str]] = {}
_synonym_loaded_at: float = 0.0
_synonym_indexes_ready = False


def empty_synonym_map() -> dict[str, dict[str, str]]:
    return {dimension: {} for dimension in config.SYNONYM_DIMENSIONS}


def load_synonyms_from_json(
    path: Path = config.SYNONYMS_PATH,
) -> dict[str, dict[str, str]]:
    """Load seed synonyms from flat or metadata-wrapped JSON."""
    out = empty_synonym_map()
    if not path.exists():
        return out
    raw = json.loads(path.read_text())
    if isinstance(raw.get("synonyms"), dict):
        raw = raw["synonyms"]
    for dimension, pairs in raw.items():
        if (
            not isinstance(dimension, str)
            or dimension.startswith("_")
            or not isinstance(pairs, dict)
        ):
            continue
        out.setdefault(dimension, {})
        for alias, canonical in pairs.items():
            if isinstance(alias, str) and isinstance(canonical, str):
                out[dimension][normalize_dimension_key(dimension, alias)] = canonical
    return out


def ensure_synonym_indexes() -> None:
    global _synonym_indexes_ready
    if _synonym_indexes_ready:
        return
    from infrastructure.db import get_db

    get_db()[config.SYNONYMS_COLLECTION].create_index(
        [("dimension", 1), ("alias_key", 1)],
        unique=True,
        name="dimension_alias_key_unique",
    )
    _synonym_indexes_ready = True


def load_synonyms(force: bool = False) -> dict[str, dict[str, str]]:
    global _synonym_cache, _synonym_loaded_at
    if not config.USE_SYNONYM_TABLE:
        _synonym_cache = empty_synonym_map()
        _synonym_loaded_at = time.time()
        return _synonym_cache

    now = time.time()
    if (
        not force
        and _synonym_cache
        and (now - _synonym_loaded_at) < config.SYNONYM_TTL_SEC
    ):
        return _synonym_cache

    out = empty_synonym_map()
    try:
        from infrastructure.db import get_db

        ensure_synonym_indexes()
        docs = list(
            get_db()[config.SYNONYMS_COLLECTION].find(
                {}, {"dimension": 1, "alias_key": 1, "canonical": 1}
            )
        )
        if docs:
            for doc in docs:
                dimension = doc.get("dimension")
                alias_key = doc.get("alias_key")
                canonical = doc.get("canonical")
                if (
                    dimension in out
                    and isinstance(alias_key, str)
                    and isinstance(canonical, str)
                    and canonical.strip()
                ):
                    out[dimension][
                        normalize_dimension_key(dimension, alias_key)
                    ] = canonical
            _synonym_cache = out
            _synonym_loaded_at = now
            return out
    except Exception as exc:
        print(f"[entity_linking] Mongo synonyms unavailable ({exc}); using JSON seed")

    _synonym_cache = load_synonyms_from_json()
    _synonym_loaded_at = now
    return _synonym_cache


def reload_synonyms() -> None:
    global _synonym_cache, _synonym_loaded_at
    _synonym_cache = {}
    _synonym_loaded_at = 0.0
    load_synonyms(force=True)


def upsert_synonym(
    dimension: str,
    alias: str,
    canonical: str,
    *,
    source: str = "user_confirm",
) -> dict[str, str]:
    if dimension not in config.SYNONYM_DIMENSIONS:
        raise ValueError(f"Unsupported synonym dimension: {dimension}")
    alias_key = normalize_dimension_key(dimension, alias)
    canonical = str(canonical).strip()
    if not alias_key or not canonical:
        raise ValueError("alias and canonical are required")

    from infrastructure.db import get_db

    ensure_synonym_indexes()
    now = datetime.utcnow()
    get_db()[config.SYNONYMS_COLLECTION].update_one(
        {"dimension": dimension, "alias_key": alias_key},
        {
            "$set": {
                "dimension": dimension,
                "alias_key": alias_key,
                "alias": str(alias).strip(),
                "canonical": canonical,
                "source": source,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    global _synonym_cache, _synonym_loaded_at
    if not _synonym_cache:
        _synonym_cache = empty_synonym_map()
    _synonym_cache.setdefault(dimension, {})[alias_key] = canonical
    _synonym_loaded_at = time.time()
    return {"dimension": dimension, "alias_key": alias_key, "canonical": canonical}
