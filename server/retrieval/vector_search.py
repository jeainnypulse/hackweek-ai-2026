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
                "drug": 1,
                "indication": 1,
                "book_of_business": 1,
                "payer": 1,
                "qoa_segment": 1,
                "lives_pct": 1,
                "report_period": 1,
            }
        }
    ]
    records = list(db.payerLatestAccess.aggregate(pipeline))
    print(f"[vector_search] {len(records)} records found")
    return records
