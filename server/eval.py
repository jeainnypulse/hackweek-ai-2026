import csv
import io
import json
import re
from pathlib import Path
from typing import Optional

from entity_linking import LINK_DIMENSIONS
from retrieval.exact_match import clarify_needed, extract_and_link

EVAL_CSV_PATH = Path(__file__).resolve().parent / "data" / "pulse_eval_orca_tregzi.csv"
FALLBACK_CSV_PATH = (
    Path(__file__).resolve().parent / "data" / "pulse_eval_orca_tregzi.csv"
)

LINKER_EVAL_METHODS = frozenset(
    {
        "exact_match_entity_linker",
        "exact_match",
    }
)

# All supplied treatment-plan fields are now linker-scoped and scored.
IDENTITY_SLOT_FIELDS = tuple(LINK_DIMENSIONS)

# CSV type values that expect the linker to stop for clarification
# (pass if clarify_needed=True; do not require full org/slot equality)
EXPECT_CLARIFY_TYPES = frozenset(
    {"clarify_needed", "under_specific", "clarify_should_trigger"}
)


def expects_clarify(item: dict) -> bool:
    return (item.get("type") or "").strip().lower() in EXPECT_CLARIFY_TYPES


def cascade_stage_label(method: Optional[str], status: Optional[str] = None) -> str:
    """Normalize linker method/status into a short cascade stage name."""
    raw = (method or "").strip().lower()
    if raw.startswith("cascade:"):
        raw = raw[len("cascade:") :]
    if raw in ("synonym",) or raw.startswith("synonym"):
        return "synonym"
    if raw in ("catalog",) or raw.startswith("catalog"):
        return "catalog"
    if "hypothesize" in raw or raw in ("hyp+verify", "hyp_verify"):
        return "hyp+verify"
    if raw in ("clarify",) or raw.startswith("clarify"):
        return "clarify"
    if status in ("ambiguous",) and not raw:
        return "clarify"
    if status in ("unknown",) or raw in ("", "cascade", "unknown"):
        return "unknown"
    return raw or "unknown"


def summarize_link_diagnostics(
    linked_slots: Optional[dict],
    link_info: Optional[dict],
    *,
    expected_slots: Optional[dict] = None,
    mismatched_fields: Optional[list] = None,
) -> dict:
    """Build per-row diagnostics for eval CSV export."""
    info = link_info or {}
    stages: dict[str, str] = {}
    statuses: dict[str, str] = {}
    candidates_by_dim: dict[str, list] = {}
    for dimension in IDENTITY_SLOT_FIELDS:
        dim_info = info.get(dimension) or {}
        if not dim_info and not (linked_slots or {}).get(dimension):
            continue
        status = dim_info.get("status")
        method = dim_info.get("method")
        stages[dimension] = cascade_stage_label(method, status)
        if status:
            statuses[dimension] = status
        cands = dim_info.get("candidates") or []
        if cands:
            # Cap huge catalog dumps for CSV readability
            candidates_by_dim[dimension] = list(cands)[:40]

    wrong: list[dict] = []
    for item in mismatched_fields or []:
        wrong.append(
            {
                "field": item.get("field"),
                "expected": item.get("expected"),
                "actual": item.get("actual"),
                "stage": stages.get(item.get("field") or ""),
                "raw": (info.get(item.get("field") or "") or {}).get("raw"),
            }
        )

    # Also surface hyp+verify matches that are simply wrong vs gold even if
    # compare_slots already listed them — keep one clear list for analysts.
    if expected_slots and not wrong:
        for field in IDENTITY_SLOT_FIELDS:
            exp = normalize_slot_value((expected_slots or {}).get(field))
            act = normalize_slot_value((linked_slots or {}).get(field))
            if exp is not None and act is not None and exp != act:
                wrong.append(
                    {
                        "field": field,
                        "expected": expected_slots.get(field),
                        "actual": (linked_slots or {}).get(field),
                        "stage": stages.get(field),
                        "raw": (info.get(field) or {}).get("raw"),
                    }
                )

    return {
        "cascade_stages": stages,
        "cascade_statuses": statuses,
        "clarify_candidates": candidates_by_dim,
        "wrong_canonicals": wrong,
        "actual_organization": (linked_slots or {}).get("organization"),
        "actual_regimen": (linked_slots or {}).get("regimen"),
        "actual_indication": (linked_slots or {}).get("indication"),
        "actual_book": (linked_slots or {}).get("book"),
    }

SLOT_FIELDS = (
    "regimen",
    "indication",
    "line",
    "book",
    "organization",
    "coverage",
    "population",
)


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
        "sub_type": (row.get("sub_type") or "").strip(),
        "question": question,
        "expected_access": get_expected_access(row),
        "expected_slots": parse_expected_slots(
            (row.get("expected_slots") or "").strip()
        ),
        "citation": (row.get("citation") or row.get("eval_notes") or "").strip(),
        "retrieval_method": (
            row.get("retrieval_method") or "exact_match_entity_linker"
        ).strip(),
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


