import argparse
import os
import sys
import time
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from bedrock import get_embedding
from db import get_db
from embedding_utils import flatten_record
from vector_index import (
    EMBEDDING_PATH,
    INDEX_NAME,
    atlas_index_instructions,
    create_vector_search_index,
    get_vector_search_status,
)


def print_status() -> int:
    status = get_vector_search_status()
    print(f"Index name: {status['index_name']}")
    print(f"Embedding field: {status['embedding_path']}")
    print(f"Dimensions: {status['embedding_dimensions']}")
    print(f"Records: {status['embedded_records']} / {status['total_records']} embedded")
    if status["index"]:
        print(
            "Index status:",
            status["index"].get("status"),
            "queryable=",
            status["index"].get("queryable"),
        )
    else:
        print("Index status: not found")
    print(status["message"])
    return 0 if status["ready"] else 1


def embed_records(
    limit: Optional[int] = None,
    sleep_seconds: float = 0.1,
    regimen: Optional[str] = None,
) -> int:
    db = get_db()
    collection = db.payerLatestAccess
    query = {EMBEDDING_PATH: {"$exists": False}}
    if regimen:
        query["regimen"] = regimen
    total = collection.count_documents(query)
    if total == 0:
        print("All matching records already have embeddings.")
        return 0

    target = min(total, limit) if limit else total
    label = f" for regimen={regimen}" if regimen else ""
    print(f"Embedding {target} of {total} records without embeddings{label}...")

    cursor = collection.find(query, {"_id": 1}).limit(target)
    embedded = 0
    for record in cursor:
        full = collection.find_one({"_id": record["_id"]})
        text = flatten_record(full)
        embedding = get_embedding(text)
        collection.update_one(
            {"_id": record["_id"]},
            {"$set": {EMBEDDING_PATH: embedding, "embedding_text": text}},
        )
        embedded += 1
        if embedded % 10 == 0:
            print(f"  {embedded}/{target}")
            time.sleep(sleep_seconds)

    print(f"Done. Embedded {embedded} records.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up MongoDB Atlas vector search")
    parser.add_argument(
        "--status", action="store_true", help="Show vector search readiness"
    )
    parser.add_argument(
        "--create-index", action="store_true", help="Create Atlas vector search index"
    )
    parser.add_argument(
        "--embed", action="store_true", help="Embed records missing embeddings"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max records to embed (useful for initial testing)",
    )
    parser.add_argument(
        "--regimen",
        type=str,
        default=None,
        help="Only embed records for this regimen (e.g. Brukinsa)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Create index and embed records",
    )
    args = parser.parse_args()

    if not any([args.status, args.create_index, args.embed, args.all]):
        return print_status()

    if args.create_index or args.all:
        try:
            result = create_vector_search_index()
            print(result["message"])
        except Exception as exc:
            print(f"Could not create index programmatically: {exc}")
            print(atlas_index_instructions())

    if args.embed or args.all:
        embed_records(limit=args.limit, regimen=args.regimen)

    return print_status()


if __name__ == "__main__":
    raise SystemExit(main())
