from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from chatbot.query_resolution.cascade_clarify import (
    auto_confirm_sole_cascade_options,
    build_cascade_state,
    cascade_needed,
    clean_slot_value,
    distinct_values,
    filter_from_confirmed,
    parse_selections,
    population_candidates,
    population_dropdown_options,
    resolve_selection,
)


IDENTITY = {
    "organization": "Aetna",
    "regimen": "Keytruda",
    "indication": "NSCLC",
    "book": "Commercial",
}


def _distinct_from(mapping):
    def fake(field, filter_query):
        key = (field, tuple(sorted(filter_query.items())))
        return list(mapping.get(key, []))

    return fake


class CascadeHelperTests(unittest.TestCase):
    def test_clean_and_filter_drop_only_empty_slot_values(self) -> None:
        self.assertIsNone(clean_slot_value(None))
        self.assertIsNone(clean_slot_value(""))
        self.assertIsNone(clean_slot_value("null"))
        self.assertEqual(clean_slot_value("  Medical  "), "Medical")
        self.assertEqual(
            filter_from_confirmed(
                {"coverage": "Medical", "population": None, "line": ""}
            ),
            {"coverage": "Medical"},
        )

    def test_population_helpers_distinguish_skip_sentinels(self) -> None:
        values = ["No Subtype Specified", "n/a", "None", "EGFR+"]
        self.assertEqual(population_candidates(values), ["EGFR+"])
        self.assertEqual(
            population_dropdown_options(values),
            ["No Subtype Specified", "EGFR+"],
        )
        self.assertEqual(
            population_dropdown_options(["n/a", "none", ""]),
            [],
        )

    def test_selection_is_case_and_whitespace_insensitive(self) -> None:
        options = ["Medical Benefit", "Pharmacy"]
        self.assertEqual(
            resolve_selection("  medical   benefit ", options),
            "Medical Benefit",
        )
        self.assertIsNone(resolve_selection("Dental", options))
        self.assertIsNone(resolve_selection(None, options))

    def test_parse_selections_accepts_dict_or_json_object(self) -> None:
        self.assertEqual(
            parse_selections({"coverage": " Medical ", "line": "null"}),
            {"coverage": "Medical", "line": None},
        )
        self.assertEqual(
            parse_selections(json.dumps({"population": " EGFR+ "})),
            {"population": "EGFR+"},
        )

    def test_parse_selections_rejects_invalid_or_non_object_json(self) -> None:
        self.assertEqual(parse_selections("not json"), {})
        self.assertEqual(parse_selections('["Medical"]'), {})
        self.assertEqual(parse_selections(""), {})
        self.assertEqual(parse_selections(None), {})

    def test_distinct_values_scopes_mongo_query_and_cleans_results(self) -> None:
        collection = Mock()
        collection.distinct.return_value = [
            " pharmacy ",
            None,
            "",
            "Medical",
            "Medical",
        ]
        database = {"payerLatestAccess": collection}
        scoped = Mock(return_value={"organization": {"$eq": "Aetna"}})

        with patch(
            "chatbot.query_resolution.cascade_clarify.get_db",
            return_value=database,
        ):
            result = distinct_values(
                "coverage",
                {"organization": "Aetna"},
                scoped_fn=scoped,
            )

        self.assertEqual(result, ["Medical", "pharmacy"])
        scoped.assert_called_once_with({"organization": "Aetna"})
        collection.distinct.assert_called_once_with(
            "coverage", {"organization": {"$eq": "Aetna"}}
        )


