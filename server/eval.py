import csv
import io
import json
import re
from pathlib import Path
from typing import Optional

from retrieval.combined import combined_retrieval
from retrieval.exact_match import exact_match_retrieval, extract_slots
from retrieval.vector_search import vector_search_retrieval
from synthesis import synthesize_answer

EVAL_CSV_PATH = Path(__file__).resolve().parent.parent / "client" / "public" / "qa_pairs.csv"
FALLBACK_CSV_PATH = Path(__file__).resolve().parent.parent / "client" / "public" / "eval_questions.csv"

RETRIEVAL_HANDLERS = {
    "exact_match": exact_match_retrieval,
    "vector_search": vector_search_retrieval,
    "combined": combined_retrieval,
}


def parse_expected_slots(raw: str) -> Optional[dict]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def get_expected_access(row: dict) -> str:
    return (
        row.get("expected_access")
        or row.get("expected_answer")
        or row.get("answer")
        or ""
    ).strip()


def normalize_row(row: dict, index: int) -> dict:
    question = (row.get("question") or "").strip()
    return {
        "id": row.get("id") or str(index + 1),
        "type": (row.get("type") or "").strip(),
        "question": question,
        "expected_access": get_expected_access(row),
        "expected_slots": parse_expected_slots((row.get("expected_slots") or "").strip()),
        "citation": (row.get("citation") or "").strip(),
        "retrieval_method": (row.get("retrieval_method") or "exact_match").strip(),
    }


def load_eval_questions(csv_text: Optional[str] = None) -> list[dict]:
    if csv_text is not None:
        source = csv_text
    elif EVAL_CSV_PATH.exists():
        source = EVAL_CSV_PATH.read_text(encoding="utf-8")
    elif FALLBACK_CSV_PATH.exists():
        source = FALLBACK_CSV_PATH.read_text(encoding="utf-8")
    else:
        return []

    reader = csv.DictReader(io.StringIO(source))
    questions = []
    for index, row in enumerate(reader):
        normalized = normalize_row(row, index)
        if normalized["question"]:
            questions.append(normalized)
    return questions


def normalize_access_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text.lower()).strip()
    text = text.replace(";", " ")
    text = re.sub(r"\bwith\b", " ", text)
    text = re.sub(r"\band\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def access_parts(term: str) -> list[str]:
    parts = re.split(r"[;,]|\s+with\s+|\s+and\s+", term, flags=re.IGNORECASE)
    return [part.strip().lower() for part in parts if len(part.strip()) > 2]


def compare_access_label(expected_access: str, actual_norm: str) -> dict:
    parts = access_parts(expected_access)
    if not parts:
        normalized = normalize_access_text(expected_access)
        match = normalized in actual_norm
        return {
            "score": 1.0 if match else 0.0,
            "match": match,
            "matched_terms": [expected_access] if match else [],
        }

    matched = [part for part in parts if part in actual_norm]
    score = len(matched) / len(parts)
    return {
        "score": round(score, 2),
        "match": len(matched) == len(parts),
        "matched_terms": matched,
    }


def compare_answers(expected: str, actual: str) -> dict:
    expected_access = (expected or "").strip()
    actual_norm = normalize_access_text(actual)

    if not expected_access:
        return {"score": None, "match": None, "matched_terms": []}

    return compare_access_label(expected_access, actual_norm)


SLOT_FIELDS = (
    "regimen",
    "indication",
    "line",
    "book",
    "organization",
    "coverage",
    "population",
)


def normalize_slot_value(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text.lower()


def compare_slots(expected_slots: Optional[dict], actual_slots: Optional[dict]) -> dict:
    if not expected_slots:
        return {
            "score": None,
            "match": None,
            "matched_fields": [],
            "mismatched_fields": [],
        }

    matched_fields = []
    mismatched_fields = []
    checked_fields = []

    for field in SLOT_FIELDS:
        expected = normalize_slot_value(expected_slots.get(field))
        if expected is None:
            continue
        checked_fields.append(field)
        actual = normalize_slot_value((actual_slots or {}).get(field))
        if actual == expected:
            matched_fields.append(field)
        else:
            mismatched_fields.append(
                {
                    "field": field,
                    "expected": expected_slots.get(field),
                    "actual": (actual_slots or {}).get(field),
                }
            )

    if not checked_fields:
        return {
            "score": None,
            "match": None,
            "matched_fields": [],
            "mismatched_fields": [],
        }

    score = len(matched_fields) / len(checked_fields)
    return {
        "score": round(score, 2),
        "match": len(mismatched_fields) == 0,
        "matched_fields": matched_fields,
        "mismatched_fields": mismatched_fields,
    }


def compare_eval_result(item: dict, actual_answer: str, actual_slots: Optional[dict]) -> dict:
    access = compare_answers(get_expected_access(item), actual_answer)
    slots = compare_slots(item.get("expected_slots"), actual_slots)

    access_match = access.get("match")
    slots_match = slots.get("match")

    if access_match is None and slots_match is None:
        overall_match = None
    elif slots_match is None:
        overall_match = access_match
    elif access_match is None:
        overall_match = slots_match
    else:
        overall_match = access_match and slots_match

    return {
        "match": overall_match,
        "access": access,
        "slots": slots,
        "actual_slots": actual_slots,
    }


def run_eval_questions(questions: list[dict]) -> dict:
    results = []
    passed = 0
    scored = 0

    for item in questions:
        method = item.get("retrieval_method") or "exact_match"
        handler = RETRIEVAL_HANDLERS.get(method)
        if not handler:
            results.append(
                {
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
            )
            continue

        try:
            actual_slots = extract_slots(item["question"])
            if method == "exact_match":
                records = handler(item["question"], slots=actual_slots)
            else:
                records = handler(item["question"])
            actual_answer, citations = synthesize_answer(item["question"], records)
            comparison = compare_eval_result(item, actual_answer, actual_slots)
            if comparison["match"] is True:
                passed += 1
            if comparison["access"].get("score") is not None or comparison["slots"].get("score") is not None:
                scored += 1
            results.append(
                {
                    **item,
                    "actual_answer": actual_answer,
                    "citations": citations,
                    "records_retrieved": len(records),
                    "comparison": comparison,
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                {
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
            )

    return {
        "total": len(results),
        "passed": passed,
        "scored": scored,
        "pass_rate": round(passed / scored, 2) if scored else 0.0,
        "results": results,
    }
