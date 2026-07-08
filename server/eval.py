import csv
import io
import re
from pathlib import Path
from typing import Optional

from retrieval.combined import combined_retrieval
from retrieval.exact_match import exact_match_retrieval
from retrieval.vector_search import vector_search_retrieval
from synthesis import synthesize_answer

EVAL_CSV_PATH = Path(__file__).resolve().parent.parent / "client" / "public" / "qa_pairs.csv"
FALLBACK_CSV_PATH = Path(__file__).resolve().parent.parent / "client" / "public" / "eval_questions.csv"

RETRIEVAL_HANDLERS = {
    "exact_match": exact_match_retrieval,
    "vector_search": vector_search_retrieval,
    "combined": combined_retrieval,
}


def normalize_row(row: dict, index: int) -> dict:
    question = (row.get("question") or "").strip()
    expected = (row.get("expected_answer") or row.get("answer") or "").strip()
    return {
        "id": row.get("id") or str(index + 1),
        "type": (row.get("type") or "").strip(),
        "question": question,
        "expected_answer": expected,
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


def extract_comparison_terms(expected: str) -> list[str]:
    access_quotes = [
        match.strip()
        for match in re.findall(r"(?:is|has|have|are|was)\s+'([^']+)'", expected, flags=re.IGNORECASE)
    ]
    if access_quotes:
        return access_quotes

    quoted = [match.strip() for match in re.findall(r"'([A-Z][^']*)'", expected)]
    if quoted:
        return quoted

    return [term.strip() for term in re.split(r"[,;]", expected) if len(term.strip()) > 2]


def term_matches_actual(term: str, actual_norm: str) -> bool:
    parts = access_parts(term)
    if not parts:
        return normalize_access_text(term) in actual_norm
    return all(part in actual_norm for part in parts)


def compare_answers(expected: str, actual: str) -> dict:
    expected_norm = normalize_access_text(expected)
    actual_norm = normalize_access_text(actual)

    if not expected_norm:
        return {"score": None, "match": None, "matched_terms": []}

    quoted = extract_comparison_terms(expected)
    if quoted:
        matched = [term for term in quoted if term_matches_actual(term, actual_norm)]
        score = len(matched) / len(quoted)
        return {
            "score": round(score, 2),
            "match": len(matched) == len(quoted),
            "matched_terms": matched,
        }

    numbers = re.findall(r"\d+", expected)
    if numbers:
        matched = [number for number in numbers if number in actual]
        score = len(matched) / len(numbers)
        return {
            "score": round(score, 2),
            "match": score >= 0.5,
            "matched_terms": matched,
        }

    terms = extract_comparison_terms(expected)
    if terms:
        matched = [term for term in terms if term_matches_actual(term, actual_norm)]
        score = len(matched) / len(terms)
        return {
            "score": round(score, 2),
            "match": score >= 0.5,
            "matched_terms": matched,
        }

    return {"score": 0.0, "match": False, "matched_terms": []}


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
                    "comparison": {"score": None, "match": None, "matched_terms": []},
                    "error": f"Unknown retrieval method: {method}",
                }
            )
            continue

        try:
            records = handler(item["question"])
            actual_answer, citations = synthesize_answer(item["question"], records)
            comparison = compare_answers(item.get("expected_answer", ""), actual_answer)
            if comparison["match"] is True:
                passed += 1
            if comparison["score"] is not None:
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
                    "comparison": {"score": None, "match": False, "matched_terms": []},
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
