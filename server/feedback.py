from db import get_db
from datetime import datetime

def save_feedback(result_id: str, rating: str):
    db = get_db()
    db.logs.update_one(
        {"_id": result_id},
        {"$set": {"feedback": rating, "feedback_at": datetime.utcnow()}}
    )
