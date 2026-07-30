from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import uuid
from typing import Optional, Union

load_dotenv()

from retrieval.exact_match import exact_match_turn
from retrieval.exact_match_finetuned import exact_match_finetuned_retrieval
from retrieval.vector_search import vector_search_retrieval
from synthesis import synthesize_answer


from db import get_db
from feedback import save_feedback
from eval import load_eval_questions, run_eval_questions
from eval_jobs import get_eval_job, get_eval_job_csv, start_eval_job, stop_eval_job

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


class EvalQuestion(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    question: str
    expected_answer: str = ""
    citation: Optional[str] = None
    retrieval_method: str = "exact_match_entity_linker"


class EvalRunRequest(BaseModel):
    questions: list[EvalQuestion] = []
    default_retrieval_method: str = "exact_match_entity_linker"
    csv_text: Optional[str] = None


class EvalJobStartRequest(BaseModel):
    questions: list[EvalQuestion] = []
    default_retrieval_method: str = "exact_match_entity_linker"
    csv_text: Optional[str] = None


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/health/bedrock")
def health_bedrock():
    from bedrock import invoke_claude

    try:
        invoke_claude("Reply with OK only.", max_tokens=10)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/health/vector-search")
def health_vector_search():
    from vector_index import get_vector_search_status

    status = get_vector_search_status()
    return status


def _finalize_ask(
    question: str,
    records: list,
    retrieval_method: str,
    skip_log: bool,
    confirmed_slots: Optional[dict] = None,
) -> dict:
    answer, citations = synthesize_answer(question, records)
    result_id = str(uuid.uuid4())
    if not skip_log:
        db = get_db()
        db.logs.insert_one(
            {
                "_id": result_id,
                "question": question,
                "retrieval_method": retrieval_method,
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
        "records_retrieved": len(records),
        "confirmed_slots": confirmed_slots or {},
        "session_id": None,
    }


@app.post("/api/ask")
def ask(req: AskRequest):
    try:
        # Exact match + entity linker with optional dropdown clarification
        # "exact_match" kept as deprecated alias
        if req.retrieval_method in ("exact_match_entity_linker", "exact_match"):
            if req.session_id and req.action:
                turn = exact_match_turn(
                    session_id=req.session_id,
                    action=req.action,
                    value=req.value,
                )
            elif req.question.strip():
                turn = exact_match_turn(question=req.question.strip())
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Question cannot be empty",
                )

            if turn.get("status") == "complete":
                result = _finalize_ask(
                    turn.get("question") or req.question,
                    turn.get("records") or [],
                    req.retrieval_method,
                    req.skip_log,
                    confirmed_slots=turn.get("confirmed_slots"),
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
                "retrieval_method": req.retrieval_method,
                "records_retrieved": 0,
            }

        if not req.question.strip():
            raise HTTPException(status_code=400, detail="Question cannot be empty")

        if req.retrieval_method == "vector_search":
            records = vector_search_retrieval(req.question)
        elif req.retrieval_method == "exact_match_finetuned":
            records = exact_match_finetuned_retrieval(req.question)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown retrieval method: {req.retrieval_method}",
            )
        return _finalize_ask(
            req.question, records, req.retrieval_method, req.skip_log
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))



@app.post("/api/feedback")
def feedback(req: FeedbackRequest):
    save_feedback(req.id, req.rating)
    return {"ok": True}


@app.get("/api/eval/questions")
def eval_questions():
    from eval import EVAL_CSV_PATH, FALLBACK_CSV_PATH

    if EVAL_CSV_PATH.exists():
        return {
            "csv": EVAL_CSV_PATH.read_text(encoding="utf-8"),
            "filename": "qa_pairs.csv",
        }
    if FALLBACK_CSV_PATH.exists():
        return {
            "csv": FALLBACK_CSV_PATH.read_text(encoding="utf-8"),
            "filename": "eval_questions.csv",
        }
    raise HTTPException(status_code=404, detail="No eval CSV found")


@app.post("/api/eval/run")
def eval_run(req: EvalRunRequest):
    questions = _resolve_eval_questions(req)
    for question in questions:
        if not question.get("retrieval_method"):
            question["retrieval_method"] = req.default_retrieval_method
    return run_eval_questions(questions)


def _resolve_eval_questions(
    req: Union[EvalRunRequest, EvalJobStartRequest],
) -> list[dict]:
    if req.questions:
        questions = [q.model_dump() for q in req.questions]
    elif req.csv_text:
        questions = load_eval_questions(req.csv_text)
    else:
        questions = load_eval_questions()

    if not questions:
        raise HTTPException(status_code=400, detail="No eval questions provided")
    return questions


@app.post("/api/eval/jobs/start")
def eval_job_start(req: EvalJobStartRequest):
    questions = _resolve_eval_questions(req)
    job_id = start_eval_job(questions, req.default_retrieval_method)
    return {"job_id": job_id, "total": len(questions)}


@app.get("/api/eval/jobs/{job_id}")
def eval_job_status(job_id: str):
    job = get_eval_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Eval job not found")
    return job


@app.post("/api/eval/jobs/{job_id}/stop")
def eval_job_stop(job_id: str):
    if not stop_eval_job(job_id):
        raise HTTPException(status_code=400, detail="Job is not running")
    return {"ok": True}


@app.get("/api/eval/jobs/{job_id}/results.csv")
def eval_job_results_csv(job_id: str):
    csv_text = get_eval_job_csv(job_id)
    if csv_text is None:
        raise HTTPException(status_code=404, detail="Results not available")
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="eval_results_{job_id[:8]}.csv"'
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
