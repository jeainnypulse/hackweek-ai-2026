from __future__ import annotations

import unittest
from unittest.mock import patch

from entity_linking import (
    TERRITORY_LINK_DIMENSION,
    load_synonyms_from_json,
    resolve_cascade,
)
from retrieval.exact_match import _territory_clarify_response
from retrieval.lives_extract import (
    LIVES_TERRITORY_OPTIONS,
    US_STATE_CODES,
    apply_lives_defaults,
    coerce_lives_territory_slot,
)


class TerritoryLinkingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.synonyms = load_synonyms_from_json()
        cls.catalogs = {
            TERRITORY_LINK_DIMENSION: sorted(
                {*US_STATE_CODES, "United States"}
            )
        }

    def resolve(self, mention: str) -> dict:
        with patch("entity_linking.load_synonyms", return_value=self.synonyms):
            with patch("entity_linking.USE_SYNONYM_TABLE", True):
                return resolve_cascade(
                    TERRITORY_LINK_DIMENSION,
                    mention,
                    self.catalogs,
                )

    def test_all_supported_lives_territories_have_aliases(self) -> None:
        canonicals = set(self.synonyms[TERRITORY_LINK_DIMENSION].values())
        self.assertEqual(canonicals, {*US_STATE_CODES, "United States"})

    def test_exact_name_and_code(self) -> None:
        self.assertEqual(self.resolve("California")["value"], "CA")
        self.assertEqual(self.resolve("CA")["value"], "CA")
        self.assertEqual(self.resolve("Puerto Rico")["value"], "PR")

    def test_clear_misspelling_uses_fuzzy_alias(self) -> None:
        result = self.resolve("Californa")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["value"], "CA")
        self.assertEqual(result["method"], "cascade:fuzzy_synonym")

    def test_ambiguous_carolina_requires_clarification(self) -> None:
        result = self.resolve("Carolina")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["candidates"][:2], ["NC", "SC"])

    def test_missing_geography_remains_national(self) -> None:
        slots = {"territoryName": None, "territoryInvalid": None}
        coerce_lives_territory_slot("How many lives does Aetna have?", slots)
        linked = {}
        apply_lives_defaults(slots, linked)
        self.assertEqual(linked["territoryType"], "National")
        self.assertEqual(linked["territoryName"], "United States")

    def test_raw_state_is_preserved_for_linking(self) -> None:
        slots = {"territoryName": "California", "territoryInvalid": None}
        coerce_lives_territory_slot("Aetna California enrollment", slots)
        self.assertEqual(slots["territoryName"], "California")

    def test_payer_name_state_does_not_override_national(self) -> None:
        slots = {"territoryName": "New Jersey", "territoryInvalid": None}
        coerce_lives_territory_slot(
            "Horizon BlueCross BlueShield New Jersey nationally",
            slots,
        )
        self.assertIsNone(slots["territoryName"])
        self.assertIsNone(slots["territoryInvalid"])

    def test_city_routes_to_manual_clarification(self) -> None:
        slots = {"territoryName": None, "territoryInvalid": "Los Angeles"}
        coerce_lives_territory_slot("Aetna lives in Los Angeles", slots)
        session = {
            "session_id": "test",
            "question": "Aetna lives in Los Angeles",
            "intent": "lives",
            "slots": slots,
            "linked": {},
            "link_info": {},
        }
        response = _territory_clarify_response(session)
        self.assertEqual(response["status"], "needs_input")
        self.assertIn("National", response["candidates"])
        self.assertIn("Puerto Rico", response["candidates"])
        self.assertEqual(
            tuple(response["candidates"]),
            LIVES_TERRITORY_OPTIONS,
        )


if __name__ == "__main__":
    unittest.main()
