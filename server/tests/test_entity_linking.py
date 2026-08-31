from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from chatbot.query_resolution.entity_linking import (
    TERRITORY_LINK_DIMENSION,
    link_slots,
    load_catalogs_from_db,
    resolve_cascade,
    resolve_hypothesize_verify,
)
from chatbot.query_resolution.linking import config


class CascadeResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalogs = {
            "organization": ["Anthem", "Anthem (Elevance Health)", "Aetna"],
            "regimen": ["Keytruda", "Keytruda (IV)", "Tregzi"],
            "coverage": ["Medical", "Pharmacy"],
            TERRITORY_LINK_DIMENSION: ["CA", "NC", "SC", "United States"],
        }

    def test_synonym_precedes_literal_catalog_match(self) -> None:
        synonyms = {"organization": {"anthem": "Anthem (Elevance Health)"}}
        with (
            patch.object(config, "USE_SYNONYM_TABLE", True),
            patch(
                "chatbot.query_resolution.linking.synonyms.load_synonyms",
                return_value=synonyms,
            ),
        ):
            result = resolve_cascade("organization", "Anthem", self.catalogs)

        self.assertEqual(
            result,
            {
                "status": "matched",
                "value": "Anthem (Elevance Health)",
                "method": "cascade:synonym",
            },
        )

    def test_synonym_target_not_in_catalog_is_still_returned(self) -> None:
        synonyms = {"organization": {"legacy payer": "Legacy Payer"}}
        with (
            patch.object(config, "USE_SYNONYM_TABLE", True),
            patch(
                "chatbot.query_resolution.linking.synonyms.load_synonyms",
                return_value=synonyms,
            ),
        ):
            result = resolve_cascade("organization", "legacy payer", self.catalogs)

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["value"], "Legacy Payer")
        self.assertEqual(result["method"], "cascade:synonym")

    def test_catalog_match_uses_canonical_casing_without_llm(self) -> None:
        with (
            patch.object(config, "USE_SYNONYM_TABLE", False),
            patch(
                "chatbot.query_resolution.linking.hypothesis.resolve_hypothesize_verify"
            ) as llm_resolver,
        ):
            result = resolve_cascade("coverage", " medical ", self.catalogs)

        self.assertEqual(result["value"], "Medical")
        self.assertEqual(result["method"], "cascade:catalog")
        llm_resolver.assert_not_called()

    def test_verified_llm_match_is_labeled_as_cascade_stage(self) -> None:
        hyp_result = {
            "status": "matched",
            "value": "Tregzi",
            "method": "hypothesize_verify",
        }
        with (
            patch.object(config, "USE_SYNONYM_TABLE", False),
            patch(
                "chatbot.query_resolution.linking.hypothesis.resolve_hypothesize_verify",
                return_value=hyp_result,
            ),
        ):
            result = resolve_cascade("regimen", "tregzib", self.catalogs)

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["value"], "Tregzi")
        self.assertEqual(result["method"], "cascade:hypothesize_verify")

    def test_ambiguous_llm_result_keeps_only_verified_candidates(self) -> None:
        with (
            patch.object(config, "USE_SYNONYM_TABLE", False),
            patch(
                "chatbot.query_resolution.linking.hypothesis.resolve_hypothesize_verify",
                return_value={
                    "status": "ambiguous",
                    "candidates": ["Keytruda", "Keytruda (IV)"],
                    "method": "hypothesize_verify",
                },
            ),
        ):
            result = resolve_cascade("regimen", "pembro", self.catalogs)

        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["candidates"], ["Keytruda", "Keytruda (IV)"])
        self.assertEqual(result["method"], "cascade:clarify")

    def test_unresolved_mention_falls_back_to_sorted_full_catalog(self) -> None:
        with (
            patch.object(config, "USE_SYNONYM_TABLE", False),
            patch(
                "chatbot.query_resolution.linking.hypothesis.resolve_hypothesize_verify",
                return_value={
                    "status": "unknown",
                    "method": "hypothesize_verify",
                },
            ),
        ):
            result = resolve_cascade("coverage", "benefit", self.catalogs)

        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["candidates"], ["Medical", "Pharmacy"])
        self.assertEqual(result["method"], "cascade:clarify")

    def test_unresolved_mention_with_empty_catalog_is_unknown(self) -> None:
        with (
            patch.object(config, "USE_SYNONYM_TABLE", False),
            patch(
                "chatbot.query_resolution.linking.hypothesis.resolve_hypothesize_verify",
                return_value={
                    "status": "unknown",
                    "method": "hypothesize_verify",
                },
            ),
        ):
            result = resolve_cascade("coverage", "benefit", {"coverage": []})

        self.assertEqual(result, {"status": "unknown", "method": "cascade"})

    def test_territory_fuzzy_match_never_calls_llm(self) -> None:
        with (
            patch.object(config, "USE_SYNONYM_TABLE", False),
            patch(
                "chatbot.query_resolution.linking.catalog_match.resolve_fuzzy_synonym",
                return_value={
                    "status": "matched",
                    "value": "CA",
                    "method": "fuzzy_synonym",
                },
            ),
            patch(
                "chatbot.query_resolution.linking.hypothesis.resolve_hypothesize_verify"
            ) as llm_resolver,
        ):
            result = resolve_cascade(
                TERRITORY_LINK_DIMENSION, "Californa", self.catalogs
            )

        self.assertEqual(result["value"], "CA")
        self.assertEqual(result["method"], "cascade:fuzzy_synonym")
        llm_resolver.assert_not_called()

    def test_territory_fuzzy_ambiguity_preserves_scores(self) -> None:
        scores = {"NC": 92.0, "SC": 90.0}
        with (
            patch.object(config, "USE_SYNONYM_TABLE", False),
            patch(
                "chatbot.query_resolution.linking.catalog_match.resolve_fuzzy_synonym",
                return_value={
                    "status": "ambiguous",
                    "candidates": ["NC", "SC"],
                    "scores": scores,
                    "method": "fuzzy_synonym",
                },
            ),
        ):
            result = resolve_cascade(
                TERRITORY_LINK_DIMENSION, "Carolina", self.catalogs
            )

        self.assertEqual(result["candidates"], ["NC", "SC"])
        self.assertEqual(result["scores"], scores)
        self.assertEqual(result["method"], "cascade:clarify")


