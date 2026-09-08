import unittest
from unittest.mock import patch

import main
from chatbot.synthesis import synthesis


class CanonicalSynthesisTests(unittest.TestCase):
    def test_prompt_prefers_confirmed_canonical_slots(self):
        records = [
            {
                "collection": "lives",
                "organization": "Aetna",
                "book": "Medicare",
                "coverage": "Pharmacy",
                "territoryName": "CA",
                "lives": 431_233,
            }
        ]
        confirmed = {
            "organization": "Aetna",
            "regimen": "Tregzi",
            "book": "Medicare",
            "coverage": "Pharmacy",
            "territoryName": "CA",
        }

        with patch("chatbot.synthesis.synthesis.invoke_claude", return_value="Canonical answer.") as invoke:
            synthesis.synthesize_answer(
                "Is Keytrudax covered in Califorxnia?",
                records,
                intent="joint",
                confirmed_slots=confirmed,
            )

        prompt = invoke.call_args.args[0]
        self.assertIn('"regimen": "Tregzi"', prompt)
        self.assertIn('"territoryName": "CA"', prompt)
        self.assertIn("instead of aliases", prompt)

    def test_finalize_forwards_confirmed_slots_to_synthesis(self):
        confirmed = {"regimen": "Tregzi", "territoryName": "CA"}

        with patch(
            "main.synthesize_answer",
            return_value=("Canonical answer.", []),
        ) as synthesize:
            main._finalize_ask(
                "Original misspelled question",
                [{"collection": "access"}],
                True,
                confirmed_slots=confirmed,
                intent="joint",
            )

        synthesize.assert_called_once_with(
            "Original misspelled question",
            [{"collection": "access"}],
            intent="joint",
            confirmed_slots=confirmed,
        )


class SynthesisHelperTests(unittest.TestCase):
    def test_clean_answer_removes_common_markdown(self):
        raw = """# Result
> **Aetna** has *restricted* access.
---
- Source included.


Done."""
        self.assertEqual(
            synthesis.clean_answer(raw),
            "Result\nAetna has restricted access.\n\nSource included.\n\nDone.",
        )

    def test_lives_record_detection_prefers_collection_tag(self):
        self.assertTrue(synthesis._is_lives_record({"collection": "lives"}))
        self.assertFalse(
            synthesis._is_lives_record(
                {"collection": "access", "lives": 100}
            )
        )
        self.assertTrue(synthesis._is_lives_record({"lives": 100}))
        self.assertFalse(
            synthesis._is_lives_record(
                {"lives": 100, "accessData": {"access": "Restricted"}}
            )
        )

    def test_access_citation_omits_missing_fields(self):
        self.assertEqual(
            synthesis._citation_for_record(
                {
                    "organization": "Aetna",
                    "regimen": "Tregzi",
                    "book": "Medicare",
                    "accessData": {"access": "Restricted"},
                },
                lives=False,
            ),
            "Aetna · Tregzi · Medicare · Restricted",
        )
        self.assertIsNone(synthesis._citation_for_record({}, lives=False))

    def test_lives_citation_formats_numeric_and_non_numeric_counts(self):
        self.assertEqual(
            synthesis._citation_for_record(
                {
                    "organization": "Aetna",
                    "book": "Commercial",
                    "coverage": "Medical",
                    "territoryName": "CA",
                    "lives": 1234567.8,
                },
                lives=True,
            ),
            "Aetna · Commercial · Medical · CA · 1,234,567 lives",
        )
        self.assertEqual(
            synthesis._citation_for_record({"lives": "unknown"}, lives=True),
            "unknown lives",
        )


