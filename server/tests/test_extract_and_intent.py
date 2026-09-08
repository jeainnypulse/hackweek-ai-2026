from __future__ import annotations

import json
import unittest
from unittest.mock import call, patch

from chatbot import intent_classifier as intent
from chatbot.slot_extract import access_extract
from chatbot.slot_extract import utils


class AccessExtractGroundingTests(unittest.TestCase):
    def test_exact_question_regimen_overrides_conflicting_llm_value(self) -> None:
        catalogs = {"regimen": ["Tregzi", "Tecvayli"]}
        with (
            patch.object(
                access_extract,
                "invoke_json_extract",
                return_value={"regimen": "Tecvayli"},
            ),
            patch.object(
                access_extract,
                "load_catalogs_from_db",
                return_value=catalogs,
            ),
        ):
            slots = access_extract.extract_access_slots(
                "Is Tregzi unrestricted at Aetna?"
            )

        self.assertEqual(slots["regimen"], "Tregzi")

    def test_longest_exact_regimen_wins_over_contained_name(self) -> None:
        catalogs = {"regimen": ["Keytruda", "Keytruda Qlex"]}

        result = access_extract._exact_catalog_value_from_question(
            "What is the access for Keytruda Qlex?",
            "regimen",
            catalogs,
        )

        self.assertEqual(result, "Keytruda Qlex")

    def test_multiple_distinct_regimen_mentions_are_ambiguous(self) -> None:
        catalogs = {"regimen": ["Tregzi", "Tecvayli"]}

        result = access_extract._exact_catalog_value_from_question(
            "Compare Tregzi and Tecvayli",
            "regimen",
            catalogs,
        )

        self.assertIsNone(result)

    def test_regimen_match_requires_token_boundaries(self) -> None:
        catalogs = {"regimen": ["Tregzi"]}

        result = access_extract._exact_catalog_value_from_question(
            "Is Notregzi covered?",
            "regimen",
            catalogs,
        )

        self.assertIsNone(result)

    def test_existing_llm_value_is_preserved_without_exact_match(self) -> None:
        catalogs = {"regimen": ["Tregzi", "Tecvayli"]}
        with (
            patch.object(
                access_extract,
                "invoke_json_extract",
                return_value={"regimen": "Tregzi"},
            ),
            patch.object(
                access_extract,
                "load_catalogs_from_db",
                return_value=catalogs,
            ),
        ):
            slots = access_extract.extract_access_slots(
                "Is Tregzy unrestricted at Aetna?"
            )

        self.assertEqual(slots["regimen"], "Tregzi")

    def test_exact_question_book_overrides_conflicting_llm_value(self) -> None:
        catalogs = {
            "regimen": ["Tregzi"],
            "book": ["MAC", "Medicare"],
        }
        with (
            patch.object(
                access_extract,
                "invoke_json_extract",
                return_value={"regimen": "Tregzi", "book": "Medicare"},
            ),
            patch.object(
                access_extract,
                "load_catalogs_from_db",
                return_value=catalogs,
            ),
        ):
            slots = access_extract.extract_access_slots(
                "How does CGS cover Tregzi under MAC?"
            )

        self.assertEqual(slots["book"], "MAC")

    def test_longest_exact_book_wins_over_contained_name(self) -> None:
        catalogs = {"book": ["Medicaid", "FFS Medicaid"]}

        result = access_extract._exact_catalog_value_from_question(
            "Tregzi access for State of Alabama FFS Medicaid",
            "book",
            catalogs,
        )

        self.assertEqual(result, "FFS Medicaid")

    def test_short_exact_token_does_not_replace_longer_extracted_book(self) -> None:
        catalogs = {
            "regimen": ["Tregzi"],
            "book": ["Medicaid", "Managed Medicaid"],
        }
        with (
            patch.object(
                access_extract,
                "invoke_json_extract",
                return_value={
                    "regimen": "Tregzi",
                    "book": "Managed Medicaid",
                },
            ),
            patch.object(
                access_extract,
                "load_catalogs_from_db",
                return_value=catalogs,
            ),
        ):
            slots = access_extract.extract_access_slots(
                "Tregzi access on managd medicaid"
            )

        self.assertEqual(slots["book"], "Managed Medicaid")

    def test_unsupported_longer_book_is_replaced_by_exact_question_book(self) -> None:
        catalogs = {
            "regimen": ["Tregzi"],
            "book": ["Medicaid", "Managed Medicaid"],
        }
        with (
            patch.object(
                access_extract,
                "invoke_json_extract",
                return_value={
                    "regimen": "Tregzi",
                    "book": "Managed Medicaid",
                },
            ),
            patch.object(
                access_extract,
                "load_catalogs_from_db",
                return_value=catalogs,
            ),
        ):
            slots = access_extract.extract_access_slots(
                "Tregzi access at the State of Nevada Medicaid program"
            )

        self.assertEqual(slots["book"], "Medicaid")

    def test_extracted_medicaid_suffix_is_removed_from_exact_payer(self) -> None:
        catalogs = {
            "organization": ["State of Nevada"],
            "book": ["FFS Medicaid"],
        }

        result = access_extract._organization_without_book_suffix(
            "State of Nevada Medicaid",
            "Medicaid",
            catalogs,
        )

        self.assertEqual(result, "State of Nevada")

    def test_catalog_book_suffix_is_removed_from_exact_payer(self) -> None:
        catalogs = {
            "organization": ["State of Alabama"],
            "book": ["FFS Medicaid"],
        }

        result = access_extract._organization_without_book_suffix(
            "State of Alabama FFS Medicaid",
            "Medicaid",
            catalogs,
        )

        self.assertEqual(result, "State of Alabama")

    def test_longest_book_suffix_is_tried_first(self) -> None:
        catalogs = {
            "organization": ["State of Alabama", "State of Alabama FFS"],
            "book": ["FFS Medicaid"],
        }

        result = access_extract._organization_without_book_suffix(
            "State of Alabama FFS Medicaid",
            "Medicaid",
            catalogs,
        )

        self.assertEqual(result, "State of Alabama")

    def test_book_suffix_is_preserved_when_payer_remainder_is_unknown(self) -> None:
        catalogs = {
            "organization": ["State of Nevada"],
            "book": ["FFS Medicaid"],
        }

        result = access_extract._organization_without_book_suffix(
            "Unknown State Medicaid",
            "Medicaid",
            catalogs,
        )

        self.assertIsNone(result)

    def test_ordinary_payer_name_is_not_changed(self) -> None:
        catalogs = {
            "organization": ["Molina Healthcare"],
            "book": ["Managed Medicaid"],
        }

        result = access_extract._organization_without_book_suffix(
            "Molina Healthcare",
            "Managed Medicaid",
            catalogs,
        )

        self.assertIsNone(result)


