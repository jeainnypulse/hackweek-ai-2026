from __future__ import annotations

import unittest
from unittest.mock import MagicMock, Mock, patch

from chatbot.conversation.actions import handle_continuation
from chatbot.conversation.completion import _complete_from_session
from chatbot.conversation.resolution import provide_current_slot, resolve_next_slot
from chatbot.conversation.slot_plan import (
    ACCESS_SLOTS,
    JOINT_SLOTS,
    LIVES_SLOTS,
    required_slots,
)
from chatbot.conversation.state import _sessions
from chatbot.conversation.turn import _prepare_lives_fields
from chatbot.query_resolution.cascade_clarify import (
    distinct_values,
    filter_from_confirmed,
    resolve_selection,
)


ACCESS_VALUES = {
    "regimen": "Tregzi",
    "indication": "CLL",
    "book": "Medicare",
    "organization": "Aetna",
    "coverage": "Medical",
    "population": "No Subtype Specified",
    "line": "1L+",
}


def _session(intent: str = "access", values: dict | None = None) -> dict:
    supplied = dict(values or {})
    linked = dict(supplied)
    info = {
        field: {
            "raw": value,
            "status": "matched",
            "value": value,
            "method": "catalog",
        }
        for field, value in supplied.items()
        if value not in (None, "")
    }
    return {
        "session_id": "session-1",
        "question": "question",
        "intent": intent,
        "slots": dict(supplied),
        "linked": linked,
        "link_info": info,
        "pending_writebacks": [],
        "current_slot": None,
    }


def _field_options(options: dict[str, list[str]]):
    def fake(_session, field, _filt):
        return list(options.get(field, []))

    return fake


class SlotPlanTests(unittest.TestCase):
    def test_required_slot_orders_are_exact(self) -> None:
        self.assertEqual(
            ACCESS_SLOTS,
            (
                "regimen",
                "indication",
                "book",
                "organization",
                "coverage",
                "population",
                "line",
            ),
        )
        self.assertEqual(
            LIVES_SLOTS,
            ("organization", "book", "territoryName", "coverage"),
        )
        self.assertEqual(JOINT_SLOTS, (*ACCESS_SLOTS, "territoryName"))
        self.assertEqual(required_slots("access"), ACCESS_SLOTS)
        self.assertEqual(required_slots("lives"), LIVES_SLOTS)
        self.assertEqual(required_slots("joint"), JOINT_SLOTS)


class PrimitiveTests(unittest.TestCase):
    def test_selection_is_case_and_whitespace_insensitive(self) -> None:
        self.assertEqual(
            resolve_selection("  medical   benefit ", ["Medical Benefit"]),
            "Medical Benefit",
        )
        self.assertIsNone(resolve_selection("Dental", ["Medical"]))
        self.assertEqual(
            filter_from_confirmed({"coverage": "Medical", "line": None}),
            {"coverage": "Medical"},
        )

    def test_distinct_values_is_permission_scoped_and_cleaned(self) -> None:
        collection = Mock()
        collection.distinct.return_value = [" pharmacy ", None, "", "Medical"]
        scoped = Mock(return_value={"permission": "filter"})
        with patch(
            "chatbot.query_resolution.cascade_clarify.get_db",
            return_value={"payerLatestAccess": collection},
        ):
            result = distinct_values(
                "coverage",
                {"organization": "Aetna"},
                scoped_fn=scoped,
            )

        scoped.assert_called_once_with({"organization": "Aetna"})
        collection.distinct.assert_called_once_with(
            "coverage", {"permission": "filter"}
        )
        self.assertEqual(result, ["Medical", "pharmacy"])


