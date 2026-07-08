from bedrock import get_embedding
from db import get_db
from vector_index import EMBEDDING_PATH, INDEX_NAME, ensure_vector_search_ready


def vector_search_retrieval(question: str, top_k: int = 10) -> list[dict]:
    ensure_vector_search_ready()

    embedding = get_embedding(question)
    db = get_db()
    num_candidates = max(top_k * 10, 100)
    pipeline = [
        {
            "$vectorSearch": {
                "index": INDEX_NAME,
                "path": EMBEDDING_PATH,
                "queryVector": embedding,
                "numCandidates": num_candidates,
                "limit": top_k,
            }
        },
        {
            "$project": {
                "_id": 0,
                "score": {"$meta": "vectorSearchScore"},
                "organization": 1,
                "organizationTiny": 1,
                "regimen": 1,
                "indication": 1,
                "line": 1,
                "book": 1,
                "coverage": 1,
                "population": 1,
                "accessData": 1,
                "tierData": 1,
                "policyLinkData": 1,
                "additionalCriteriaData": 1,
                "timestamp": 1,
            }
        },
    ]

    try:
        records = list(db.payerLatestAccess.aggregate(pipeline))
    except Exception as exc:
        error = str(exc)
        if "index" in error.lower() or "vectorSearch" in error:
            raise RuntimeError(
                f"MongoDB $vectorSearch failed for index '{INDEX_NAME}'. "
                "Create the Atlas vector search index with "
                "python scripts/setup_vector_search.py --create-index. "
                f"Original error: {error}"
            ) from exc
        raise

    print(f"[vector_search] {len(records)} records found")
    return records
