from __future__ import annotations

import unittest
from unittest.mock import patch

from retrieval.exact_match import (
    _linker_clarify_response,
    _refresh_missing_access_identity,
    _resolved_identity_filter,
)


def _session() -> dict:
    return {
        "session_id": "test",
        "question": "Is Keytrudax unrestricted at Aetna?",
        "intent": "access",
        "slots": {
            "regimen": "Keytrudax",
            "indication": None,
            "book": "Medicare",
            "organization": "Aetna",
        },
        "linked": {
            "regimen": "Tregzi",
            "book": "Medicare",
            "organization": "Aetna",
        },
        "link_info": {
            "regimen": {
                "raw": "Keytrudax",
                "status": "matched",
                "value": "Tregzi",
                "method": "user_confirm",
            },
            "book": {"status": "matched", "value": "Medicare"},
            "organization": {"status": "matched", "value": "Aetna"},
        },
        "pending_writebacks": [],
        "current_slot": None,
        "cascade": None,
    }


class TreatmentPlanCompletionTests(unittest.TestCase):
    def test_missing_identity_is_recomputed_after_product_confirmation(self):
        session = _session()

        with patch(
            "retrieval.exact_match._distinct_for_session",
            return_value=["CLL", "MCL"],
        ):
            response = _linker_clarify_response(session)

        self.assertEqual(response["status"], "needs_input")
        self.assertEqual(response["current_slot"], "indication")
        self.assertEqual(response["candidates"], ["CLL", "MCL"])

    def test_sole_missing_identity_is_auto_confirmed(self):
        session = _session()

        with patch(
            "retrieval.exact_match._distinct_for_session",
            return_value=["CLL"],
        ):
            _refresh_missing_access_identity(session)

        self.assertEqual(session["linked"]["indication"], "CLL")
        self.assertEqual(
            session["link_info"]["indication"]["method"],
            "cascade:auto_sole",
        )

    def test_unresolved_raw_identity_is_not_used_to_scope_other_fields(self):
        session = _session()
        session["linked"]["regimen"] = "Keytrudax"
        session["link_info"]["regimen"] = {
            "raw": "Keytrudax",
            "status": "ambiguous",
            "candidates": ["Tregzi"],
        }

        self.assertEqual(
            _resolved_identity_filter(session, exclude="indication"),
            {"book": "Medicare", "organization": "Aetna"},
        )


if __name__ == "__main__":
    unittest.main()
