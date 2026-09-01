from __future__ import annotations

import unittest

from evaluation.orchestration import compare_orchestration_result
from evaluation.generate_decision_path_eval import build_rows


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


if __name__ == "__main__":
    unittest.main()
