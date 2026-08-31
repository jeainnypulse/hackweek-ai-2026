from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import main


class MainApiTests(unittest.TestCase):
    def test_finalize_contract_synthesizes_and_skips_logging(self):
        confirmed = {"organization": "Aetna", "book": "Medicare"}

        with (
            patch(
                "main.synthesize_answer",
                return_value=("Covered.", [{"source": "policy"}]),
            ) as synthesize,
            patch("main.get_db") as get_db,
            patch("main.uuid.uuid4", return_value="result-1"),
        ):
            result = main._finalize_ask(
                "Is it covered?",
                [{"organization": "Aetna"}],
                "exact_match_entity_linker",
                True,
                confirmed_slots=confirmed,
                intent="access",
            )

        synthesize.assert_called_once_with(
            "Is it covered?",
            [{"organization": "Aetna"}],
            intent="access",
            confirmed_slots=confirmed,
        )
        get_db.assert_not_called()
        self.assertEqual(
            result,
            {
                "id": "result-1",
                "status": "complete",
                "question": "Is it covered?",
                "answer": "Covered.",
                "citations": [{"source": "policy"}],
                "retrieval_method": "exact_match_entity_linker",
                "intent": "access",
                "records_retrieved": 1,
                "confirmed_slots": confirmed,
                "session_id": None,
            },
        )

    def test_finalize_logs_complete_result(self):
        db = MagicMock()

        with (
            patch("main.get_db", return_value=db),
            patch("main.uuid.uuid4", return_value="result-2"),
            patch("main.synthesize_answer") as synthesize,
        ):
            result = main._finalize_ask(
                "Unsupported question",
                [],
                "exact_match_entity_linker",
                False,
                confirmed_slots={},
                intent="out_of_scope",
                answer="I can only answer payer questions.",
                citations=[],
            )

        synthesize.assert_not_called()
        db.logs.insert_one.assert_called_once_with(
            {
                "_id": "result-2",
                "question": "Unsupported question",
                "retrieval_method": "exact_match_entity_linker",
                "intent": "out_of_scope",
                "records_retrieved": 0,
                "answer": "I can only answer payer questions.",
                "citations": [],
                "confirmed_slots": {},
                "feedback": None,
            }
        )
        self.assertEqual(result["id"], "result-2")
        self.assertEqual(result["records_retrieved"], 0)

    def test_ask_clarification_response_contract(self):
        turn = {
            "status": "needs_input",
            "session_id": "session-1",
            "question": "Is it covered?",
            "message": "Choose a payer.",
            "current_slot": "organization",
            "proposed_value": None,
            "candidates": ["Aetna", "Centene"],
            "confirmed_slots": {"book": "Medicare"},
            "allow_skip": False,
            "input_mode": "dropdown",
            "cascade": None,
            "link_info": {"organization": {"status": "ambiguous"}},
            "intent": "access",
        }

        with patch(
            "main.handle_conversation_turn", return_value=turn
        ) as handle_turn:
            result = main.ask(main.AskRequest(question="  Is it covered?  "))

        handle_turn.assert_called_once_with(question="Is it covered?")
        self.assertEqual(result["id"], None)
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["answer"], "Choose a payer.")
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["records_retrieved"], 0)
        self.assertEqual(result["candidates"], ["Aetna", "Centene"])
        self.assertEqual(result["session_id"], "session-1")

    def test_ask_continuation_forwards_action_payload(self):
        turn = {
            "status": "needs_cascade_input",
            "session_id": "session-1",
            "question": "Is it covered?",
            "cascade": {"pending_slots": ["coverage"]},
            "intent": "access",
        }
        value = {"coverage": "Medical"}

        with patch(
            "main.handle_conversation_turn", return_value=turn
        ) as handle_turn:
            result = main.ask(
                main.AskRequest(
                    session_id="session-1",
                    action="cascade_options",
                    value=value,
                )
            )

        handle_turn.assert_called_once_with(
            session_id="session-1",
            action="cascade_options",
            value=value,
        )
        self.assertEqual(result["status"], "needs_cascade_input")
        self.assertEqual(result["input_mode"], "dropdown")

    def test_ask_complete_contract_includes_session_and_link_info(self):
        turn = {
            "status": "complete",
            "session_id": "session-2",
            "question": "Is Tregzi covered?",
            "records": [{"coverage": "Medical"}],
            "confirmed_slots": {"regimen": "Tregzi"},
            "link_info": {"regimen": {"status": "matched"}},
            "intent": "access",
        }

        with (
            patch("main.handle_conversation_turn", return_value=turn),
            patch(
                "main.synthesize_answer",
                return_value=("Yes.", [{"record": 1}]),
            ),
            patch("main.uuid.uuid4", return_value="result-3"),
        ):
            result = main.ask(
                main.AskRequest(
                    question="Is Tregzi covered?",
                    skip_log=True,
                )
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["session_id"], "session-2")
        self.assertEqual(
            result["link_info"],
            {"regimen": {"status": "matched"}},
        )
        self.assertEqual(result["answer"], "Yes.")
        self.assertEqual(result["records_retrieved"], 1)

    def test_out_of_scope_bypasses_synthesis_and_is_logged(self):
        turn = {
            "status": "complete",
            "session_id": None,
            "question": "Write me a poem",
            "records": [],
            "confirmed_slots": {},
            "link_info": {},
            "message": "I can only answer payer questions.",
            "intent": "out_of_scope",
        }
        db = MagicMock()

        with (
            patch("main.handle_conversation_turn", return_value=turn),
            patch("main.synthesize_answer") as synthesize,
            patch("main.get_db", return_value=db),
            patch("main.uuid.uuid4", return_value="result-4"),
        ):
            result = main.ask(main.AskRequest(question="Write me a poem"))

        synthesize.assert_not_called()
        self.assertEqual(result["intent"], "out_of_scope")
        self.assertEqual(result["answer"], turn["message"])
        self.assertEqual(result["citations"], [])
        db.logs.insert_one.assert_called_once()
        logged = db.logs.insert_one.call_args.args[0]
        self.assertEqual(logged["intent"], "out_of_scope")
        self.assertEqual(logged["records_retrieved"], 0)

    def test_unknown_retrieval_method_is_rejected_before_orchestration(self):
        with patch("main.handle_conversation_turn") as handle_turn:
            with self.assertRaises(HTTPException) as raised:
                main.ask(
                    main.AskRequest(
                        question="Is it covered?",
                        retrieval_method="semantic",
                    )
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Unknown retrieval method", raised.exception.detail)
        handle_turn.assert_not_called()

    def test_empty_request_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            main.ask(main.AskRequest(question="   "))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "Question cannot be empty")

    def test_orchestration_failures_are_mapped_to_service_unavailable(self):
        with patch(
            "main.handle_conversation_turn",
            side_effect=RuntimeError("database unavailable"),
        ):
            with self.assertRaises(HTTPException) as raised:
                main.ask(main.AskRequest(question="Is it covered?"))

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "database unavailable")


if __name__ == "__main__":
    unittest.main()