class SynthesizeAnswerTests(unittest.TestCase):
    def test_empty_access_records_return_guidance_without_bedrock(self):
        with patch("chatbot.synthesis.synthesis.invoke_claude") as invoke:
            answer, citations = synthesis.synthesize_answer(
                "Tregzi access?",
                [],
            )
        self.assertIn("No matching records", answer)
        self.assertIn("regimen name", answer)
        self.assertEqual(citations, [])
        invoke.assert_not_called()

    def test_empty_lives_and_joint_records_have_specific_guidance(self):
        with patch("chatbot.synthesis.synthesis.invoke_claude") as invoke:
            lives_answer, lives_citations = synthesis.synthesize_answer(
                "Aetna enrollment?",
                [],
                intent="lives",
            )
            joint_answer, joint_citations = synthesis.synthesize_answer(
                "Aetna access and enrollment?",
                [],
                intent="joint",
            )

        self.assertIn("No matching lives records", lives_answer)
        self.assertIn("No matching access or lives records", joint_answer)
        self.assertEqual(lives_citations, [])
        self.assertEqual(joint_citations, [])
        invoke.assert_not_called()

    def test_lives_records_select_lives_prompt_and_deduplicate_citations(self):
        record = {
            "organization": "Aetna",
            "book": "Commercial",
            "coverage": "Pharmacy",
            "territoryName": "United States",
            "lives": 5685473,
        }
        with patch(
            "chatbot.synthesis.synthesis.invoke_claude",
            return_value="**Aetna** has 5,685,473 lives.",
        ) as invoke:
            answer, citations = synthesis.synthesize_answer(
                "How many lives?",
                [record, dict(record)],
            )

        self.assertEqual(answer, "Aetna has 5,685,473 lives.")
        self.assertEqual(
            citations,
            [
                "Aetna · Commercial · Pharmacy · United States · "
                "5,685,473 lives"
            ],
        )
        prompt = invoke.call_args.args[0]
        self.assertIn("State the lives count and percent directly", prompt)
        self.assertEqual(invoke.call_args.kwargs["system"], synthesis.LIVES_SYSTEM_PROMPT)
        self.assertEqual(invoke.call_args.kwargs["max_tokens"], 150)

    def test_access_records_select_access_prompt_and_citation(self):
        record = {
            "collection": "access",
            "organization": "Aetna",
            "regimen": "Tregzi",
            "indication": "Hematologic malignancies",
            "line": "1L",
            "book": "Medicare",
            "accessData": {"access": "Restricted"},
        }
        with patch(
            "chatbot.synthesis.synthesis.invoke_claude",
            return_value="# Tregzi is Restricted.",
        ) as invoke:
            answer, citations = synthesis.synthesize_answer(
                "Tregzi access?",
                [record],
                intent="access",
            )

        self.assertEqual(answer, "Tregzi is Restricted.")
        self.assertEqual(
            citations,
            [
                "Aetna · Tregzi · Hematologic malignancies · 1L · "
                "Medicare · Restricted"
            ],
        )
        self.assertEqual(
            invoke.call_args.kwargs["system"],
            synthesis.ACCESS_SYSTEM_PROMPT,
        )
        self.assertIn(
            "Copy the value from accessData.access verbatim",
            invoke.call_args.kwargs["system"],
        )
        self.assertEqual(invoke.call_args.kwargs["max_tokens"], 150)

    def test_joint_records_use_per_record_citation_types(self):
        access = {
            "collection": "access",
            "organization": "Aetna",
            "regimen": "Tregzi",
            "book": "Medicare",
            "accessData": {"access": "Not Reviewed"},
        }
        lives = {
            "collection": "lives",
            "organization": "Aetna",
            "book": "Medicare",
            "coverage": "Pharmacy",
            "territoryName": "United States",
            "lives": 1000,
        }
        with patch(
            "chatbot.synthesis.synthesis.invoke_claude",
            return_value="Access sentence. Lives sentence.",
        ) as invoke:
            answer, citations = synthesis.synthesize_answer(
                "Access and lives?",
                [access, lives],
                intent="joint",
                confirmed_slots={"organization": "Aetna"},
            )

        self.assertEqual(answer, "Access sentence. Lives sentence.")
        self.assertEqual(
            citations,
            [
                "Aetna · Tregzi · Medicare · Not Reviewed",
                "Aetna · Medicare · Pharmacy · United States · 1,000 lives",
            ],
        )
        prompt = invoke.call_args.args[0]
        self.assertIn("Access fact first, then lives fact", prompt)
        self.assertIn("Copy accessData.access verbatim", prompt)
        self.assertIn('"organization": "Aetna"', prompt)
        self.assertEqual(
            invoke.call_args.kwargs["system"],
            synthesis.JOINT_SYSTEM_PROMPT,
        )
        self.assertEqual(invoke.call_args.kwargs["max_tokens"], 220)

    def test_access_answer_omits_score_from_context(self):
        record = {
            "collection": "access",
            "organization": "Aetna",
            "regimen": "Tregzi",
            "accessData": {"access": "Restricted", "score": 30},
        }
        with patch(
            "chatbot.synthesis.synthesis.invoke_claude",
            return_value="Tregzi is Restricted at Aetna.",
        ) as invoke:
            answer, _ = synthesis.synthesize_answer(
                "What is Tregzi access at Aetna?",
                [record],
                intent="access",
            )

        self.assertEqual(answer, "Tregzi is Restricted at Aetna.")
        prompt = invoke.call_args.args[0]
        self.assertNotIn('"score"', prompt)
        self.assertIn("verbatim without interpreting it", prompt)

    def test_joint_context_omits_access_score_but_keeps_lives_count(self):
        records = [
            {
                "collection": "access",
                "organization": "Aetna",
                "regimen": "Tregzi",
                "accessData": {"access": "Restricted", "score": 30},
            },
            {
                "collection": "lives",
                "organization": "Aetna",
                "book": "Medicare",
                "coverage": "Pharmacy",
                "lives": 1000,
            },
        ]
        with patch(
            "chatbot.synthesis.synthesis.invoke_claude",
            return_value=(
                "Tregzi is Restricted at Aetna. "
                "Aetna has 1,000 Medicare pharmacy lives."
            ),
        ) as invoke:
            answer, _ = synthesis.synthesize_answer(
                "What is access and lives?",
                records,
                intent="joint",
            )

        self.assertEqual(
            answer,
            "Tregzi is Restricted at Aetna. "
            "Aetna has 1,000 Medicare pharmacy lives.",
        )
        prompt = invoke.call_args.args[0]
        self.assertNotIn('"score"', prompt)
        self.assertIn('"lives": 1000', prompt)


if __name__ == "__main__":
    unittest.main()
