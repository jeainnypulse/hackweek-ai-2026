from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from chatbot.conversation.state import _sessions
from evaluation.access_workflow import (
    access_results_to_csv,
    evaluate_access_item,
    run_access_questions,
)
from evaluation.orchestration import (
    compare_orchestration_result,
    load_access_questions,
    normalize_orchestration_row,
)
from evaluation.generate_decision_path_eval import build_rows
from evaluation.run_orchestration_eval import ACCESS_RESULTS_DIR, _auto_results_path


def _access(
    organization: str,
    slug: str,
    book: str,
    coverage: str = "Medical",
) -> dict:
    return {
        "organization": organization,
        "slug": slug,
        "regimen": "Tregzi",
        "indication": "Hematologic malignancies",
        "book": book,
        "coverage": coverage,
        "population": "No Subtype Specified",
        "line": "1L+",
    }


def _lives(slug: str, book: str, coverage: str, lives: int) -> dict:
    return {
        "slug": slug,
        "book": book,
        "coverage": coverage,
        "territoryType": "National",
        "territoryName": "United States",
        "source": "DRG",
        "lives": lives,
    }


class DecisionPathEvalTests(unittest.TestCase):
    def setUp(self):
        self.access_records = [
            _access("Aetna", "aetna", "Commercial"),
            _access("Aetna", "aetna", "Medicare"),
            _access("Centene", "centene", "Managed Medicaid"),
        ]
        self.lives_records = [
            _lives("aetna", "Commercial", "Medical", 100),
            _lives("aetna", "Commercial", "Pharmacy", 80),
            _lives("aetna", "Medicare", "Medical", 60),
            _lives("centene", "Managed Medicaid", "Medical", 40),
        ]

    def test_generator_covers_every_observable_behavior(self):
        rows = build_rows(self.access_records, self.lives_records)

        self.assertGreaterEqual(len(rows), 100)
        self.assertEqual(len({row["question"] for row in rows}), len(rows))
        self.assertEqual(
            {row["expected_behavior"] for row in rows},
            {"complete", "clarify", "empty", "refuse"},
        )
        self.assertEqual(
            {row["expected_intent"] for row in rows},
            {"access", "lives", "joint", "out_of_scope"},
        )
        self.assertIn(
            "data_clarify_coverage",
            {row["sub_type"] for row in rows},
        )
        self.assertIn(
            "data_refuse_unsupported_geography",
            {row["sub_type"] for row in rows},
        )
        subtype_counts = {}
        for row in rows:
            key = (row["type"], row["sub_type"])
            subtype_counts[key] = subtype_counts.get(key, 0) + 1
        self.assertTrue(all(count >= 10 for count in subtype_counts.values()))

    def test_access_expected_slots_are_scored(self):
        item = {
            "expected_intent": "access",
            "expected_behavior": "complete",
            "expected_slots": {"organization": "Aetna", "book": "Commercial"},
        }
        turn = {
            "intent": "access",
            "status": "complete",
            "records": [{"organization": "Aetna"}],
            "confirmed_slots": {
                "organization": "Aetna",
                "book": "Medicare",
            },
        }

        comparison = compare_orchestration_result(item, turn)

        self.assertFalse(comparison["match"])
        self.assertFalse(comparison["slots_match"])
        self.assertEqual(comparison["mismatched_fields"][0]["field"], "book")