class HypothesizeVerifyBoundaryTests(unittest.TestCase):
    def test_llm_guess_must_verify_against_catalog(self) -> None:
        catalogs = {"regimen": ["Keytruda", "Tregzi"]}
        invoke = Mock(
            return_value=(
                '{"guesses":["Keytruda","InventedDrug"],'
                '"confidence":"high","reason":"brand"}'
            )
        )
        with patch("infrastructure.bedrock.invoke_claude", invoke):
            result = resolve_hypothesize_verify("regimen", "pembro", catalogs)

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["value"], "Keytruda")
        self.assertEqual(result["candidates"], ["Keytruda"])
        invoke.assert_called_once()

    def test_low_confidence_verified_guess_requires_clarification(self) -> None:
        catalogs = {"organization": ["Aetna"]}
        invoke = Mock(
            return_value=(
                '{"guesses":["Aetna"],"confidence":"low","reason":"uncertain"}'
            )
        )
        with patch("infrastructure.bedrock.invoke_claude", invoke):
            result = resolve_hypothesize_verify(
                "organization", "payer maybe", catalogs
            )

        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["candidates"], ["Aetna"])

    def test_llm_failure_is_contained_as_unknown(self) -> None:
        with patch(
            "infrastructure.bedrock.invoke_claude",
            side_effect=RuntimeError("offline"),
        ):
            result = resolve_hypothesize_verify(
                "regimen", "pembro", {"regimen": ["Keytruda"]}
            )

        self.assertEqual(
            result,
            {"status": "unknown", "method": "hypothesize_verify"},
        )

    def test_brand_guess_with_multiple_formulations_is_ambiguous(self) -> None:
        catalogs = {
            "regimen": ["Keytruda", "Keytruda (IV)", "Keytruda & Alimta"]
        }
        invoke = Mock(
            return_value=(
                '{"guesses":["Keytruda"],'
                '"confidence":"high","reason":"brand"}'
            )
        )
        with patch("infrastructure.bedrock.invoke_claude", invoke):
            result = resolve_hypothesize_verify("regimen", "pembro", catalogs)

        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["candidates"], ["Keytruda", "Keytruda (IV)"])


