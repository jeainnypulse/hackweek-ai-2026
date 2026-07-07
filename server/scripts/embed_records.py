import sys, os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()
from db import get_db
from bedrock import get_embedding
import time


def flatten_record(record: dict) -> str:
    parts = []
    if record.get("organization"):
        parts.append(record["organization"])
    if record.get("regimen"):
        parts.append(record["regimen"])
    if record.get("indication"):
        parts.append(record["indication"])
    if record.get("line"):
        parts.append(record["line"])
    if record.get("book"):
        parts.append(record["book"])
    if record.get("coverage"):
        parts.append(record["coverage"])
    if record.get("population"):
        parts.append(record["population"])

    access = record.get("accessData") or {}
    if access.get("access"):
        parts.append(f"access {access['access']}")
    if access.get("score") is not None:
        parts.append(f"score {access['score']}")

    tier = record.get("tierData") or {}
    if tier.get("tierRating"):
        parts.append(tier["tierRating"])

    for item in record.get("additionalCriteriaData") or []:
        if item.get("criteria"):
            parts.append(item["criteria"])

    caption = (access.get("caption") or {}).get("General") or (access.get("caption") or {}).get("ALL")
    if caption:
        parts.append(caption)

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
        collection.update_one(
            {"_id": record["_id"]},
            {"$set": {"embedding": embedding, "embedding_text": text}},
        )
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{total}")
            time.sleep(0.3)
    print(f"Done. {total} records embedded.")
    print("\nNow create a vector search index in MongoDB Atlas:")
    print(
        "  Collection: payerLatestAccess | Index: qoa_vector_index | Field: embedding | Dimensions: 1024 | Similarity: cosine"
    )


if __name__ == "__main__":
    main()
