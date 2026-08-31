from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import uuid
from typing import Optional, Union

load_dotenv()

from chatbot.answer_retrieval import handle_conversation_turn
from chatbot.synthesis.synthesis import synthesize_answer
from infrastructure.db import get_db
from chatbot.synthesis.feedback import save_feedback

app = FastAPI(title="Pulse AI Chatbot", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str = ""
    retrieval_method: str = "exact_match_entity_linker"
    skip_log: bool = False
    session_id: Optional[str] = None
    action: Optional[str] = None  # provide | skip | cascade_options | submit_cascade
    value: Optional[Union[str, dict]] = None


class FeedbackRequest(BaseModel):
    id: str
    rating: str


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/health/bedrock")
def health_bedrock():
    from infrastructure.bedrock import invoke_claude

    try:
        invoke_claude("Reply with OK only.", max_tokens=10)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def _finalize_ask(
    question: str,
    records: list,
    retrieval_method: str,
    skip_log: bool,
    confirmed_slots: Optional[dict] = None,
    intent: Optional[str] = None,
    answer: Optional[str] = None,
    citations: Optional[list] = None,
) -> dict:
    intent = intent or "access"
    if answer is None:
        answer, citations = synthesize_answer(
            question,
            records,
            intent=intent,
            confirmed_slots=confirmed_slots,
        )
    else:
        citations = list(citations or [])
    result_id = str(uuid.uuid4())
    if not skip_log:
        db = get_db()
        db.logs.insert_one(
            {
                "_id": result_id,
                "question": question,
                "retrieval_method": retrieval_method,
                "intent": intent,
                "records_retrieved": len(records),
                "answer": answer,
                "citations": citations,
                "confirmed_slots": confirmed_slots,
                "feedback": None,
            }
        )
    return {
        "id": result_id,
        "status": "complete",
        "question": question,
        "answer": answer,
        "citations": citations,
        "retrieval_method": retrieval_method,
        "intent": intent,
        "records_retrieved": len(records),
        "confirmed_slots": confirmed_slots or {},
        "session_id": None,
    }


@app.post("/api/ask")
def ask(req: AskRequest):
    try:
        method = req.retrieval_method or "exact_match_entity_linker"
        # "exact_match" kept as deprecated alias for older clients
        if method not in ("exact_match_entity_linker", "exact_match"):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown retrieval method: {method}",
            )

        if req.session_id and req.action:
            turn = handle_conversation_turn(
                session_id=req.session_id,
                action=req.action,
                value=req.value,
            )
        elif req.question.strip():
            turn = handle_conversation_turn(question=req.question.strip())
        else:
            raise HTTPException(
                status_code=400,
                detail="Question cannot be empty",
            )

        if turn.get("status") == "complete":
            intent = turn.get("intent")
            if intent == "out_of_scope":
                result = _finalize_ask(
                    turn.get("question") or req.question,
                    [],
                    method,
                    req.skip_log,
                    confirmed_slots=turn.get("confirmed_slots") or {},
                    intent="out_of_scope",
                    answer=turn.get("message") or "",
                    citations=[],
                )
            else:
                result = _finalize_ask(
                    turn.get("question") or req.question,
                    turn.get("records") or [],
                    method,
                    req.skip_log,
                    confirmed_slots=turn.get("confirmed_slots"),
                    intent=intent,
                )
            result["session_id"] = turn.get("session_id")
            result["link_info"] = turn.get("link_info") or {}
            return result

        return {
            "id": None,
            "status": turn.get("status"),
            "session_id": turn.get("session_id"),
            "question": turn.get("question") or req.question,
            "message": turn.get("message"),
            "current_slot": turn.get("current_slot"),
            "proposed_value": turn.get("proposed_value"),
            "candidates": turn.get("candidates") or [],
            "confirmed_slots": turn.get("confirmed_slots") or {},
            "allow_skip": bool(turn.get("allow_skip")),
            "input_mode": turn.get("input_mode") or "dropdown",
            "cascade": turn.get("cascade"),
            "link_info": turn.get("link_info") or {},
            "answer": turn.get("message"),
            "citations": [],
            "retrieval_method": method,
            "intent": turn.get("intent"),
            "records_retrieved": 0,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/feedback")
def feedback(req: FeedbackRequest):
    save_feedback(req.id, req.rating)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