class FacetedProgressionTests(unittest.TestCase):
    def tearDown(self) -> None:
        _sessions.clear()

    def test_first_missing_field_is_the_only_requested_field(self) -> None:
        session = _session()
        with patch(
            "chatbot.conversation.resolution._distinct_for_session",
            side_effect=_field_options({"regimen": ["Tregzi", "Brukinsa"]}),
        ):
            response = resolve_next_slot(session)

        self.assertEqual(response["status"], "needs_input")
        self.assertEqual(response["current_slot"], "regimen")
        self.assertEqual(response["candidates"], ["Tregzi", "Brukinsa"])
        self.assertFalse(response["allow_skip"])

    def test_confirmed_fields_are_not_requeried(self) -> None:
        session = _session(values={"regimen": "Tregzi"})
        with patch(
            "chatbot.conversation.resolution._distinct_for_session",
            return_value=["CLL", "MCL"],
        ) as distinct:
            response = resolve_next_slot(session)

        self.assertEqual(response["current_slot"], "indication")
        distinct.assert_called_once_with(
            session,
            "indication",
            {"regimen": "Tregzi"},
        )

    def test_supplied_typo_still_uses_linker_candidates(self) -> None:
        session = _session(values={"regimen": "Tregzzi"})
        session["linked"].pop("regimen")
        session["link_info"]["regimen"] = {
            "raw": "Tregzzi",
            "status": "ambiguous",
            "candidates": ["Tregzi", "Brukinsa"],
            "method": "fuzzy",
        }
        with patch(
            "chatbot.conversation.resolution._distinct_for_session",
            return_value=["Tregzi", "Brukinsa", "Other"],
        ):
            response = resolve_next_slot(session)

        self.assertEqual(response["current_slot"], "regimen")
        self.assertEqual(response["candidates"], ["Tregzi", "Brukinsa"])
        self.assertIn("Tregzzi", response["message"])
        self.assertNotIn("regimen", response["confirmed_slots"])

    def test_sole_scoped_linker_candidate_is_auto_confirmed(self) -> None:
        session = _session(values={"regimen": "Tregzzi"})
        session["linked"].pop("regimen")
        session["link_info"]["regimen"] = {
            "raw": "Tregzzi",
            "status": "ambiguous",
            "candidates": ["Tregzi"],
            "method": "fuzzy",
        }

        def distinct(_session, field, _filt):
            if field == "regimen":
                return ["Tregzi", "Brukinsa"]
            return ["CLL", "MCL"]

        with patch(
            "chatbot.conversation.resolution._distinct_for_session",
            side_effect=distinct,
        ):
            response = resolve_next_slot(session)

        self.assertEqual(session["linked"]["regimen"], "Tregzi")
        self.assertEqual(response["current_slot"], "indication")

    def test_confirming_value_recomputes_next_field_options(self) -> None:
        values = {field: ACCESS_VALUES[field] for field in ACCESS_SLOTS[:4]}
        session = _session(values=values)
        session["current_slot"] = "coverage"
        calls = []

        def distinct(_session, field, filt):
            calls.append((field, dict(filt)))
            if field == "coverage":
                return ["Medical", "Pharmacy"]
            if field == "population":
                return ["EGFR+", "No Subtype Specified"]
            return [ACCESS_VALUES[field]]

        with patch(
            "chatbot.conversation.resolution._distinct_for_session",
            side_effect=distinct,
        ):
            response = provide_current_slot(session, "Medical")

        self.assertEqual(response["current_slot"], "population")
        self.assertEqual(response["candidates"], ["EGFR+", "No Subtype Specified"])
        self.assertIn(
            (
                "population",
                {
                    **values,
                    "coverage": "Medical",
                },
            ),
            calls,
        )

    def test_sole_options_auto_confirm_through_exact_completion(self) -> None:
        session = _session()
        options = {field: [value] for field, value in ACCESS_VALUES.items()}
        with (
            patch(
                "chatbot.conversation.resolution._distinct_for_session",
                side_effect=_field_options(options),
            ),
            patch(
                "chatbot.conversation.resolution._complete_from_session",
                return_value={"status": "complete"},
            ) as complete,
        ):
            response = resolve_next_slot(session)

        self.assertEqual(response["status"], "complete")
        self.assertEqual(
            {field: session["linked"][field] for field in ACCESS_SLOTS},
            ACCESS_VALUES,
        )
        complete.assert_called_once_with(session)

    def test_invalid_selection_is_not_confirmed(self) -> None:
        session = _session()
        session["current_slot"] = "regimen"
        with patch(
            "chatbot.conversation.resolution._distinct_for_session",
            return_value=["Tregzi", "Brukinsa"],
        ):
            response = provide_current_slot(session, "Not a product")

        self.assertEqual(response["status"], "needs_input")
        self.assertEqual(response["current_slot"], "regimen")
        self.assertNotIn("regimen", session["linked"])
        self.assertIn("valid product", response["message"])

    def test_zero_options_returns_resolution_error_without_querying(self) -> None:
        session = _session()
        with (
            patch(
                "chatbot.conversation.resolution._distinct_for_session",
                return_value=[],
            ),
            patch(
                "chatbot.conversation.resolution._complete_from_session"
            ) as complete,
        ):
            response = resolve_next_slot(session)

        self.assertEqual(response["status"], "error")
        self.assertIn("No valid product", response["message"])
        self.assertEqual(response["records"], [])
        complete.assert_not_called()

    def test_zero_options_after_confirmed_facets_returns_empty_without_querying(
        self,
    ) -> None:
        session = _session(
            values={
                "regimen": "Tregzi",
                "indication": "CLL",
                "book": "FFS Medicaid",
                "organization": "Aetna",
            }
        )
        with (
            patch(
                "chatbot.conversation.resolution._distinct_for_session",
                return_value=[],
            ),
            patch(
                "chatbot.conversation.resolution._complete_from_session"
            ) as complete,
        ):
            response = resolve_next_slot(session)

        self.assertEqual(response["status"], "complete")
        self.assertEqual(response["records"], [])
        self.assertEqual(
            response["confirmed_slots"]["book"],
            "FFS Medicaid",
        )
        complete.assert_not_called()

    def test_completion_queries_only_after_all_required_fields_are_confirmed(self) -> None:
        session = _session(values=ACCESS_VALUES)
        options = {field: [value] for field, value in ACCESS_VALUES.items()}
        with (
            patch(
                "chatbot.conversation.resolution._distinct_for_session",
                side_effect=_field_options(options),
            ),
            patch(
                "chatbot.conversation.completion._query_records",
                return_value=[{"coverage": "Medical"}],
            ) as query,
        ):
            response = resolve_next_slot(session)

        self.assertEqual(response["status"], "complete")
        query.assert_called_once_with(ACCESS_VALUES, intent="access")

    def test_completion_guard_never_queries_partial_slots(self) -> None:
        session = _session(values={"regimen": "Tregzi"})
        with patch("chatbot.conversation.completion._query_records") as query:
            response = _complete_from_session(session)

        self.assertEqual(response["status"], "error")
        self.assertIn("Cannot query", response["message"])
        query.assert_not_called()

    def test_alias_confirmation_is_queued_for_writeback(self) -> None:
        session = _session()
        session["slots"]["regimen"] = "Tregzzi"
        session["link_info"]["regimen"] = {
            "raw": "Tregzzi",
            "status": "ambiguous",
            "candidates": ["Tregzi"],
        }
        session["current_slot"] = "regimen"
        with patch(
            "chatbot.conversation.resolution._distinct_for_session",
            side_effect=_field_options(
                {
                    "regimen": ["Tregzi", "Brukinsa"],
                    "indication": ["CLL", "MCL"],
                }
            ),
        ):
            provide_current_slot(session, "Tregzi")

        self.assertEqual(
            session["pending_writebacks"],
            [
                {
                    "dimension": "regimen",
                    "alias": "Tregzzi",
                    "canonical": "Tregzi",
                }
            ],
        )

    def test_expired_session_and_non_provide_actions_are_rejected(self) -> None:
        expired = handle_continuation("expired", "provide", "Tregzi")
        self.assertEqual(expired["status"], "error")
        self.assertIn("expired", expired["message"].lower())

        session = _session()
        _sessions[session["session_id"]] = session
        rejected = handle_continuation(session["session_id"], "skip")
        self.assertEqual(rejected["status"], "error")
        self.assertIn("provide", rejected["message"])


