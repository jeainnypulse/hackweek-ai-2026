from bedrock import get_embedding
from db import get_db


def vector_search_retrieval(question: str, top_k: int = 10) -> list[dict]:
    embedding = get_embedding(question)
    db = get_db()
    pipeline = [
        {
            "$vectorSearch": {
                "index": "qoa_vector_index",
                "path": "embedding",
                "queryVector": embedding,
                "numCandidates": top_k * 10,
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
    records = list(db.payerLatestAccess.aggregate(pipeline))
    print(f"[vector_search] {len(records)} records found")
    return records