class LinkSlotsTests(unittest.TestCase):
    def test_link_slots_updates_matches_and_preserves_unresolved_raw_values(self) -> None:
        slots = {"coverage": "med", "population": "mystery"}

        def fake_resolve(dimension, mention, method, catalogs):
            if dimension == "coverage":
                return {
                    "status": "matched",
                    "value": "Medical",
                    "method": "cascade:hypothesize_verify",
                }
            return {
                "status": "ambiguous",
                "candidates": ["CLL", "MCL"],
                "method": "cascade:clarify",
            }

        with patch(
            "chatbot.query_resolution.linking.registry.resolve",
            side_effect=fake_resolve,
        ):
            linked, info = link_slots(
                slots,
                catalogs={"coverage": ["Medical"]},
                dimensions=("coverage", "population"),
                fill_missing=False,
            )

        self.assertEqual(linked, {"coverage": "Medical", "population": "mystery"})
        self.assertEqual(info["coverage"]["raw"], "med")
        self.assertEqual(info["population"]["status"], "ambiguous")

    def test_missing_identity_with_one_permissioned_value_is_auto_filled(self) -> None:
        with patch(
            "chatbot.query_resolution.linking.slot_linking._permissioned_distinct_for_slot",
            return_value=["Commercial"],
        ) as distinct:
            linked, info = link_slots(
                {"organization": "Aetna"},
                catalogs={"organization": ["Aetna"]},
                dimensions=("organization", "book"),
                fill_missing_dimensions=("book",),
            )

        self.assertEqual(linked["book"], "Commercial")
        self.assertEqual(info["book"]["method"], "cascade:auto_sole")
        distinct.assert_called_once()

    def test_missing_identity_with_multiple_values_requires_clarification(self) -> None:
        with patch(
            "chatbot.query_resolution.linking.slot_linking._permissioned_distinct_for_slot",
            return_value=["Commercial", "Medicare"],
        ):
            linked, info = link_slots(
                {"organization": "Aetna"},
                catalogs={"organization": ["Aetna"]},
                dimensions=("organization", "book"),
                fill_missing_dimensions=("book",),
            )

        self.assertNotIn("book", linked)
        self.assertEqual(info["book"]["status"], "ambiguous")
        self.assertEqual(info["book"]["method"], "cascade:clarify")

    def test_supplied_unresolved_identity_is_not_auto_filled(self) -> None:
        with (
            patch(
                "chatbot.query_resolution.linking.registry.resolve",
                return_value={"status": "unknown", "method": "cascade"},
            ),
            patch(
                "chatbot.query_resolution.linking.slot_linking._permissioned_distinct_for_slot",
                return_value=["Commercial"],
            ) as distinct,
        ):
            linked, info = link_slots(
                {"book": "mystery"},
                catalogs={"book": ["Commercial"]},
                dimensions=("book",),
                fill_missing_dimensions=("book",),
            )

        self.assertEqual(linked["book"], "mystery")
        self.assertEqual(info["book"]["status"], "unknown")
        distinct.assert_not_called()

    def test_catalog_loading_uses_mocked_mongo_aggregate_and_sorts_values(self) -> None:
        collection = Mock()
        collection.aggregate.return_value = [
            {"_id": "organization", "values": ["zeta", "", "Aetna", "zeta"]},
            {"_id": "coverage", "values": ["Pharmacy", "Medical"]},
            {"_id": "not-a-link-dimension", "values": ["ignored"]},
        ]
        database = Mock()
        database.payerLatestAccess = collection

        with patch("infrastructure.db.get_db", return_value=database):
            catalogs = load_catalogs_from_db(force=True)

        self.assertEqual(catalogs["organization"], ["Aetna", "zeta"])
        self.assertEqual(catalogs["coverage"], ["Medical", "Pharmacy"])
        collection.aggregate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
