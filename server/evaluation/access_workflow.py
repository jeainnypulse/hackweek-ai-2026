"""Stage-level evaluation for access questions and OOS negative controls."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Optional

from chatbot.answer_retrieval import handle_conversation_turn
from chatbot.conversation.slot_plan import ACCESS_SLOTS
from chatbot.conversation.state import _sessions
from evaluation.orchestration import observed_behavior


def _slot_value(value: Any) -> Optional[str]:
    if value is None or value == "" or str(value).strip().lower() == "null":
        return None
    return str(value).strip()


def _clean_slots(slots: Optional[dict]) -> dict[str, str]:
    cleaned = {}
    for field in ACCESS_SLOTS:
        value = _slot_value((slots or {}).get(field))
        if value is not None:
            cleaned[field] = value
    return cleaned


def _compare_maps(
    expected: Optional[dict],
    actual: Optional[dict],
    *,
    exact: bool,
) -> tuple[Optional[bool], list[dict]]:
    if expected is None:
        return None, []
    exp = _clean_slots(expected)
    act = _clean_slots(actual)
    fields = set(exp)
    if exact:
        fields |= set(act)
    mismatches = []
    for field in sorted(fields):
        expected_value = exp.get(field)
        actual_value = act.get(field)
        if (
            expected_value is None
            or actual_value is None
            or expected_value.casefold() != actual_value.casefold()
        ):
            mismatches.append(
                {
                    "field": field,
                    "expected": expected_value,
                    "actual": actual_value,
                }
            )
    return not mismatches, mismatches


def _candidate_match(
    expected: Optional[list],
    actual: Optional[list],
) -> Optional[bool]:
    if not expected:
        return None
    actual_values = {
        str(value).strip().casefold()
        for value in (actual or [])
        if _slot_value(value) is not None
    }
    return all(
        str(value).strip().casefold() in actual_values for value in expected
    )


def _record_fields_match(expected: Optional[dict], records: list) -> Optional[bool]:
    if not expected:
        return None
    if not records:
        return False
    for record in records:
        for field, expected_value in expected.items():
            actual_value: Any = record
            for part in field.split("."):
                if not isinstance(actual_value, dict):
                    actual_value = None
                    break
                actual_value = actual_value.get(part)
            if isinstance(expected_value, str):
                if _slot_value(actual_value) is None:
                    return False
                if str(actual_value).strip().casefold() != expected_value.strip().casefold():
                    return False
            elif actual_value != expected_value:
                return False
    return True


def _turn_trace(turn: dict, *, action: str, value: Any = None) -> dict:
    return {
        "action": action,
        "value": value,
        "status": turn.get("status"),
        "intent": turn.get("intent"),
        "current_slot": turn.get("current_slot"),
        "candidates": list(turn.get("candidates") or []),
        "confirmed_slots": dict(turn.get("confirmed_slots") or {}),
        "records_retrieved": len(turn.get("records") or []),
        "message": turn.get("message"),
    }


def evaluate_access_item(item: dict) -> dict:
    """Evaluate intent/extraction/linking plus lightweight deterministic flow."""
    first_turn = handle_conversation_turn(question=item["question"])
    session_id = first_turn.get("session_id")
    session = _sessions.get(session_id) or {}
    actual_extracted = _clean_slots(session.get("slots"))
    first_resolved = _clean_slots(first_turn.get("confirmed_slots"))
    actual_link_info = dict(session.get("link_info") or first_turn.get("link_info") or {})
    expected_intent = item.get("expected_intent") or ""
    actual_intent = first_turn.get("intent") or ""
    intent_match = actual_intent == expected_intent

    extraction_match, extraction_mismatches = _compare_maps(
        item.get("expected_extracted_slots"),
        actual_extracted,
        exact=True,
    )
    resolved_expected = item.get("expected_resolved_slots")
    if resolved_expected is None:
        resolved_expected = item.get("expected_slots")
    linking_match, linking_mismatches = _compare_maps(
        resolved_expected,
        first_resolved,
        exact=False,
    )

    expected_behavior = item.get("expected_behavior") or ""
    first_behavior = observed_behavior(first_turn)
    behavior_match = (
        first_behavior == expected_behavior if expected_behavior else None
    )
    expected_current_slot = item.get("expected_current_slot") or ""
    current_slot_match = (
        first_turn.get("current_slot") == expected_current_slot
        if expected_current_slot
        else None
    )
    candidates_match = _candidate_match(
        item.get("expected_candidates"),
        first_turn.get("candidates"),
    )

    trace = [_turn_trace(first_turn, action="ask")]
    steps = item.get("clarification_steps") or []
    current_turn = first_turn
    progression_match: Optional[bool] = None
    progression_errors = []
    premature_retrieval_match = True

    try:
        if first_behavior == "clarify" and first_turn.get("records"):
            premature_retrieval_match = False
        if steps:
            progression_match = True
            for index, step in enumerate(steps):
                expected_slot = str((step or {}).get("slot") or "").strip()
                value = (step or {}).get("value")
                if current_turn.get("status") != "needs_input":
                    progression_match = False
                    progression_errors.append(
                        f"step {index + 1}: expected clarification, "
                        f"got {current_turn.get('status')!r}"
                    )
                    break
                if expected_slot and current_turn.get("current_slot") != expected_slot:
                    progression_match = False
                    progression_errors.append(
                        f"step {index + 1}: expected slot {expected_slot!r}, "
                        f"got {current_turn.get('current_slot')!r}"
                    )
                    break
                current_turn = handle_conversation_turn(
                    session_id=current_turn.get("session_id") or session_id,
                    action="provide",
                    value=value,
                )
                trace.append(
                    _turn_trace(current_turn, action="provide", value=value)
                )
                if current_turn.get("status") == "error":
                    progression_match = False
                    progression_errors.append(
                        f"step {index + 1}: continuation returned an error"
                    )
                    break
                if index < len(steps) - 1 and current_turn.get("records"):
                    premature_retrieval_match = False

            if progression_match:
                final_slots = _clean_slots(current_turn.get("confirmed_slots"))
                for step in steps:
                    field = str((step or {}).get("slot") or "").strip()
                    expected_value = _slot_value((step or {}).get("value"))
                    actual_value = _slot_value(final_slots.get(field))
                    if (
                        field
                        and expected_value is not None
                        and (
                            actual_value is None
                            or actual_value.casefold() != expected_value.casefold()
                        )
                    ):
                        progression_match = False
                        progression_errors.append(
                            f"provided {field!r} was not retained canonically"
                        )

        final_turn = current_turn
        final_records = list(final_turn.get("records") or [])
        expected_record_count = item.get("expected_record_count")
        record_count_match = (
            len(final_records) == expected_record_count
            if expected_record_count is not None
            else None
        )
        record_fields_match = _record_fields_match(
            item.get("expected_record_fields"),
            final_records,
        )
        expected_final_behavior = None
        if steps and expected_record_count is not None:
            expected_final_behavior = (
                "complete" if expected_record_count > 0 else "empty"
            )
        elif steps and item.get("expected_record_fields"):
            expected_final_behavior = "complete"
        final_behavior = observed_behavior(final_turn)
        final_behavior_match = (
            final_behavior == expected_final_behavior
            if expected_final_behavior
            else None
        )
        retrieval_checks = [
            check
            for check in (record_count_match, record_fields_match)
            if check is not None
        ]
        retrieval_match = all(retrieval_checks) if retrieval_checks else None
        scored = [
            intent_match,
            linking_match,
            behavior_match,
            current_slot_match,
            candidates_match,
            progression_match,
            final_behavior_match,
            retrieval_match,
            premature_retrieval_match,
        ]
        match = all(check is not False for check in scored)

        return {
            **item,
            "actual_intent": actual_intent,
            "actual_extracted_slots": actual_extracted,
            "actual_resolved_slots": first_resolved,
            "actual_link_info": actual_link_info,
            "first_behavior": first_behavior,
            "expected_final_behavior": expected_final_behavior,
            "final_behavior": final_behavior,
            "actual_current_slot": first_turn.get("current_slot"),
            "actual_candidates": list(first_turn.get("candidates") or []),
            "actual_record_count": len(final_records),
            "actual_records": final_records,
            "actual_answer": final_turn.get("message") or "",
            "turn_trace": trace,
            "comparison": {
                "match": match,
                "expected_intent": expected_intent,
                "actual_intent": actual_intent,
                "intent_match": intent_match,
                "expected_behavior": expected_behavior or None,
                "observed_behavior": first_behavior,
                "extraction_match": extraction_match,
                "linking_match": linking_match,
                "behavior_match": behavior_match,
                "current_slot_match": current_slot_match,
                "candidates_match": candidates_match,
                "progression_match": progression_match,
                "final_behavior_match": final_behavior_match,
                "retrieval_match": retrieval_match,
                "record_count_match": record_count_match,
                "record_fields_match": record_fields_match,
                "premature_retrieval_match": premature_retrieval_match,
                "extraction_mismatches": extraction_mismatches,
                "linking_mismatches": linking_mismatches,
                "progression_errors": progression_errors,
            },
            "error": None,
        }
    finally:
        if session_id:
            _sessions.pop(session_id, None)


def _error_row(item: dict, exc: Exception) -> dict:
    return {
        **item,
        "actual_intent": None,
        "actual_extracted_slots": {},
        "actual_resolved_slots": {},
        "actual_link_info": {},
        "first_behavior": "error",
        "expected_final_behavior": None,
        "final_behavior": "error",
        "actual_current_slot": None,
        "actual_candidates": [],
        "actual_record_count": 0,
        "actual_records": [],
        "actual_answer": "",
        "turn_trace": [],
        "comparison": {
            "match": False,
            "expected_intent": item.get("expected_intent"),
            "actual_intent": None,
            "intent_match": False,
            "expected_behavior": item.get("expected_behavior") or None,
            "observed_behavior": "error",
            "extraction_match": False,
            "linking_match": False,
            "behavior_match": False,
            "current_slot_match": False,
            "candidates_match": False,
            "progression_match": False,
            "final_behavior_match": False,
            "retrieval_match": False,
            "record_count_match": False,
            "record_fields_match": False,
            "premature_retrieval_match": False,
            "extraction_mismatches": [],
            "linking_mismatches": [],
            "progression_errors": [],
        },
        "error": str(exc),
    }


def run_access_questions(questions: list[dict], *, on_row=None) -> dict:
    results = []
    passed = 0
    extraction_scored = 0
    extraction_passed = 0
    for item in questions:
        try:
            row = evaluate_access_item(item)
        except Exception as exc:
            row = _error_row(item, exc)
        results.append(row)
        if row["comparison"]["match"] is True:
            passed += 1
        extraction_match = row["comparison"].get("extraction_match")
        if extraction_match is not None:
            extraction_scored += 1
            if extraction_match is True:
                extraction_passed += 1
        if on_row:
            on_row(len(results), len(questions), row)
    scored = len(results)
    return {
        "total": len(results),
        "scored": scored,
        "passed": passed,
        "pass_rate": round(passed / scored, 2) if scored else 0.0,
        "extraction_scored": extraction_scored,
        "extraction_passed": extraction_passed,
        "extraction_rate": (
            round(extraction_passed / extraction_scored, 2)
            if extraction_scored
            else 0.0
        ),
        "results": results,
    }


def access_results_to_csv(results: list[dict]) -> str:
    output = io.StringIO()
    fields = [
        "id",
        "type",
        "sub_type",
        "question",
        "expected_intent",
        "actual_intent",
        "intent_match",
        "expected_extracted_slots",
        "actual_extracted_slots",
        "extraction_match",
        "extraction_mismatches",
        "expected_resolved_slots",
        "actual_resolved_slots",
        "linking_match",
        "linking_mismatches",
        "actual_link_info",
        "expected_behavior",
        "first_behavior",
        "expected_final_behavior",
        "final_behavior",
        "final_behavior_match",
        "behavior_match",
        "expected_current_slot",
        "actual_current_slot",
        "current_slot_match",
        "expected_candidates",
        "actual_candidates",
        "candidates_match",
        "clarification_steps",
        "progression_match",
        "progression_errors",
        "premature_retrieval_match",
        "expected_record_count",
        "actual_record_count",
        "record_count_match",
        "expected_record_fields",
        "record_fields_match",
        "retrieval_match",
        "match",
        "actual_answer",
        "turn_trace",
        "eval_notes",
        "error",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    json_fields = {
        "expected_extracted_slots",
        "actual_extracted_slots",
        "extraction_mismatches",
        "expected_resolved_slots",
        "actual_resolved_slots",
        "linking_mismatches",
        "actual_link_info",
        "expected_candidates",
        "actual_candidates",
        "clarification_steps",
        "progression_errors",
        "expected_record_fields",
        "turn_trace",
    }
    for row in results:
        comparison = row.get("comparison") or {}
        values = {
            **{field: row.get(field, "") for field in fields},
            **{field: comparison.get(field) for field in comparison if field in fields},
            "eval_notes": row.get("citation") or row.get("eval_notes") or "",
        }
        if row.get("expected_resolved_slots") is None:
            values["expected_resolved_slots"] = row.get("expected_slots")
        for field in json_fields:
            value = values.get(field)
            values[field] = (
                json.dumps(value, ensure_ascii=False)
                if value not in (None, "")
                else ""
            )
        writer.writerow(values)
    return output.getvalue()
