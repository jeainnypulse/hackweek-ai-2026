from __future__ import annotations

import json
import unittest
from unittest.mock import call, patch

from chatbot.retrieval import extract_utils, intent


class ExtractUtilsTests(unittest.TestCase):
    def test_parse_llm_json_accepts_plain_and_fenced_json(self) -> None:
        expected = {"organization": "Aetna"}
        self.assertEqual(extract_utils.parse_llm_json(json.dumps(expected)), expected)
        self.assertEqual(
            extract_utils.parse_llm_json(
                '```json\n{"organization": "Aetna"}\n```'
            ),
            expected,
        )

    def test_parse_llm_json_extracts_object_from_surrounding_text(self) -> None:
        self.assertEqual(
            extract_utils.parse_llm_json('Result: {"book": "Medicare"} done'),
            {"book": "Medicare"},
        )

    def test_parse_llm_json_propagates_invalid_json(self) -> None:
        for raw in ("not json", "prefix {bad} suffix"):
            with self.subTest(raw=raw), self.assertRaises(json.JSONDecodeError):
                extract_utils.parse_llm_json(raw)

    def test_invoke_json_extract_builds_prompt_and_returns_object(self) -> None:
        with patch(
            "chatbot.retrieval.extract_utils.invoke_claude",
            return_value='{"coverage": "Pharmacy"}',
        ) as invoke:
            result = extract_utils.invoke_json_extract(
                "Extract: ",
                "Aetna lives",
                max_tokens=77,
            )

        self.assertEqual(result, {"coverage": "Pharmacy"})
        invoke.assert_called_once_with("Extract: Aetna lives", max_tokens=77)

    def test_invoke_json_extract_rejects_non_object_json(self) -> None:
        with patch(
            "chatbot.retrieval.extract_utils.invoke_claude",
            return_value='["Aetna"]',
        ):
            with self.assertRaisesRegex(ValueError, "JSON object"):
                extract_utils.invoke_json_extract("prompt", "question")

    def test_slot_text_normalizes_missing_and_present_values(self) -> None:
        for value in (None, "", " ", "null", " NULL "):
            with self.subTest(value=value):
                self.assertIsNone(extract_utils.slot_text(value))
        self.assertEqual(extract_utils.slot_text("  Aetna  "), "Aetna")
        self.assertEqual(extract_utils.slot_text(42), "42")


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
            "chatbot.retrieval.intent.invoke_claude",
            return_value='{"intent": "lives"}',
        ) as invoke:
            self.assertEqual(intent.classify_intent("Aetna enrollment?"), "lives")

        invoke.assert_called_once_with(
            intent.CLASSIFY_PROMPT + "Aetna enrollment?",
            max_tokens=160,
        )

    def test_classify_intent_retries_one_parse_failure(self) -> None:
        with patch(
            "chatbot.retrieval.intent.invoke_claude",
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

    def test_classify_intent_returns_out_of_scope_after_two_parse_failures(self) -> None:
        with patch(
            "chatbot.retrieval.intent.invoke_claude",
            side_effect=['{"intent": "unknown"}', "invalid"],
        ) as invoke:
            self.assertEqual(intent.classify_intent("unclear"), "out_of_scope")
        self.assertEqual(invoke.call_count, 2)

    def test_classify_intent_propagates_bedrock_failures_without_retry(self) -> None:
        with patch(
            "chatbot.retrieval.intent.invoke_claude",
            side_effect=RuntimeError("Bedrock unavailable"),
        ) as invoke:
            with self.assertRaisesRegex(RuntimeError, "Bedrock unavailable"):
                intent.classify_intent("Aetna access?")
        invoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()
