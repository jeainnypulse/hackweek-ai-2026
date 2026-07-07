from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import uuid

load_dotenv()

from retrieval.exact_match import exact_match_retrieval
from retrieval.vector_search import vector_search_retrieval
from retrieval.combined import combined_retrieval
from synthesis import synthesize_answer
from db import get_db
from feedback import save_feedback

app = FastAPI(title="Pulse AI Chatbot", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class AskRequest(BaseModel):
    question: str
    retrieval_method: str = "exact_match"

class FeedbackRequest(BaseModel):
    id: str
    rating: str

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.post("/api/ask")
def ask(req: AskRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    if req.retrieval_method == "exact_match":
        records = exact_match_retrieval(req.question)
    elif req.retrieval_method == "vector_search":
        records = vector_search_retrieval(req.question)
    elif req.retrieval_method == "combined":
        records = combined_retrieval(req.question)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown retrieval method: {req.retrieval_method}")
    answer, citations = synthesize_answer(req.question, records)
    result_id = str(uuid.uuid4())
    db = get_db()
    db.logs.insert_one({
        "_id": result_id,
        "question": req.question,
        "retrieval_method": req.retrieval_method,
        "records_retrieved": len(records),
        "answer": answer,
        "citations": citations,
        "feedback": None,
    })
    return {
        "id": result_id,
        "question": req.question,
        "answer": answer,
        "citations": citations,
        "retrieval_method": req.retrieval_method,
        "records_retrieved": len(records),
    }

@app.post("/api/feedback")
def feedback(req: FeedbackRequest):
    save_feedback(req.id, req.rating)
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