class BuildCascadeStateTests(unittest.TestCase):
    def test_options_are_scoped_through_coverage_population_and_line(self) -> None:
        identity_key = tuple(sorted(IDENTITY.items()))
        medical_filter = tuple(sorted({**IDENTITY, "coverage": "Medical"}.items()))
        population_filter = tuple(
            sorted(
                {
                    **IDENTITY,
                    "coverage": "Medical",
                    "population": "EGFR+",
                }.items()
            )
        )
        mapping = {
            ("coverage", identity_key): ["Medical", "Pharmacy"],
            ("population", medical_filter): ["EGFR+", "No Subtype Specified"],
            ("line", population_filter): ["1L", "2L+"],
        }

        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            side_effect=_distinct_from(mapping),
        ):
            state = build_cascade_state(
                IDENTITY,
                {
                    "coverage": " medical ",
                    "population": "egfr+",
                    "line": "2l+",
                },
            )

        self.assertEqual(
            state["selections"],
            {"coverage": "Medical", "population": "EGFR+", "line": "2L+"},
        )
        self.assertEqual(state["options"]["coverage"], ["Medical", "Pharmacy"])
        self.assertEqual(
            state["options"]["population"],
            ["EGFR+", "No Subtype Specified"],
        )
        self.assertEqual(state["options"]["line"], ["1L", "2L+"])
        self.assertEqual(
            state["pending_slots"], ["coverage", "population", "line"]
        )

    def test_dependencies_stay_unselected_when_parent_selection_is_invalid(self) -> None:
        identity_key = tuple(sorted(IDENTITY.items()))
        mapping = {
            ("coverage", identity_key): ["Medical", "Pharmacy"],
            ("line", identity_key): ["1L", "2L"],
        }

        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            side_effect=_distinct_from(mapping),
        ):
            state = build_cascade_state(
                IDENTITY,
                {"coverage": "Dental", "population": "EGFR+"},
            )

        self.assertIsNone(state["selections"]["coverage"])
        self.assertEqual(state["options"]["population"], [])
        self.assertEqual(state["options"]["line"], [])
        self.assertEqual(state["pending_slots"], ["coverage", "line"])

    def test_no_real_population_candidates_allows_line_selection(self) -> None:
        identity_key = tuple(sorted(IDENTITY.items()))
        medical_filter = tuple(sorted({**IDENTITY, "coverage": "Medical"}.items()))
        mapping = {
            ("coverage", identity_key): ["Medical"],
            ("population", medical_filter): ["No Subtype Specified", "n/a"],
            ("line", medical_filter): ["1L", "2L"],
        }

        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            side_effect=_distinct_from(mapping),
        ):
            state = build_cascade_state(IDENTITY)

        self.assertEqual(state["selections"]["coverage"], "Medical")
        self.assertEqual(state["options"]["population"], [])
        self.assertEqual(state["options"]["line"], ["1L", "2L"])
        self.assertEqual(state["pending_slots"], ["coverage", "line"])

    def test_confirmed_values_are_not_shown_as_pending(self) -> None:
        confirmed = {
            **IDENTITY,
            "coverage": "Medical",
            "population": "EGFR+",
            "line": "1L",
        }
        line_filter = tuple(
            sorted(
                {
                    **IDENTITY,
                    "coverage": "Medical",
                    "population": "EGFR+",
                }.items()
            )
        )
        medical_filter = tuple(sorted({**IDENTITY, "coverage": "Medical"}.items()))
        identity_key = tuple(sorted(IDENTITY.items()))
        mapping = {
            ("coverage", identity_key): ["Medical", "Pharmacy"],
            ("population", medical_filter): ["EGFR+", "ALK+"],
            ("line", line_filter): ["1L", "2L"],
        }

        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            side_effect=_distinct_from(mapping),
        ):
            state = build_cascade_state(confirmed)

        self.assertEqual(state["pending_slots"], [])
        self.assertEqual(state["selections"]["coverage"], "Medical")
        self.assertEqual(state["selections"]["population"], "EGFR+")
        self.assertEqual(state["selections"]["line"], "1L")
        self.assertEqual(state["allow_skip"], {"population": True, "line": True})

    def test_missing_coverage_checks_subtypes_for_each_coverage(self) -> None:
        identity_key = tuple(sorted(IDENTITY.items()))
        medical_filter = tuple(sorted({**IDENTITY, "coverage": "Medical"}.items()))
        pharmacy_filter = tuple(
            sorted({**IDENTITY, "coverage": "Pharmacy"}.items())
        )
        mapping = {
            ("coverage", identity_key): ["Medical", "Pharmacy"],
            ("line", identity_key): [],
            ("population", medical_filter): ["n/a"],
            ("population", pharmacy_filter): ["Biomarker+"],
        }

        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            side_effect=_distinct_from(mapping),
        ):
            state = build_cascade_state(IDENTITY)

        self.assertEqual(state["pending_slots"], ["coverage", "population"])