class ExtractUtilsTests(unittest.TestCase):
    def test_parse_llm_json_accepts_plain_and_fenced_json(self) -> None:
        expected = {"organization": "Aetna"}
        self.assertEqual(utils.parse_llm_json(json.dumps(expected)), expected)
        self.assertEqual(
            utils.parse_llm_json('```json\n{"organization": "Aetna"}\n```'),
            expected,
        )

    def test_parse_llm_json_extracts_object_from_surrounding_text(self) -> None:
        self.assertEqual(
            utils.parse_llm_json('Result: {"book": "Medicare"} done'),
            {"book": "Medicare"},
        )

    def test_parse_llm_json_propagates_invalid_json(self) -> None:
        for raw in ("not json", "prefix {bad} suffix"):
            with self.subTest(raw=raw), self.assertRaises(json.JSONDecodeError):
                utils.parse_llm_json(raw)

    def test_invoke_json_extract_builds_prompt_and_returns_object(self) -> None:
        with patch(
            "chatbot.slot_extract.utils.invoke_claude",
            return_value='{"coverage": "Pharmacy"}',
        ) as invoke:
            result = utils.invoke_json_extract(
                "Extract: ",
                "Aetna lives",
                max_tokens=77,
            )

        self.assertEqual(result, {"coverage": "Pharmacy"})
        invoke.assert_called_once_with("Extract: Aetna lives", max_tokens=77)

    def test_invoke_json_extract_rejects_non_object_json(self) -> None:
        with patch(
            "chatbot.slot_extract.utils.invoke_claude",
            return_value='["Aetna"]',
        ):
            with self.assertRaisesRegex(ValueError, "JSON object"):
                utils.invoke_json_extract("prompt", "question")

    def test_slot_text_normalizes_missing_and_present_values(self) -> None:
        for value in (None, "", " ", "null", " NULL "):
            with self.subTest(value=value):
                self.assertIsNone(utils.slot_text(value))
        self.assertEqual(utils.slot_text("  Aetna  "), "Aetna")
        self.assertEqual(utils.slot_text(42), "42")


