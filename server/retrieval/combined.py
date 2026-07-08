from retrieval.exact_match import exact_match_retrieval
from bedrock import get_embedding
from db import get_db
from vector_index import EMBEDDING_PATH, INDEX_NAME

def combined_retrieval(question: str) -> list[dict]:
    payer_records = exact_match_retrieval(question)
    doc_chunks = retrieve_doc_chunks(question)
    print(f"[combined] {len(payer_records)} payer records + {len(doc_chunks)} doc chunks")
    return payer_records + doc_chunks

def retrieve_doc_chunks(question: str, top_k: int = 5) -> list[dict]:
    embedding = get_embedding(question)
    db = get_db()
    pipeline = [
        {
            "$vectorSearch": {
                "index": INDEX_NAME,
                "path": EMBEDDING_PATH,
                "queryVector": embedding,
                "numCandidates": top_k * 10,
                "limit": top_k,
            }
        },
        {
            "$project": {
                "_id": 0,
                "score": {"$meta": "vectorSearchScore"},
                "text": 1,
                "source_file": 1,
                "slide_or_page": 1,
                "chunk_type": {"$literal": "document"},
            }
        }
    ]
    return list(db.doc_chunks.aggregate(pipeline))