class CascadeNeededTests(unittest.TestCase):
    def test_missing_coverage_is_needed_when_options_exist(self) -> None:
        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            return_value=["Medical"],
        ) as distinct:
            self.assertTrue(cascade_needed(IDENTITY))

        distinct.assert_called_once_with("coverage", IDENTITY)

    def test_missing_population_ignores_only_skip_sentinels(self) -> None:
        confirmed = {**IDENTITY, "coverage": "Medical"}

        def fake(field, _filter):
            return {
                "population": ["No Subtype Specified", "n/a"],
                "line": [],
            }.get(field, [])

        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            side_effect=fake,
        ):
            self.assertFalse(cascade_needed(confirmed))

    def test_missing_population_with_real_subtype_is_needed(self) -> None:
        confirmed = {**IDENTITY, "coverage": "Medical"}
        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            side_effect=lambda field, _: ["EGFR+"] if field == "population" else [],
        ):
            self.assertTrue(cascade_needed(confirmed))

    def test_missing_line_is_scoped_by_confirmed_population(self) -> None:
        confirmed = {
            **IDENTITY,
            "coverage": "Medical",
            "population": "EGFR+",
        }
        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            side_effect=[["1L"]],
        ) as distinct:
            self.assertTrue(cascade_needed(confirmed))

        distinct.assert_called_once_with(
            "line",
            {**IDENTITY, "coverage": "Medical", "population": "EGFR+"},
        )

    def test_complete_cascade_needs_nothing(self) -> None:
        confirmed = {
            **IDENTITY,
            "coverage": "Medical",
            "population": "EGFR+",
            "line": "1L",
        }
        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values"
        ) as distinct:
            self.assertFalse(cascade_needed(confirmed))

        distinct.assert_not_called()


class AutoConfirmTests(unittest.TestCase):
    def test_auto_confirm_walks_all_sole_options_and_mutates_confirmed(self) -> None:
        identity_key = tuple(sorted(IDENTITY.items()))
        medical_filter = tuple(sorted({**IDENTITY, "coverage": "Medical"}.items()))
        population_filter = tuple(
            sorted(
                {
                    **IDENTITY,
                    "coverage": "Medical",
                    "population": "EGFR+",
                }.items()
            )
        )
        mapping = {
            ("coverage", identity_key): ["Medical"],
            ("population", medical_filter): ["EGFR+"],
            ("line", medical_filter): ["1L"],
            ("line", population_filter): ["1L"],
        }
        confirmed = dict(IDENTITY)

        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            side_effect=_distinct_from(mapping),
        ):
            selections = auto_confirm_sole_cascade_options(confirmed)

        self.assertEqual(
            {field: confirmed[field] for field in ("coverage", "population", "line")},
            {"coverage": "Medical", "population": "EGFR+", "line": "1L"},
        )
        self.assertEqual(
            selections,
            {"coverage": "Medical", "population": "EGFR+", "line": "1L"},
        )

    def test_auto_confirm_does_not_choose_among_multiple_options(self) -> None:
        identity_key = tuple(sorted(IDENTITY.items()))
        mapping = {
            ("coverage", identity_key): ["Medical", "Pharmacy"],
            ("line", identity_key): [],
            (
                "population",
                tuple(sorted({**IDENTITY, "coverage": "Medical"}.items())),
            ): [],
            (
                "population",
                tuple(sorted({**IDENTITY, "coverage": "Pharmacy"}.items())),
            ): [],
        }
        confirmed = dict(IDENTITY)

        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            side_effect=_distinct_from(mapping),
        ):
            selections = auto_confirm_sole_cascade_options(confirmed)

        self.assertNotIn("coverage", confirmed)
        self.assertEqual(
            selections,
            {"coverage": None, "population": None, "line": None},
        )

    def test_valid_user_selection_is_auto_confirmed_when_it_is_sole_option(self) -> None:
        identity_key = tuple(sorted(IDENTITY.items()))
        medical_filter = tuple(sorted({**IDENTITY, "coverage": "Medical"}.items()))
        mapping = {
            ("coverage", identity_key): ["Medical"],
            ("population", medical_filter): [],
            ("line", medical_filter): [],
        }
        confirmed = dict(IDENTITY)

        with patch(
            "chatbot.query_resolution.cascade_clarify.distinct_values",
            side_effect=_distinct_from(mapping),
        ):
            auto_confirm_sole_cascade_options(
                confirmed, {"coverage": " medical "}
            )

        self.assertEqual(confirmed["coverage"], "Medical")


if __name__ == "__main__":
    unittest.main()