class IntentTests(unittest.TestCase):
    def test_parse_intent_accepts_json_fences_surrounding_text_and_case(self) -> None:
        cases = {
            '```json\n{"intent": "ACCESS"}\n```': "access",
            'classification: {"intent": "Lives"} complete': "lives",
            '{"intent": "out of scope"}': "out_of_scope",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(intent._parse_intent(raw), expected)

    def test_parse_intent_normalizes_supported_aliases(self) -> None:
        aliases = {
            "qoa": "access",
            "coverage": "access",
            "payerLatestAccess": "access",
            "enrollment": "lives",
            "payerLatestLives": "lives",
            "both": "joint",
            "access_and_lives": "joint",
            "access+lives": "joint",
            "oos": "out_of_scope",
            "out-of-scope": "out_of_scope",
            "unsure": "out_of_scope",
        }
        for alias, expected in aliases.items():
            with self.subTest(alias=alias):
                self.assertEqual(
                    intent._parse_intent(json.dumps({"intent": alias})),
                    expected,
                )

    def test_parse_intent_rejects_missing_unknown_and_malformed_values(self) -> None:
        invalid = ('{"reason": "none"}', '{"intent": "weather"}', "not json")
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(
                (ValueError, json.JSONDecodeError)
            ):
                intent._parse_intent(raw)

    def test_classify_intent_returns_first_valid_response(self) -> None:
        with patch(
            "chatbot.intent_classifier.invoke_claude",
            return_value='{"intent": "lives"}',
        ) as invoke:
            self.assertEqual(intent.classify_intent("Aetna enrollment?"), "lives")

        invoke.assert_called_once_with(
            intent.CLASSIFY_PROMPT + "Aetna enrollment?",
            max_tokens=160,
        )

    def test_classify_intent_retries_one_parse_failure(self) -> None:
        with patch(
            "chatbot.intent_classifier.invoke_claude",
            side_effect=["invalid", '{"intent": "joint"}'],
        ) as invoke:
            self.assertEqual(intent.classify_intent("access and lives"), "joint")

        self.assertEqual(
            invoke.call_args_list,
            [
                call(intent.CLASSIFY_PROMPT + "access and lives", max_tokens=160),
                call(intent.CLASSIFY_PROMPT + "access and lives", max_tokens=160),
            ],
        )

    def test_classify_intent_returns_out_of_scope_after_two_parse_failures(
        self,
    ) -> None:
        with patch(
            "chatbot.intent_classifier.invoke_claude",
            side_effect=['{"intent": "unknown"}', "invalid"],
        ) as invoke:
            self.assertEqual(intent.classify_intent("unclear"), "out_of_scope")
        self.assertEqual(invoke.call_count, 2)

    def test_classify_intent_propagates_bedrock_failures_without_retry(self) -> None:
        with patch(
            "chatbot.intent_classifier.invoke_claude",
            side_effect=RuntimeError("Bedrock unavailable"),
        ) as invoke:
            with self.assertRaisesRegex(RuntimeError, "Bedrock unavailable"):
                intent.classify_intent("Aetna access?")
        invoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()
