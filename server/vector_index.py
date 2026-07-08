import os
from typing import Optional

from db import get_db

INDEX_NAME = os.getenv("VECTOR_SEARCH_INDEX", "qoa_vector_index")
EMBEDDING_PATH = os.getenv("VECTOR_SEARCH_EMBEDDING_PATH", "embedding")
EMBEDDING_DIMENSIONS = int(os.getenv("VECTOR_SEARCH_DIMENSIONS", "1024"))


def list_search_indexes(collection=None) -> list[dict]:
    if collection is None:
        collection = get_db().payerLatestAccess
    try:
        return list(collection.list_search_indexes())
    except Exception:
        return []


def get_index_status(collection=None) -> Optional[dict]:
    for index in list_search_indexes(collection):
        if index.get("name") == INDEX_NAME:
            return index
    return None


def get_vector_search_status(collection=None) -> dict:
    if collection is None:
        collection = get_db().payerLatestAccess
    total = collection.count_documents({})
    embedded = collection.count_documents({EMBEDDING_PATH: {"$exists": True}})
    index = get_index_status(collection)
    indexes = [
        {
            "name": item.get("name"),
            "status": item.get("status"),
            "queryable": item.get("queryable"),
        }
        for item in list_search_indexes(collection)
    ]

    if embedded == 0:
        message = (
            "No embeddings found on payerLatestAccess. "
            "Run: python scripts/setup_vector_search.py --create-index --embed --regimen Brukinsa"
        )
        ready = False
    elif not index:
        message = (
            f"MongoDB vector search index '{INDEX_NAME}' not found. "
            "Run: python scripts/setup_vector_search.py --create-index"
        )
        ready = False
    elif index.get("status") != "READY" or not index.get("queryable"):
        message = (
            f"Vector search index '{INDEX_NAME}' is still building "
            f"(status={index.get('status')}). Wait a few minutes and retry."
        )
        ready = False
    else:
        message = "Vector search is ready."
        ready = True

    return {
        "ready": ready,
        "index_name": INDEX_NAME,
        "embedding_path": EMBEDDING_PATH,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "total_records": total,
        "embedded_records": embedded,
        "index": index,
        "indexes": indexes,
        "message": message,
    }


def create_vector_search_index(collection=None) -> dict:
    if collection is None:
        collection = get_db().payerLatestAccess
    existing = get_index_status(collection)
    if existing:
        return {
            "created": False,
            "index": existing,
            "message": f"Index '{INDEX_NAME}' already exists (status={existing.get('status')}).",
        }

    db = collection.database
    db.command(
        {
            "createSearchIndexes": collection.name,
            "indexes": [
                {
                    "name": INDEX_NAME,
                    "type": "vectorSearch",
                    "definition": {
                        "fields": [
                            {
                                "type": "vector",
                                "path": EMBEDDING_PATH,
                                "numDimensions": EMBEDDING_DIMENSIONS,
                                "similarity": "cosine",
                            }
                        ]
                    },
                }
            ],
        }
    )
    index = get_index_status(collection)
    return {
        "created": True,
        "index": index,
        "message": (
            f"Created vector search index '{INDEX_NAME}'. "
            "It may take a few minutes to become queryable."
        ),
    }


ATLAS_VECTOR_INDEX_JSON = """{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1024,
      "similarity": "cosine"
    }
  ]
}"""


def atlas_index_instructions() -> str:
    return (
        "Create the vector search index manually in MongoDB Atlas:\n"
        "  1. Atlas -> Browse Collections -> pulse-dev -> payerLatestAccess -> Search Indexes\n"
        "  2. Create Search Index -> JSON Editor -> Vector Search\n"
        f"  3. Index name: {INDEX_NAME}\n"
        f"  4. Paste this definition:\n{ATLAS_VECTOR_INDEX_JSON}"
    )


def ensure_vector_search_ready(collection=None) -> None:
    status = get_vector_search_status(collection)
    if not status["ready"]:
        raise RuntimeError(status["message"])
