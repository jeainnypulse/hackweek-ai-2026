import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from db import get_db
from bedrock import get_embedding
import time

def flatten_record(record: dict) -> str:
    parts = []
    if record.get("payer"):            parts.append(record["payer"])
    if record.get("drug"):             parts.append(record["drug"])
    if record.get("indication"):       parts.append(f"indication {record['indication']}")
    if record.get("book_of_business"): parts.append(record["book_of_business"])
    if record.get("qoa_segment"):      parts.append(f"QoA {record['qoa_segment']}")
    if record.get("lives_pct"):        parts.append(f"{record['lives_pct']}% of lives")
    if record.get("report_period"):    parts.append(f"report period {record['report_period']}")
    return " ".join(parts)

def main():
    db = get_db()
    collection = db.payerLatestAccess
    total = collection.count_documents({"embedding": {"$exists": False}})
    print(f"Records to embed: {total}")
    if total == 0:
        print("All records already have embeddings.")
        return
    for i, record in enumerate(collection.find({"embedding": {"$exists": False}})):
        text = flatten_record(record)
        embedding = get_embedding(text)
        collection.update_one({"_id": record["_id"]}, {"$set": {"embedding": embedding, "embedding_text": text}})
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{total}")
            time.sleep(0.3)
    print(f"Done. {total} records embedded.")
    print("\nNow create a vector search index in MongoDB Atlas:")
    print("  Collection: payerLatestAccess | Index: qoa_vector_index | Field: embedding | Dimensions: 1024 | Similarity: cosine")

if __name__ == "__main__":
    main()