class AccessWorkflowEvalTests(unittest.TestCase):
    def tearDown(self):
        _sessions.clear()

    def test_access_schema_parses_stage_and_multiturn_fields(self):
        item = normalize_orchestration_row(
            {
                "question": "tregzzi at aetna?",
                "expected_intent": "access",
                "expected_extracted_slots": '{"regimen":"tregzzi"}',
                "expected_resolved_slots": '{"regimen":"Tregzi"}',
                "expected_candidates": '["Commercial"]',
                "clarification_steps": '[{"slot":"book","value":"Commercial"}]',
                "expected_record_count": "1",
                "expected_record_fields": '{"organization":"Aetna"}',
            },
            0,
        )

        self.assertEqual(item["expected_extracted_slots"], {"regimen": "tregzzi"})
        self.assertEqual(item["expected_resolved_slots"], {"regimen": "Tregzi"})
        self.assertEqual(item["expected_candidates"], ["Commercial"])
        self.assertEqual(item["clarification_steps"][0]["slot"], "book")
        self.assertEqual(item["expected_record_count"], 1)
        self.assertEqual(
            item["expected_record_fields"],
            {"organization": "Aetna"},
        )

    def test_access_loader_keeps_access_and_oos_controls(self):
        csv_text = (
            "id,question,expected_intent\n"
            "1,Is Tregzi covered?,access\n"
            "2,How many lives?,lives\n"
            "3,Rank payers,out_of_scope\n"
        )

        items = load_access_questions(csv_text)

        self.assertEqual(
            [item["expected_intent"] for item in items],
            ["access", "out_of_scope"],
        )

    def test_complete_case_scores_extraction_linking_and_retrieval(self):
        _sessions["s1"] = {
            "slots": {"regimen": "tregzzi", "organization": "aetna"},
            "link_info": {
                "regimen": {"status": "matched", "value": "Tregzi"},
                "organization": {"status": "matched", "value": "Aetna"},
            },
        }
        turn = {
            "status": "complete",
            "session_id": "s1",
            "intent": "access",
            "current_slot": None,
            "candidates": [],
            "confirmed_slots": {
                "regimen": "Tregzi",
                "organization": "Aetna",
                "line": "1L+",
            },
            "records": [
                {
                    "regimen": "Tregzi",
                    "organization": "Aetna",
                    "accessData": {"access": "Covered to Label"},
                }
            ],
        }
        item = {
            "question": "tregzzi at aetna?",
            "expected_intent": "access",
            "expected_extracted_slots": {
                "regimen": "tregzzi",
                "organization": "aetna",
            },
            "expected_resolved_slots": {
                "regimen": "Tregzi",
                "organization": "Aetna",
            },
            "expected_behavior": "complete",
            "expected_current_slot": "",
            "expected_candidates": [],
            "clarification_steps": [],
            "expected_record_count": 1,
            "expected_record_fields": {
                "organization": "Aetna",
                "accessData.access": "Covered to Label",
            },
        }

        with patch(
            "evaluation.access_workflow.handle_conversation_turn",
            return_value=turn,
        ):
            row = evaluate_access_item(item)

        comparison = row["comparison"]
        self.assertTrue(comparison["match"])
        self.assertTrue(comparison["extraction_match"])
        self.assertTrue(comparison["linking_match"])
        self.assertTrue(comparison["retrieval_match"])
        self.assertNotIn("s1", _sessions)

    def test_extraction_mismatch_does_not_fail_correct_workflow(self):
        _sessions["s-extraction-diagnostic"] = {
            "slots": {"regimen": "Tregzi", "book": "Medicaid"},
            "link_info": {},
        }
        turn = {
            "status": "complete",
            "session_id": "s-extraction-diagnostic",
            "intent": "access",
            "confirmed_slots": {
                "regimen": "Tregzi",
                "book": "FFS Medicaid",
            },
            "records": [{"regimen": "Tregzi", "book": "FFS Medicaid"}],
        }
        item = {
            "question": "Tregzi Medicaid access",
            "expected_intent": "access",
            "expected_extracted_slots": {
                "regimen": "Tregzi",
                "book": "FFS Medicaid",
            },
            "expected_resolved_slots": {
                "regimen": "Tregzi",
                "book": "FFS Medicaid",
            },
            "expected_behavior": "complete",
            "expected_record_count": 1,
            "expected_record_fields": {"book": "FFS Medicaid"},
        }

        with patch(
            "evaluation.access_workflow.handle_conversation_turn",
            return_value=turn,
        ):
            row = evaluate_access_item(item)

        self.assertFalse(row["comparison"]["extraction_match"])
        self.assertTrue(row["comparison"]["linking_match"])
        self.assertTrue(row["comparison"]["retrieval_match"])
        self.assertTrue(row["comparison"]["match"])

    def test_wrong_canonical_entity_still_fails_workflow(self):
        _sessions["s-wrong-entity"] = {
            "slots": {"regimen": "Tecvayli"},
            "link_info": {},
        }
        turn = {
            "status": "complete",
            "session_id": "s-wrong-entity",
            "intent": "access",
            "confirmed_slots": {"regimen": "Tecvayli"},
            "records": [],
        }
        item = {
            "question": "Tregzi access",
            "expected_intent": "access",
            "expected_extracted_slots": {"regimen": "Tregzi"},
            "expected_resolved_slots": {"regimen": "Tregzi"},
            "expected_behavior": "empty",
            "expected_record_count": 0,
        }

        with patch(
            "evaluation.access_workflow.handle_conversation_turn",
            return_value=turn,
        ):
            row = evaluate_access_item(item)

        self.assertFalse(row["comparison"]["extraction_match"])
        self.assertFalse(row["comparison"]["linking_match"])
        self.assertTrue(row["comparison"]["retrieval_match"])
        self.assertFalse(row["comparison"]["match"])

    def test_access_summary_reports_extraction_separately(self):
        rows = [
            {
                "comparison": {
                    "match": True,
                    "extraction_match": False,
                }
            },
            {
                "comparison": {
                    "match": False,
                    "extraction_match": True,
                }
            },
        ]
        with patch(
            "evaluation.access_workflow.evaluate_access_item",
            side_effect=rows,
        ):
            result = run_access_questions([{"id": "1"}, {"id": "2"}])

        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["extraction_passed"], 1)
        self.assertEqual(result["extraction_scored"], 2)
        self.assertEqual(result["extraction_rate"], 0.5)

    def test_clarification_scores_slot_candidates_and_no_retrieval(self):
        _sessions["s2"] = {
            "slots": {"regimen": "Tregzi"},
            "link_info": {"regimen": {"status": "matched", "value": "Tregzi"}},
        }
        turn = {
            "status": "needs_input",
            "session_id": "s2",
            "intent": "access",
            "current_slot": "organization",
            "candidates": ["Aetna", "Cigna"],
            "confirmed_slots": {"regimen": "Tregzi"},
            "records": [],
        }
        item = {
            "question": "Is Tregzi covered?",
            "expected_intent": "access",
            "expected_extracted_slots": {"regimen": "Tregzi"},
            "expected_resolved_slots": {"regimen": "Tregzi"},
            "expected_behavior": "clarify",
            "expected_current_slot": "organization",
            "expected_candidates": ["Aetna"],
            "clarification_steps": [],
            "expected_record_count": None,
            "expected_record_fields": None,
        }

        with patch(
            "evaluation.access_workflow.handle_conversation_turn",
            return_value=turn,
        ):
            row = evaluate_access_item(item)

        self.assertTrue(row["comparison"]["match"])
        self.assertTrue(row["comparison"]["current_slot_match"])
        self.assertTrue(row["comparison"]["candidates_match"])
        self.assertTrue(row["comparison"]["premature_retrieval_match"])

    def test_scripted_continuation_reaches_expected_record(self):
        _sessions["s3"] = {
            "slots": {"regimen": "Tregzi", "organization": "Aetna"},
            "link_info": {},
        }
        first = {
            "status": "needs_input",
            "session_id": "s3",
            "intent": "access",
            "current_slot": "book",
            "candidates": ["Commercial", "Medicare"],
            "confirmed_slots": {
                "regimen": "Tregzi",
                "organization": "Aetna",
            },
            "records": [],
        }
        complete = {
            "status": "complete",
            "session_id": "s3",
            "intent": "access",
            "current_slot": None,
            "candidates": [],
            "confirmed_slots": {
                "regimen": "Tregzi",
                "organization": "Aetna",
                "book": "Commercial",
            },
            "records": [
                {
                    "regimen": "Tregzi",
                    "organization": "Aetna",
                    "book": "Commercial",
                }
            ],
        }
        item = {
            "question": "Tregzi at Aetna",
            "expected_intent": "access",
            "expected_extracted_slots": {
                "regimen": "Tregzi",
                "organization": "Aetna",
            },
            "expected_resolved_slots": {
                "regimen": "Tregzi",
                "organization": "Aetna",
            },
            "expected_behavior": "clarify",
            "expected_current_slot": "book",
            "expected_candidates": ["Commercial"],
            "clarification_steps": [{"slot": "book", "value": "Commercial"}],
            "expected_record_count": 1,
            "expected_record_fields": {"book": "Commercial"},
        }

        with patch(
            "evaluation.access_workflow.handle_conversation_turn",
            side_effect=[first, complete],
        ) as handle:
            row = evaluate_access_item(item)

        self.assertTrue(row["comparison"]["match"])
        self.assertTrue(row["comparison"]["progression_match"])
        self.assertTrue(row["comparison"]["final_behavior_match"])
        self.assertEqual(row["final_behavior"], "complete")
        self.assertEqual(handle.call_count, 2)

    def test_empty_case_can_assert_zero_records(self):
        _sessions["s4"] = {"slots": {}, "link_info": {}}
        turn = {
            "status": "complete",
            "session_id": "s4",
            "intent": "access",
            "confirmed_slots": {},
            "records": [],
        }
        item = {
            "question": "Unsupported permissioned tuple",
            "expected_intent": "access",
            "expected_extracted_slots": {},
            "expected_resolved_slots": {},
            "expected_behavior": "empty",
            "expected_current_slot": "",
            "expected_candidates": [],
            "clarification_steps": [],
            "expected_record_count": 0,
            "expected_record_fields": None,
        }

        with patch(
            "evaluation.access_workflow.handle_conversation_turn",
            return_value=turn,
        ):
            row = evaluate_access_item(item)

        self.assertTrue(row["comparison"]["match"])
        self.assertTrue(row["comparison"]["record_count_match"])

    def test_premature_retrieval_and_wrong_continuation_slot_fail(self):
        _sessions["s5"] = {"slots": {}, "link_info": {}}
        turn = {
            "status": "needs_input",
            "session_id": "s5",
            "intent": "access",
            "current_slot": "organization",
            "candidates": ["Aetna"],
            "confirmed_slots": {},
            "records": [{"organization": "Aetna"}],
        }
        item = {
            "question": "Access?",
            "expected_intent": "access",
            "expected_extracted_slots": {},
            "expected_resolved_slots": {},
            "expected_behavior": "clarify",
            "expected_current_slot": "organization",
            "expected_candidates": ["Aetna"],
            "clarification_steps": [{"slot": "book", "value": "Commercial"}],
            "expected_record_count": None,
            "expected_record_fields": None,
        }

        with patch(
            "evaluation.access_workflow.handle_conversation_turn",
            return_value=turn,
        ):
            row = evaluate_access_item(item)

        self.assertFalse(row["comparison"]["match"])
        self.assertFalse(row["comparison"]["premature_retrieval_match"])
        self.assertFalse(row["comparison"]["progression_match"])

    def test_out_of_scope_negative_control_scores_without_session(self):
        turn = {
            "status": "complete",
            "session_id": None,
            "intent": "out_of_scope",
            "confirmed_slots": {},
            "records": [],
        }
        item = {
            "question": "Rank all payers by Tregzi access",
            "expected_intent": "out_of_scope",
            "expected_extracted_slots": {},
            "expected_resolved_slots": {},
            "expected_behavior": "refuse",
            "expected_current_slot": "",
            "expected_candidates": [],
            "clarification_steps": [],
            "expected_record_count": None,
            "expected_record_fields": None,
        }

        with patch(
            "evaluation.access_workflow.handle_conversation_turn",
            return_value=turn,
        ):
            row = evaluate_access_item(item)

        self.assertTrue(row["comparison"]["match"])
        self.assertTrue(row["comparison"]["intent_match"])
        self.assertTrue(row["comparison"]["behavior_match"])

    def test_continuation_error_fails_progression(self):
        _sessions["s6"] = {
            "slots": {"regimen": "Tregzi"},
            "link_info": {},
        }
        first = {
            "status": "needs_input",
            "session_id": "s6",
            "intent": "access",
            "current_slot": "book",
            "candidates": ["Commercial"],
            "confirmed_slots": {"regimen": "Tregzi"},
            "records": [],
        }
        error = {
            "status": "error",
            "session_id": "s6",
            "intent": "access",
            "confirmed_slots": {
                "regimen": "Tregzi",
                "book": "Commercial",
            },
            "records": [],
        }
        item = {
            "question": "Tregzi access",
            "expected_intent": "access",
            "expected_extracted_slots": {"regimen": "Tregzi"},
            "expected_resolved_slots": {"regimen": "Tregzi"},
            "expected_behavior": "clarify",
            "expected_current_slot": "book",
            "expected_candidates": ["Commercial"],
            "clarification_steps": [{"slot": "book", "value": "Commercial"}],
            "expected_record_count": None,
            "expected_record_fields": None,
        }

        with patch(
            "evaluation.access_workflow.handle_conversation_turn",
            side_effect=[first, error],
        ):
            row = evaluate_access_item(item)

        self.assertFalse(row["comparison"]["match"])
        self.assertFalse(row["comparison"]["progression_match"])
        self.assertIn(
            "continuation returned an error",
            row["comparison"]["progression_errors"][0],
        )

    def test_access_results_csv_contains_stage_diagnostics(self):
        csv_text = access_results_to_csv(
            [
                {
                    "id": "1",
                    "question": "Is Tregzi covered?",
                    "expected_intent": "access",
                    "actual_intent": "access",
                    "actual_extracted_slots": {"regimen": "Tregzi"},
                    "comparison": {
                        "match": True,
                        "intent_match": True,
                        "extraction_match": True,
                    },
                    "turn_trace": [{"status": "complete"}],
                }
            ]
        )

        self.assertIn("extraction_match", csv_text.splitlines()[0])
        self.assertIn('"{""regimen"": ""Tregzi""}"', csv_text)
        self.assertIn('"[{""status"": ""complete""}]"', csv_text)

    def test_access_results_default_inside_repository(self):
        path = _auto_results_path(Path("/tmp/hard-access-bank.csv"), access=True)

        self.assertEqual(path.parent, ACCESS_RESULTS_DIR)
        self.assertEqual(path.name, "hard-access-bank_access_results.csv")


if __name__ == "__main__":
    unittest.main()
