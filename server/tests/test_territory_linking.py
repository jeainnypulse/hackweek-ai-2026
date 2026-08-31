from __future__ import annotations

import unittest
from unittest.mock import patch

from chatbot.query_resolution.entity_linking import (
    TERRITORY_LINK_DIMENSION,
    load_synonyms_from_json,
    resolve_cascade,
)
from chatbot.query_resolution.linking import config
from chatbot.conversation.resolution import resolve_next_slot
from chatbot.slot_extract.lives_extract import (
    LIVES_DEFAULT_SOURCE,
    US_STATE_CODES,
    apply_lives_defaults,
    coerce_lives_territory_slot,
    extract_lives_slots,
    is_national_territory,
    normalize_territory,
    normalized_state_code,
    state_code_from_in_clause,
    territory_raw_from_slots,
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
        with patch(
            "chatbot.query_resolution.linking.synonyms.load_synonyms",
            return_value=self.synonyms,
        ):
            with patch.object(config, "USE_SYNONYM_TABLE", True):
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
            "linked": {
                "organization": "Aetna",
                "book": "Commercial",
                "source": "DRG",
            },
            "link_info": {
                "organization": {"status": "matched", "value": "Aetna"},
                "book": {"status": "matched", "value": "Commercial"},
            },
        }
        options = {
            "organization": ["Aetna"],
            "book": ["Commercial"],
            "territoryName": ["PR", "United States"],
        }
        with patch(
            "chatbot.conversation.resolution._distinct_for_session",
            side_effect=lambda _session, field, _filt: options[field],
        ):
            response = resolve_next_slot(session)
        self.assertEqual(response["status"], "needs_input")
        self.assertEqual(response["current_slot"], "territoryName")
        self.assertEqual(response["candidates"], ["PR", "United States"])

    def test_territory_raw_prefers_territory_name_and_ignores_null(self) -> None:
        self.assertEqual(
            territory_raw_from_slots(
                {"territoryName": " California ", "territory": "TX"}
            ),
            "California",
        )
        self.assertEqual(
            territory_raw_from_slots(
                {"territoryName": "null", "territory": "TX"}
            ),
            "TX",
        )
        self.assertIsNone(territory_raw_from_slots({}))

    def test_normalized_state_code_supports_names_codes_and_territories(self) -> None:
        cases = {
            "California": "CA",
            " ca ": "CA",
            "Puerto Rico": "PR",
            "pr": "PR",
            "District   of Columbia": "DC",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalized_state_code(raw), expected)
        for raw in (None, "", "null", "ZZ", "Atlantis"):
            with self.subTest(raw=raw):
                self.assertIsNone(normalized_state_code(raw))

    def test_national_territory_recognizes_supported_aliases(self) -> None:
        for raw in ("US", "u.s.", "USA", "United States", "National", "Nationwide"):
            with self.subTest(raw=raw):
                self.assertTrue(is_national_territory(raw))
        for raw in (None, "California", "nationally"):
            with self.subTest(raw=raw):
                self.assertFalse(is_national_territory(raw))

    def test_state_code_fallback_requires_explicit_in_clause(self) -> None:
        self.assertEqual(
            state_code_from_in_clause("Aetna commercial lives in California"),
            "CA",
        )
        self.assertEqual(
            state_code_from_in_clause("Aetna commercial lives in tx"),
            "TX",
        )
        self.assertIsNone(
            state_code_from_in_clause("California payer enrollment")
        )
        self.assertIsNone(
            state_code_from_in_clause("Aetna enrollment in Los Angeles")
        )
        self.assertIsNone(
            state_code_from_in_clause("Aetna enrollment in the Midwest")
        )

    def test_coerce_explicit_national_overrides_extracted_state(self) -> None:
        slots = {"territoryName": "New Jersey", "territoryInvalid": "Midwest"}
        coerce_lives_territory_slot(
            "Horizon New Jersey enrollment nationwide",
            slots,
        )
        self.assertEqual(
            slots,
            {
                "territoryName": None,
                "territory": None,
                "territoryInvalid": None,
            },
        )

    def test_coerce_region_preserves_invalid_value_for_clarification(self) -> None:
        slots = {"territoryName": None, "territoryInvalid": None}
        coerce_lives_territory_slot(
            "Aetna commercial lives in the Midwest",
            slots,
        )
        self.assertIsNone(slots["territoryName"])
        self.assertEqual(slots["territoryInvalid"], "midwest")

    def test_coerce_uses_state_in_clause_as_fallback(self) -> None:
        slots = {"territoryName": None, "territoryInvalid": None}
        coerce_lives_territory_slot(
            "Aetna commercial lives in California",
            slots,
        )
        self.assertEqual(slots["territoryName"], "CA")
        self.assertEqual(slots["territory"], "CA")
        self.assertIsNone(slots["territoryInvalid"])

    def test_normalize_territory_defaults_unknown_values_to_national(self) -> None:
        self.assertEqual(
            normalize_territory(None),
            ("National", "United States"),
        )
        self.assertEqual(
            normalize_territory("United States"),
            ("National", "United States"),
        )
        self.assertEqual(normalize_territory("California"), ("U.S. State", "CA"))
        self.assertEqual(
            normalize_territory("Atlantis"),
            ("National", "United States"),
        )

    def test_apply_lives_defaults_uses_linked_territory_and_coverage(self) -> None:
        slots = {
            "coverage": "Pharmacy",
            "territoryName": "California",
            "territoryInvalid": None,
        }
        linked = {
            "territoryName": "CA",
            "territory": "California",
            "territoryInvalid": "old",
        }
        apply_lives_defaults(slots, linked, territory_type="U.S. State")
        self.assertEqual(
            linked,
            {
                "territoryType": "U.S. State",
                "territoryName": "CA",
                "source": LIVES_DEFAULT_SOURCE,
                "coverage": "Pharmacy",
            },
        )

    def test_apply_lives_defaults_preserves_linked_coverage(self) -> None:
        slots = {"coverage": "Pharmacy", "territoryName": None}
        linked = {"coverage": "Medical"}
        apply_lives_defaults(slots, linked)
        self.assertEqual(linked["coverage"], "Medical")
        self.assertEqual(linked["territoryType"], "National")

    def test_extract_lives_slots_coerces_mocked_llm_result(self) -> None:
        extracted = {
            "organization": "Aetna",
            "book": "Commercial",
            "coverage": "Medical",
            "territoryName": None,
            "territoryInvalid": None,
        }
        with patch(
            "chatbot.slot_extract.lives_extract.invoke_json_extract",
            return_value=extracted,
        ) as invoke:
            result = extract_lives_slots(
                "How many Aetna commercial medical lives are in CA?"
            )

        self.assertIs(result, extracted)
        self.assertEqual(result["territoryName"], "CA")
        invoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()
