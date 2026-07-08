import sys, os, time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()
from db import get_db
from bedrock import get_embedding
from embedding_utils import flatten_record
from vector_index import EMBEDDING_PATH, INDEX_NAME


def main():
    db = get_db()
    collection = db.payerLatestAccess
    total = collection.count_documents({EMBEDDING_PATH: {"$exists": False}})
    print(f"Records to embed: {total}")
    if total == 0:
        print("All records already have embeddings.")
        return
    for i, record in enumerate(collection.find({EMBEDDING_PATH: {"$exists": False}})):
        text = flatten_record(record)
        embedding = get_embedding(text)
        collection.update_one(
            {"_id": record["_id"]},
            {"$set": {EMBEDDING_PATH: embedding, "embedding_text": text}},
        )
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{total}")
            time.sleep(0.3)
    print(f"Done. {total} records embedded.")
    print("\nNow create a vector search index in MongoDB Atlas:")
    print(
        f"  Collection: payerLatestAccess | Index: {INDEX_NAME} | Field: {EMBEDDING_PATH} | Dimensions: 1024 | Similarity: cosine"
    )
    print("Or run: python scripts/setup_vector_search.py --create-index")


if __name__ == "__main__":
    main()
