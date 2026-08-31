from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from chatbot.conversation.actions import _present_cascade
from chatbot.conversation.flow import _linker_clarify_response
from chatbot.conversation.identity import (
    _first_pending_link,
    _refresh_missing_access_identity,
    _resolved_identity_filter,
    _scoped_candidates,
)
from chatbot.conversation.queries import _lives_mongo_filter, _query_access_records
from chatbot.conversation.state import _sessions
from chatbot.conversation.turn import handle_conversation_turn


def _session() -> dict:
    return {
        "session_id": "test",
        "question": "Is Keytrudax unrestricted at Aetna?",
        "intent": "access",
        "slots": {
            "regimen": "Keytrudax",
            "indication": None,
            "book": "Medicare",
            "organization": "Aetna",
        },
        "linked": {
            "regimen": "Tregzi",
            "book": "Medicare",
            "organization": "Aetna",
        },
        "link_info": {
            "regimen": {
                "raw": "Keytrudax",
                "status": "matched",
                "value": "Tregzi",
                "method": "user_confirm",
            },
            "book": {"status": "matched", "value": "Medicare"},
            "organization": {"status": "matched", "value": "Aetna"},
        },
        "pending_writebacks": [],
        "current_slot": None,
        "cascade": None,
    }


class TreatmentPlanCompletionTests(unittest.TestCase):
    def tearDown(self):
        _sessions.clear()

    def test_missing_identity_is_recomputed_after_product_confirmation(self):
        session = _session()

        with patch(
            "chatbot.conversation.identity._distinct_for_session",
            return_value=["CLL", "MCL"],
        ):
            response = _linker_clarify_response(session)

        self.assertEqual(response["status"], "needs_input")
        self.assertEqual(response["current_slot"], "indication")
        self.assertEqual(response["candidates"], ["CLL", "MCL"])

    def test_sole_missing_identity_is_auto_confirmed(self):
        session = _session()

        with patch(
            "chatbot.conversation.identity._distinct_for_session",
            return_value=["CLL"],
        ):
            _refresh_missing_access_identity(session)

        self.assertEqual(session["linked"]["indication"], "CLL")
        self.assertEqual(
            session["link_info"]["indication"]["method"],
            "cascade:auto_sole",
        )

    def test_unresolved_raw_identity_is_not_used_to_scope_other_fields(self):
        session = _session()
        session["linked"]["regimen"] = "Keytrudax"
        session["link_info"]["regimen"] = {
            "raw": "Keytrudax",
            "status": "ambiguous",
            "candidates": ["Tregzi"],
        }

        self.assertEqual(
            _resolved_identity_filter(session, exclude="indication"),
            {"book": "Medicare", "organization": "Aetna"},
        )

    def test_pending_link_prefers_ambiguous_before_unknown(self):
        pending = _first_pending_link(
            {
                "regimen": {
                    "status": "unknown",
                    "candidates": ["Tregzi"],
                },
                "book": {
                    "status": "ambiguous",
                    "candidates": ["Medicare", "Commercial"],
                },
            }
        )

        self.assertEqual(pending[0], "book")

    def test_scoped_candidates_intersect_linker_candidates(self):
        session = _session()

        with patch(
            "chatbot.conversation.identity._distinct_for_session",
            return_value=["CLL", "MCL"],
        ):
            candidates = _scoped_candidates(
                session,
                "indication",
                ["MCL", "NSCLC"],
            )

        self.assertEqual(candidates, ["MCL"])

    def test_lives_filter_applies_defaults_and_can_drop_dimensions(self):
        slots = {
            "organization": "Aetna",
            "book": "Medicare",
            "coverage": "Medical",
        }

        with patch(
            "chatbot.conversation.queries._organization_to_slug",
            return_value="aetna",
        ):
            query = _lives_mongo_filter(slots)
            dropped = _lives_mongo_filter(
                slots,
                drop_fields={"organization", "coverage"},
            )

        self.assertEqual(
            query,
            {
                "slug": "aetna",
                "book": "Medicare",
                "coverage": "Medical",
                "source": "DRG",
                "territoryType": "National",
                "territoryName": "United States",
            },
        )
        self.assertNotIn("slug", dropped)
        self.assertNotIn("coverage", dropped)
        self.assertEqual(dropped["book"], "Medicare")

    def test_access_query_strips_lives_only_fields_and_scopes_filter(self):
        collection = MagicMock()
        collection.find.return_value.limit.return_value = [{"book": "Medicare"}]
        db = MagicMock(payerLatestAccess=collection)
        slots = {
            "regimen": "Tregzi",
            "book": "Medicare",
            "territoryName": "CA",
            "collection": "access",
            "line": None,
        }

        with (
            patch("chatbot.conversation.queries.get_db", return_value=db),
            patch(
                "chatbot.conversation.queries.scoped_query",
                side_effect=lambda query: {"scoped": query},
            ) as scoped,
        ):
            records = _query_access_records(slots)

        scoped.assert_called_once_with(
            {"regimen": "Tregzi", "book": "Medicare"}
        )
        collection.find.assert_called_once_with(
            {"scoped": {"regimen": "Tregzi", "book": "Medicare"}},
            {"_id": 0},
        )
        self.assertEqual(records, [{"book": "Medicare"}])

    def test_linker_clarify_payload_contract(self):
        session = _session()
        session["linked"]["indication"] = None
        session["link_info"]["indication"] = {
            "raw": None,
            "status": "ambiguous",
            "candidates": ["CLL", "MCL"],
        }

        with patch(
            "chatbot.conversation.identity._distinct_for_session",
            return_value=["CLL", "MCL"],
        ):
            response = _linker_clarify_response(session)

        self.assertEqual(response["status"], "needs_input")
        self.assertEqual(response["input_mode"], "dropdown")
        self.assertTrue(response["allow_skip"])
        self.assertEqual(response["records"], [])
        self.assertIsNone(response["cascade"])
        self.assertEqual(response["intent"], "access")

    def test_cascade_payload_contract(self):
        session = _session()
        cascade = {
            "pending_slots": ["coverage", "population"],
            "selections": {
                "coverage": None,
                "population": None,
                "line": None,
            },
            "options": {
                "coverage": ["Medical", "Pharmacy"],
                "population": [],
                "line": [],
            },
            "allow_skip": {"population": True, "line": True},
        }

        with (
            patch(
                "chatbot.conversation.actions.auto_confirm_sole_cascade_options",
                return_value={},
            ),
            patch(
                "chatbot.conversation.actions.build_cascade_state",
                return_value=cascade,
            ),
        ):
            response = _present_cascade(session)

        self.assertEqual(response["status"], "needs_cascade_input")
        self.assertEqual(response["input_mode"], "cascade")
        self.assertEqual(response["candidates"], ["Medical", "Pharmacy"])
        self.assertEqual(response["cascade"], cascade)
        self.assertFalse(response["allow_skip"])

    def test_expired_session_action_returns_error(self):
        response = handle_conversation_turn(
            session_id="expired",
            action="provide",
            value="Tregzi",
        )

        self.assertEqual(response["status"], "error")
        self.assertIn("expired", response["message"].lower())
        self.assertEqual(response["confirmed_slots"], {})

    def test_provide_records_confirmation_and_pending_synonym(self):
        session = _session()
        session["current_slot"] = "regimen"
        session["linked"]["regimen"] = None
        session["link_info"]["regimen"] = {
            "raw": "Keytrudax",
            "status": "ambiguous",
            "candidates": ["Tregzi"],
        }
        _sessions["test"] = session

        with patch(
            "chatbot.conversation.flow._linker_clarify_response",
            return_value={"status": "next"},
        ):
            response = handle_conversation_turn(
                session_id="test",
                action="provide",
                value="Tregzi",
            )

        self.assertEqual(response, {"status": "next"})
        self.assertEqual(session["linked"]["regimen"], "Tregzi")
        self.assertEqual(
            session["pending_writebacks"],
            [
                {
                    "dimension": "regimen",
                    "alias": "Keytrudax",
                    "canonical": "Tregzi",
                }
            ],
        )

    def test_skip_marks_current_slot_and_continues(self):
        session = _session()
        session["current_slot"] = "indication"
        session["link_info"]["indication"] = {
            "status": "ambiguous",
            "candidates": ["CLL", "MCL"],
        }
        _sessions["test"] = session

        with patch(
            "chatbot.conversation.flow._linker_clarify_response",
            return_value={"status": "next"},
        ):
            response = handle_conversation_turn(
                session_id="test",
                action="skip",
            )

        self.assertEqual(response, {"status": "next"})
        self.assertEqual(
            session["link_info"]["indication"]["status"],
            "skipped",
        )

    def test_cascade_options_rebuilds_payload_with_selections(self):
        session = _session()
        _sessions["test"] = session

        with patch(
            "chatbot.conversation.actions._present_cascade",
            return_value={"status": "needs_cascade_input"},
        ) as present:
            response = handle_conversation_turn(
                session_id="test",
                action="cascade_options",
                value='{"coverage": "Medical"}',
            )

        present.assert_called_once_with(session, {"coverage": "Medical"})
        self.assertEqual(response["status"], "needs_cascade_input")

    def test_submit_cascade_commits_valid_selections(self):
        session = _session()
        session["linked"].pop("coverage", None)
        session["linked"].pop("population", None)
        session["linked"].pop("line", None)
        _sessions["test"] = session
        cascade = {
            "pending_slots": ["coverage", "population", "line"],
            "selections": {
                "coverage": "Medical",
                "population": "CLL",
                "line": "2L",
            },
            "options": {
                "coverage": ["Medical"],
                "population": ["CLL"],
                "line": ["1L", "2L"],
            },
            "allow_skip": {"population": True, "line": True},
        }

        with (
            patch(
                "chatbot.conversation.actions.build_cascade_state",
                return_value=cascade,
            ),
            patch(
                "chatbot.conversation.actions.distinct_values",
                return_value=["1L", "2L"],
            ),
            patch(
                "chatbot.conversation.actions._maybe_lives_coverage_then_complete",
                return_value={"status": "complete"},
            ) as complete,
        ):
            response = handle_conversation_turn(
                session_id="test",
                action="submit_cascade",
                value={
                    "coverage": "Medical",
                    "population": "CLL",
                    "line": "2L",
                },
            )

        self.assertEqual(response["status"], "complete")
        self.assertEqual(session["linked"]["coverage"], "Medical")
        self.assertEqual(session["linked"]["population"], "CLL")
        self.assertEqual(session["linked"]["line"], "2L")
        complete.assert_called_once_with(session)


if __name__ == "__main__":
    unittest.main()
