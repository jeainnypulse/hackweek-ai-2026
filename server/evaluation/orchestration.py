"""Orchestration eval: intent router + first-turn behavior (access / lives / joint / OOS)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Optional

from chatbot.retrieval.exact_match import exact_match_turn
from chatbot.retrieval.intent import classify_intent

EVAL_CSV_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "payer_latest_decision_paths.csv"
)
FALLBACK_CSV_PATH = EVAL_CSV_PATH
EVAL_LIVES_CSV_PATH = EVAL_CSV_PATH

VALID_INTENTS = frozenset({"access", "lives", "joint", "out_of_scope"})
VALID_BEHAVIORS = frozenset({"complete", "cascade", "clarify", "refuse", "empty"})


def is_orchestration_item(item: dict) -> bool:
    intent = str(item.get("expected_intent") or "").strip().lower()
    return intent in VALID_INTENTS


def _parse_json_cell(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list, int, float)):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def normalize_orchestration_row(row: dict, index: int) -> dict:
    expected_lives = row.get("expected_lives")
    if expected_lives in ("", None):
        expected_lives = None
    else:
        try:
            expected_lives = int(float(str(expected_lives).replace(",", "")))
        except (TypeError, ValueError):
            expected_lives = None

    slots = _parse_json_cell(row.get("expected_slots"))
    if not isinstance(slots, dict):
        slots = None

    return {
        "id": row.get("id") or str(index + 1),
        "type": (row.get("type") or "").strip(),
        "sub_type": (row.get("sub_type") or "").strip(),
        "question": (row.get("question") or "").strip(),
        "expected_intent": (row.get("expected_intent") or "").strip().lower(),
        "expected_behavior": (row.get("expected_behavior") or "").strip().lower(),
        "expected_slots": slots,
        "expected_lives": expected_lives,
        "citation": (row.get("citation") or row.get("eval_notes") or "").strip(),
        "retrieval_method": (
            row.get("retrieval_method") or "exact_match_entity_linker"
        ).strip(),
    }


def load_orchestration_questions(csv_text: Optional[str] = None) -> list[dict]:
    if csv_text is not None:
        source = csv_text
    elif EVAL_CSV_PATH.exists():
        source = EVAL_CSV_PATH.read_text(encoding="utf-8")
    elif FALLBACK_CSV_PATH.exists():
        source = FALLBACK_CSV_PATH.read_text(encoding="utf-8")
    else:
        return []

    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if (
        len(lines) >= 2
        and "expected_intent" not in lines[0]
        and "expected_intent" in lines[1]
    ):
        source = "\n".join(lines[1:])

    reader = csv.DictReader(io.StringIO(source))
    questions = []
    for index, row in enumerate(reader):
        item = normalize_orchestration_row(row, index)
        if item["question"] and item["expected_intent"] in VALID_INTENTS:
            questions.append(item)
    return questions


def load_lives_questions(csv_text: Optional[str] = None) -> list[dict]:
    """Load lives-only eval rows (simple lookup + coverage/payer clarify)."""
    if csv_text is not None:
        questions = load_orchestration_questions(csv_text)
    elif EVAL_LIVES_CSV_PATH.exists():
        questions = load_orchestration_questions(
            EVAL_LIVES_CSV_PATH.read_text(encoding="utf-8")
        )
    else:
        return []
    return [q for q in questions if q.get("expected_intent") == "lives"]


def load_joint_questions(csv_text: str) -> list[dict]:
    """Load joint eval rows plus OOS negatives from caller-provided CSV text."""
    questions = load_orchestration_questions(csv_text)
    return [
        q
        for q in questions
        if q.get("expected_intent") in ("joint", "out_of_scope")
    ]


def observed_behavior(turn: dict) -> str:
    intent = (turn.get("intent") or "").strip().lower()
    status = (turn.get("status") or "").strip()
    if intent == "out_of_scope" or status == "out_of_scope":
        return "refuse"
    if status == "needs_input":
        return "clarify"
    if status == "needs_cascade_input":
        return "cascade"
    if status == "complete":
        if turn.get("records"):
            return "complete"
        if intent == "out_of_scope":
            return "refuse"
        return "empty"
    if status == "error":
        return "error"
    return status or "unknown"


def _record_lives(records: list) -> Optional[int]:
    for record in records or []:
        if record.get("lives") is None:
            continue
        try:
            return int(record["lives"])
        except (TypeError, ValueError):
            continue
    return None


_SLOT_KEY_ALIASES = {"territory": "territoryName"}


def _slot_value(value: Any) -> Optional[str]:
    if value is None or value == "" or str(value).strip().lower() == "null":
        return None
    return str(value).strip()


def compare_expected_slots(
    expected: Optional[dict], actual: Optional[dict]
) -> tuple[Optional[bool], list[dict]]:
    """Compare expected slot keys only. Missing expected_slots is not scored."""
    if not isinstance(expected, dict) or not expected:
        return None, []
    actual = actual or {}
    mismatches = []
    for key, raw in expected.items():
        exp = _slot_value(raw)
        if exp is None:
            continue
        actual_key = _SLOT_KEY_ALIASES.get(key, key)
        act = _slot_value(actual.get(actual_key))
        if act is None and actual_key != key:
            act = _slot_value(actual.get(key))
        if act is None or act.casefold() != exp.casefold():
            mismatches.append(
                {"field": actual_key, "expected": exp, "actual": act}
            )
    return (len(mismatches) == 0), mismatches


def compare_orchestration_result(item: dict, turn: dict) -> dict:
    expected_intent = (item.get("expected_intent") or "").strip().lower()
    actual_intent = (turn.get("intent") or "").strip().lower()
    observed = observed_behavior(turn)
    expected_behavior = (item.get("expected_behavior") or "").strip().lower()

    intent_match = actual_intent == expected_intent if expected_intent else None

    if expected_behavior:
        behavior_match = observed == expected_behavior
    elif expected_intent == "out_of_scope":
        behavior_match = observed == "refuse"
    elif expected_intent in ("access", "lives", "joint"):
        # In-scope questions must not be refused as OOS. Clarify/cascade/complete/empty
        # are all acceptable unless expected_behavior is set.
        behavior_match = observed != "refuse"
    else:
        behavior_match = None

    expected_lives = item.get("expected_lives")
    actual_lives = _record_lives(turn.get("records") or [])
    lives_match: Optional[bool] = None
    if expected_lives is not None and expected_intent in ("lives", "joint"):
        lives_match = actual_lives == expected_lives

    actual_slots = turn.get("confirmed_slots") or {}
    slots_match: Optional[bool] = None
    mismatched_fields: list[dict] = []
    if expected_intent in ("access", "lives", "joint"):
        slots_match, mismatched_fields = compare_expected_slots(
            item.get("expected_slots"), actual_slots
        )

    match = all(
        flag is not False
        for flag in (intent_match, behavior_match, lives_match, slots_match)
    )

    return {
        "match": match,
        "intent_match": intent_match,
        "behavior_match": behavior_match,
        "lives_match": lives_match,
        "slots_match": slots_match,
        "mismatched_fields": mismatched_fields,
        "expected_intent": expected_intent,
        "actual_intent": actual_intent,
        "expected_behavior": expected_behavior or None,
        "observed_behavior": observed,
        "expected_lives": expected_lives,
        "actual_lives": actual_lives,
        "actual_slots": actual_slots,
        "status": turn.get("status"),
        "records_retrieved": len(turn.get("records") or []),
        "message": turn.get("message"),
    }


def evaluate_orchestration_item(item: dict, *, classify_only: bool = False) -> dict:
    """Run one question through classify, or the full first turn."""
    question = item["question"]
    if classify_only:
        intent = classify_intent(question)
        turn = {
            "intent": intent,
            "status": "complete" if intent == "out_of_scope" else "classified",
            "records": [],
            "confirmed_slots": {},
            "message": None,
        }
        comparison = compare_orchestration_result(item, turn)
        comparison["lives_match"] = None
        comparison["slots_match"] = None
        comparison["mismatched_fields"] = []
        comparison["match"] = comparison["intent_match"] is not False
        return {
            **item,
            "actual_answer": "",
            "records_retrieved": 0,
            "comparison": comparison,
            "error": None,
        }

    turn = exact_match_turn(question=question)
    comparison = compare_orchestration_result(item, turn)
    answer = turn.get("message") or ""
    return {
        **item,
        "actual_answer": answer,
        "records_retrieved": len(turn.get("records") or []),
        "comparison": comparison,
        "error": None,
    }


def run_orchestration_questions(
    questions: list[dict],
    *,
    classify_only: bool = False,
    on_row=None,
) -> dict:
    results = []
    passed = 0
    scored = 0
    for item in questions:
        try:
            row = evaluate_orchestration_item(item, classify_only=classify_only)
        except Exception as exc:
            row = {
                **item,
                "actual_answer": "",
                "records_retrieved": 0,
                "comparison": {
                    "match": False,
                    "intent_match": False,
                    "behavior_match": False,
                    "lives_match": None,
                    "slots_match": None,
                    "mismatched_fields": [],
                    "expected_intent": item.get("expected_intent"),
                    "actual_intent": None,
                    "expected_behavior": item.get("expected_behavior") or None,
                    "observed_behavior": "error",
                    "expected_lives": item.get("expected_lives"),
                    "actual_lives": None,
                    "actual_slots": {},
                    "status": "error",
                    "records_retrieved": 0,
                    "message": str(exc),
                },
                "error": str(exc),
            }
        results.append(row)
        comparison = row.get("comparison") or {}
        if comparison.get("expected_intent"):
            scored += 1
            if comparison.get("match") is True:
                passed += 1
        if on_row:
            on_row(len(results), len(questions), row)
    return {
        "total": len(results),
        "scored": scored,
        "passed": passed,
        "pass_rate": round(passed / scored, 2) if scored else 0.0,
        "results": results,
    }


def orchestration_results_to_csv(results: list[dict]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "type",
            "sub_type",
            "question",
            "expected_intent",
            "actual_intent",
            "intent_match",
            "expected_behavior",
            "observed_behavior",
            "behavior_match",
            "expected_lives",
            "actual_lives",
            "lives_match",
            "slots_match",
            "match",
            "status",
            "records_retrieved",
            "actual_slots",
            "actual_answer",
            "citation",
            "error",
        ]
    )
    for row in results:
        comparison = row.get("comparison") or {}
        writer.writerow(
            [
                row.get("id", ""),
                row.get("type", ""),
                row.get("sub_type", ""),
                row.get("question", ""),
                comparison.get("expected_intent") or row.get("expected_intent") or "",
                comparison.get("actual_intent") or "",
                comparison.get("intent_match"),
                comparison.get("expected_behavior")
                or row.get("expected_behavior")
                or "",
                comparison.get("observed_behavior") or "",
                comparison.get("behavior_match"),
                (
                    comparison.get("expected_lives")
                    if comparison.get("expected_lives") is not None
                    else row.get("expected_lives") or ""
                ),
                (
                    comparison.get("actual_lives")
                    if comparison.get("actual_lives") is not None
                    else ""
                ),
                comparison.get("lives_match"),
                comparison.get("slots_match"),
                comparison.get("match"),
                comparison.get("status") or "",
                row.get("records_retrieved", 0),
                json.dumps(comparison.get("actual_slots") or {}, ensure_ascii=False),
                row.get("actual_answer") or comparison.get("message") or "",
                row.get("citation", ""),
                row.get("error") or "",
            ]
        )
    return output.getvalue()
