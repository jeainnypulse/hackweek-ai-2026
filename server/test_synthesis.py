import unittest
from unittest.mock import patch

import main
import synthesis


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

        with patch("synthesis.invoke_claude", return_value="Canonical answer.") as invoke:
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
                "exact_match_entity_linker",
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


if __name__ == "__main__":
    unittest.main()
