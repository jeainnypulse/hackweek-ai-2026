import csv
import io
import json
import threading
import time
import uuid
from typing import Optional

from eval import compare_eval_result
from retrieval.exact_match import extract_slots

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def results_to_csv(results: list[dict]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "type", "question", "expected_access", "expected_slots", "citation",
        "actual_answer", "actual_slots", "match", "access_match", "access_score",
        "slots_match", "slots_score", "records_retrieved", "error",
    ])
    for row in results:
        comparison = row.get("comparison") or {}
        access = comparison.get("access") or {}
        slots = comparison.get("slots") or {}
        writer.writerow([
            row.get("id", ""),
            row.get("type", ""),
            row.get("question", ""),
            row.get("expected_access", ""),
            json.dumps(row.get("expected_slots")) if row.get("expected_slots") else "",
            row.get("citation", ""),
            row.get("actual_answer", ""),
            json.dumps(comparison.get("actual_slots")) if comparison.get("actual_slots") else "",
            comparison.get("match"),
            access.get("match"),
            access.get("score"),
            slots.get("match"),
            slots.get("score"),
            row.get("records_retrieved", 0),
            row.get("error") or "",
        ])
    return output.getvalue()


def _job_snapshot(job: dict) -> dict:
    scored = job.get("scored", 0)
    passed = job.get("passed", 0)
    return {
        "id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "total": job["total"],
        "passed": passed,
        "pass_rate": round(passed / scored, 2) if scored else 0.0,
        "error": job.get("error"),
        "results": job.get("results", []),
    }


def _run_job(job_id: str, questions: list[dict]) -> None:
    job = _jobs[job_id]
    results = []
    passed = 0
    scored = 0

    try:
        for index, item in enumerate(questions):
            if job.get("cancelled"):
                job["status"] = "stopped"
                break

            method = item.get("retrieval_method") or "exact_match"
            from eval import RETRIEVAL_HANDLERS

            handler = RETRIEVAL_HANDLERS.get(method)
            if not handler:
                row = {
                    **item,
                    "actual_answer": "",
                    "records_retrieved": 0,
                    "comparison": {
                        "match": None,
                        "access": {"score": None, "match": None, "matched_terms": []},
                        "slots": {"score": None, "match": None, "matched_fields": [], "mismatched_fields": []},
                        "actual_slots": None,
                    },
                    "error": f"Unknown retrieval method: {method}",
                }
            else:
                try:
                    actual_slots = extract_slots(item["question"])
                    if method == "exact_match":
                        records = handler(item["question"], slots=actual_slots)
                    else:
                        records = handler(item["question"])
                    from synthesis import synthesize_answer

                    actual_answer, _citations = synthesize_answer(item["question"], records)
                    comparison = compare_eval_result(item, actual_answer, actual_slots)
                    if comparison["match"] is True:
                        passed += 1
                    access = comparison.get("access") or {}
                    slots = comparison.get("slots") or {}
                    if access.get("score") is not None or slots.get("score") is not None:
                        scored += 1
                    row = {
                        **item,
                        "actual_answer": actual_answer,
                        "records_retrieved": len(records),
                        "comparison": comparison,
                        "error": None,
                    }
                except Exception as exc:
                    row = {
                        **item,
                        "actual_answer": "",
                        "records_retrieved": 0,
                        "comparison": {
                            "match": False,
                            "access": {"score": None, "match": False, "matched_terms": []},
                            "slots": {"score": None, "match": False, "matched_fields": [], "mismatched_fields": []},
                            "actual_slots": None,
                        },
                        "error": str(exc),
                    }

            results.append(row)
            job["progress"] = index + 1
            job["results"] = results
            job["passed"] = passed
            job["scored"] = scored

            if index < len(questions) - 1 and not job.get("cancelled"):
                time.sleep(0.75)

        if job["status"] == "running":
            job["status"] = "completed"
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
    finally:
        job["finished"] = True


def start_eval_job(questions: list[dict], default_retrieval_method: str = "exact_match") -> str:
    for question in questions:
        if not question.get("retrieval_method"):
            question["retrieval_method"] = default_retrieval_method

    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "status": "running",
        "progress": 0,
        "total": len(questions),
        "passed": 0,
        "scored": 0,
        "results": [],
        "error": None,
        "cancelled": False,
        "finished": False,
    }

    with _jobs_lock:
        _jobs[job_id] = job

    thread = threading.Thread(target=_run_job, args=(job_id, questions), daemon=True)
    thread.start()
    return job_id


def get_eval_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        return _job_snapshot(job)


def stop_eval_job(job_id: str) -> bool:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job["status"] != "running":
            return False
        job["cancelled"] = True
        job["status"] = "stopped"
        return True


def get_eval_job_csv(job_id: str) -> Optional[str]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or not job.get("results"):
            return None
        return results_to_csv(job["results"])
