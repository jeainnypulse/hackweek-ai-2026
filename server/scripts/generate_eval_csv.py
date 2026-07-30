"""Generate client/public/eval_questions.csv from payerLatestAccess."""

import csv
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()
from db import get_db

TEMPLATES = (
    "What is {regimen} access for {indication} under {organization} {book}?",
    "What access level does {organization} assign to {regimen} for {indication} on {book} plans?",
    "Does {organization} cover {regimen} for {line} {indication} on {book}?",
)


def main(limit: int = 50) -> None:
    col = get_db().payerLatestAccess
    pipeline = [
        {"$match": {"accessData.access": {"$exists": True, "$ne": None}}},
        {
            "$group": {
                "_id": {
                    "organization": "$organization",
                    "regimen": "$regimen",
                    "indication": "$indication",
                    "line": "$line",
                    "book": "$book",
                    "access": "$accessData.access",
                    "score": "$accessData.score",
                    "tierRating": "$tierData.tierRating",
                }
            }
        },
        {"$sort": {"_id.organization": 1, "_id.regimen": 1}},
        {"$limit": limit},
    ]

    rows = []
    for i, doc in enumerate(col.aggregate(pipeline)):
        combo = doc["_id"]
        line = combo.get("line") or ""
        line_text = line if line and line != "N/A" else combo["indication"]
        question = TEMPLATES[i % len(TEMPLATES)].format(
            regimen=combo["regimen"],
            indication=combo["indication"],
            organization=combo["organization"],
            book=combo["book"],
            line=line_text,
        )
        expected_parts = [combo["access"]]
        if combo.get("score") is not None:
            expected_parts.append(f"score {combo['score']}")
        if combo.get("tierRating"):
            expected_parts.append(combo["tierRating"])

        rows.append(
            {
                "id": i + 1,
                "question": question,
                "expected_answer": "; ".join(expected_parts),
                "retrieval_method": "exact_match_entity_linker",
            }
        )

    out = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "client",
        "public",
        "eval_questions.csv",
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "question", "expected_answer", "retrieval_method"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 50)