class LivesAndJointTests(unittest.TestCase):
    def test_missing_lives_territory_gets_national_default(self) -> None:
        session = _session(
            "lives",
            {"organization": "Aetna", "book": "Medicare", "coverage": "Medical"},
        )
        session["slots"]["territoryName"] = None
        with patch(
            "chatbot.conversation.turn.apply_lives_defaults"
        ) as apply_defaults:
            apply_defaults.side_effect = lambda _slots, linked: linked.update(
                {
                    "territoryName": "United States",
                    "territoryType": "National",
                    "source": "DRG",
                }
            )
            _prepare_lives_fields(
                session["slots"], session["linked"], session["link_info"]
            )

        self.assertEqual(session["linked"]["territoryName"], "United States")
        self.assertEqual(
            session["link_info"]["territoryName"]["method"],
            "lives:default",
        )

    def test_unresolved_territory_is_not_overwritten_by_default(self) -> None:
        slots = {"territoryName": "Californa", "territoryInvalid": None}
        linked = {"territoryName": "Californa"}
        info = {
            "territoryName": {
                "raw": "Californa",
                "status": "ambiguous",
                "candidates": ["CA"],
            }
        }
        with patch(
            "chatbot.conversation.turn.apply_lives_defaults"
        ) as apply_defaults:
            _prepare_lives_fields(slots, linked, info)

        apply_defaults.assert_not_called()
        self.assertNotIn("territoryName", linked)
        self.assertEqual(linked["source"], "DRG")

    def test_lives_asks_in_organization_book_territory_coverage_order(self) -> None:
        session = _session("lives")
        with patch(
            "chatbot.conversation.resolution._distinct_for_session",
            return_value=["Aetna", "Centene"],
        ):
            response = resolve_next_slot(session)
        self.assertEqual(response["current_slot"], "organization")

    def test_joint_requests_territory_after_all_seven_access_fields(self) -> None:
        session = _session("joint", ACCESS_VALUES)

        def distinct(_session, field, _filt):
            if field == "territoryName":
                return ["CA", "United States"]
            return [ACCESS_VALUES[field]]

        with patch(
            "chatbot.conversation.resolution._distinct_for_session",
            side_effect=distinct,
        ):
            response = resolve_next_slot(session)

        self.assertEqual(response["current_slot"], "territoryName")
        self.assertEqual(response["candidates"], ["CA", "United States"])

    def test_joint_territory_distinct_uses_lives_collection(self) -> None:
        session = _session("joint", ACCESS_VALUES)
        with patch(
            "chatbot.conversation.queries.distinct_values",
            return_value=["CA"],
        ) as distinct, patch(
            "chatbot.conversation.queries._organization_to_slug",
            return_value="aetna",
        ):
            from chatbot.conversation.queries import _distinct_for_session

            result = _distinct_for_session(
                session,
                "territoryName",
                ACCESS_VALUES,
            )

        self.assertEqual(result, ["CA"])
        self.assertEqual(
            distinct.call_args.kwargs["collection"],
            "payerLatestLives",
        )

    def test_joint_access_facets_do_not_filter_on_territory(self) -> None:
        session = _session(
            "joint",
            {**ACCESS_VALUES, "territoryName": "CA"},
        )
        with patch(
            "chatbot.conversation.queries.distinct_values",
            return_value=["CLL"],
        ) as distinct:
            from chatbot.conversation.queries import _distinct_for_session

            result = _distinct_for_session(
                session,
                "indication",
                {"regimen": "Tregzi", "territoryName": "CA"},
            )

        self.assertEqual(result, ["CLL"])
        distinct.assert_called_once_with(
            "indication",
            {"regimen": "Tregzi"},
        )


if __name__ == "__main__":
    unittest.main()