def normalize_slot_value(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text.lower()


def compare_slots(
    expected_slots: Optional[dict],
    actual_slots: Optional[dict],
    *,
    fields: tuple[str, ...] = SLOT_FIELDS,
) -> dict:
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

    for field in fields:
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


def compare_records_to_expected_slots(
    records: Optional[list], expected_slots: Optional[dict]
) -> dict:
    """
    Strict key check: every retrieved record must match all non-null gold slots.
    If gold has slots but nothing was retrieved, that is a failure.
    """
    if not expected_slots:
        return {
            "score": None,
            "match": None,
            "checked_fields": [],
            "mismatched_records": [],
        }

    checked_fields = [
        field
        for field in SLOT_FIELDS
        if normalize_slot_value(expected_slots.get(field)) is not None
    ]
    if not checked_fields:
        return {
            "score": None,
            "match": None,
            "checked_fields": [],
            "mismatched_records": [],
        }

    if not records:
        return {
            "score": 0.0,
            "match": False,
            "checked_fields": checked_fields,
            "mismatched_records": [
                {"index": None, "reason": "no_records_retrieved"}
            ],
        }

    mismatched_records = []
    for index, record in enumerate(records):
        field_mismatches = []
        for field in checked_fields:
            expected = normalize_slot_value(expected_slots.get(field))
            actual = normalize_slot_value(record.get(field))
            if actual != expected:
                field_mismatches.append(
                    {
                        "field": field,
                        "expected": expected_slots.get(field),
                        "actual": record.get(field),
                    }
                )
        if field_mismatches:
            mismatched_records.append(
                {"index": index, "mismatches": field_mismatches}
            )

    matched = len(records) - len(mismatched_records)
    score = matched / len(records)
    return {
        "score": round(score, 2),
        "match": len(mismatched_records) == 0,
        "checked_fields": checked_fields,
        "mismatched_records": mismatched_records,
    }


def _skipped_access() -> dict:
    return {"score": None, "match": None, "matched_terms": [], "skipped": True}


def _skipped_records() -> dict:
    return {
        "score": None,
        "match": None,
        "checked_fields": [],
        "mismatched_records": [],
        "skipped": True,
    }


def compare_linker_eval_result(
    item: dict,
    linked_slots: Optional[dict],
    link_info: Optional[dict] = None,
) -> dict:
    """
    Linker-scoped scoring: identity linked slots vs gold only.
    Does not score coverage/population/line, retrieve, or access.

    For types designed to need clarification (clarify_needed, under_specific),
    overall match is whether the linker flagged clarify_needed — not whether
    unresolved slots equal the gold canonicals.
    """
    slots = compare_slots(
        item.get("expected_slots"),
        linked_slots,
        fields=IDENTITY_SLOT_FIELDS,
    )
    needs_clarify = clarify_needed(link_info)
    expect_clarify = expects_clarify(item)
    diagnostics = summarize_link_diagnostics(
        linked_slots,
        link_info,
        expected_slots=item.get("expected_slots"),
        mismatched_fields=slots.get("mismatched_fields") or [],
    )
    if expect_clarify:
        overall_match = needs_clarify is True
        strict = "linker_clarify"
    else:
        overall_match = slots.get("match")
        strict = "linker"
    return {
        "match": overall_match,
        "access": _skipped_access(),
        "slots": slots,
        "records": _skipped_records(),
        "actual_slots": linked_slots,
        "link_info": link_info or {},
        "clarify_needed": needs_clarify,
        "expect_clarify": expect_clarify,
        "strict": strict,
        **diagnostics,
    }


def compare_eval_result(
    item: dict,
    actual_answer: str,
    actual_slots: Optional[dict],
    records: Optional[list] = None,
    *,
    strict: bool = False,
) -> dict:
    """
    Compare answer + slots. When strict=True (legacy full E2E):
    - final linked slots must match every non-null expected slot
    - every retrieved record must match those expected slot keys
    - expected access (if present) must appear in the answer
    """
    access = compare_answers(get_expected_access(item), actual_answer)
    slots = compare_slots(item.get("expected_slots"), actual_slots)
    records_cmp = compare_records_to_expected_slots(
        records, item.get("expected_slots")
    )

    access_match = access.get("match")
    slots_match = slots.get("match")
    records_match = records_cmp.get("match")

    if strict:
        parts = []
        if slots_match is not None:
            parts.append(slots_match)
        if records_match is not None:
            parts.append(records_match)
        if access_match is not None:
            parts.append(access_match)
        overall_match = all(parts) if parts else None
    elif access_match is None and slots_match is None:
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
        "records": records_cmp,
        "actual_slots": actual_slots,
        "clarify_needed": None,
        "link_info": {},
        "strict": strict,
    }


def run_eval_questions(questions: list[dict]) -> dict:
    results = []
    passed = 0
    scored = 0

    for item in questions:
        method = item.get("retrieval_method") or "exact_match_entity_linker"
        if method not in LINKER_EVAL_METHODS:
            results.append(
                {
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
            )
            continue

        try:
            linked, link_info = extract_and_link(item["question"])
            comparison = compare_linker_eval_result(item, linked, link_info)
            actual_answer = ""
            citations = []
            records_retrieved = 0

            if comparison["match"] is True:
                passed += 1
            if (
                comparison["slots"].get("score") is not None
                or (comparison.get("access") or {}).get("score") is not None
                or comparison.get("expect_clarify")
            ):
                scored += 1
            results.append(
                {
                    **item,
                    "actual_answer": actual_answer,
                    "citations": citations,
                    "records_retrieved": records_retrieved,
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
            )

    return {
        "total": len(results),
        "passed": passed,
        "scored": scored,
        "pass_rate": round(passed / scored, 2) if scored else 0.0,
        "results": results,
    }
