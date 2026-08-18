import csv
import io
import json
import threading
import time
import uuid
from typing import Any, Optional

from eval import (
    LINKER_EVAL_METHODS,
    compare_linker_eval_result,
)
from retrieval.exact_match import extract_and_link


_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _json_cell(value: Any) -> str:
    if value is None or value == "" or value == {} or value == []:
        return ""
    return json.dumps(value, ensure_ascii=False)


def results_to_csv(results: list[dict]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "type",
            "sub_type",
            "question",
            "expected_slots",
            "actual_slots",
            "actual_organization",
            "actual_regimen",
            "actual_indication",
            "actual_book",
            "cascade_stages",
            "cascade_statuses",
            "clarify_fired",
            "clarify_candidates",
            "wrong_canonicals",
            "match",
            "slots_match",
            "slots_score",
            "expect_clarify",
            "citation",
            "error",
            "link_info",
        ]
    )
    for row in results:
        comparison = row.get("comparison") or {}
        slots = comparison.get("slots") or {}
        writer.writerow(
            [
                row.get("id", ""),
                row.get("type", ""),
                row.get("sub_type", ""),
                row.get("question", ""),
                _json_cell(row.get("expected_slots")),
                _json_cell(comparison.get("actual_slots")),
                comparison.get("actual_organization")
                or (comparison.get("actual_slots") or {}).get("organization")
                or "",
                comparison.get("actual_regimen")
                or (comparison.get("actual_slots") or {}).get("regimen")
                or "",
                comparison.get("actual_indication")
                or (comparison.get("actual_slots") or {}).get("indication")
                or "",
                comparison.get("actual_book")
                or (comparison.get("actual_slots") or {}).get("book")
                or "",
                _json_cell(comparison.get("cascade_stages")),
                _json_cell(comparison.get("cascade_statuses")),
                comparison.get("clarify_needed"),
                _json_cell(comparison.get("clarify_candidates")),
                _json_cell(comparison.get("wrong_canonicals")),
                comparison.get("match"),
                slots.get("match"),
                slots.get("score"),
                comparison.get("expect_clarify"),
                row.get("citation", ""),
                row.get("error") or "",
                _json_cell(comparison.get("link_info")),
            ]
        )
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
    results: list[dict] = []
    passed = 0
    scored = 0

    try:
        for index, item in enumerate(questions):
            if job.get("cancelled"):
                job["status"] = "stopped"
                break

            method = item.get("retrieval_method") or "exact_match_entity_linker"
            if method not in LINKER_EVAL_METHODS:
                row = {
                    **item,
                    "actual_answer": "",
                    "records_retrieved": 0,
                    "comparison": {
                        "match": None,
                        "access": {"score": None, "match": None, "matched_terms": []},
                        "slots": {
                            "score": None,
                            "match": None,
                            "matched_fields": [],
                            "mismatched_fields": [],
                        },
                        "actual_slots": None,
                        "clarify_needed": None,
                        "link_info": {},
                    },
                    "error": f"Unknown retrieval method: {method}",
                }
            else:
                try:
                    linked, link_info = extract_and_link(item["question"])
                    comparison = compare_linker_eval_result(item, linked, link_info)
                    if comparison["match"] is True:
                        passed += 1
                    access = comparison.get("access") or {}
                    slots = comparison.get("slots") or {}
                    if (
                        access.get("score") is not None
                        or slots.get("score") is not None
                        or comparison.get("expect_clarify")
                    ):
                        scored += 1
                    row = {
                        **item,
                        "actual_answer": "",
                        "records_retrieved": 0,
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
                            "access": {
                                "score": None,
                                "match": False,
                                "matched_terms": [],
                            },
                            "slots": {
                                "score": None,
                                "match": False,
                                "matched_fields": [],
                                "mismatched_fields": [],
                            },
                            "actual_slots": None,
                            "clarify_needed": None,
                            "link_info": {},
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


def start_eval_job(
    questions: list[dict], default_retrieval_method: str = "exact_match_entity_linker"
) -> str:
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
