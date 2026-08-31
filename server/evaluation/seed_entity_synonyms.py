#!/usr/bin/env python3
"""
Seed Mongo entitySynonyms from data/entity_synonyms.json.

Usage (from server/):
  python scripts/seed_entity_synonyms.py
  python scripts/seed_entity_synonyms.py --force   # overwrite existing seed rows
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from dotenv import load_dotenv

load_dotenv(SERVER_DIR / ".env")

from chatbot.entity_linking import (  # noqa: E402
    SYNONYMS_COLLECTION,
    SYNONYMS_PATH,
    ensure_synonym_indexes,
    load_synonyms_from_json,
    reload_synonyms,
)
from infrastructure.db import get_db  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed entitySynonyms from JSON")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite docs that already exist (any source)",
    )
    parser.add_argument("--json", type=Path, default=SYNONYMS_PATH)
    args = parser.parse_args()

    seed = load_synonyms_from_json(args.json)
    ensure_synonym_indexes()
    col = get_db()[SYNONYMS_COLLECTION]
    now = datetime.utcnow()
    upserts = 0
    skipped = 0

    for dimension, pairs in seed.items():
        for alias_key, canonical in pairs.items():
            filt = {"dimension": dimension, "alias_key": alias_key}
            existing = col.find_one(filt)
            if existing and not args.force:
                # Do not clobber user_confirm rows
                if existing.get("source") == "user_confirm":
                    skipped += 1
                    continue
                if existing.get("canonical") == canonical:
                    skipped += 1
                    continue
            col.update_one(
                filt,
                {
                    "$set": {
                        "dimension": dimension,
                        "alias_key": alias_key,
                        "alias": alias_key,
                        "canonical": canonical,
                        "source": "seed",
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            upserts += 1

    reload_synonyms()
    total = col.count_documents({})
    print(
        f"[seed_entity_synonyms] upserted={upserts} skipped={skipped} "
        f"collection_total={total} path={args.json}"
    )


if __name__ == "__main__":
    main()
