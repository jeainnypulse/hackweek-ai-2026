from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chatbot import permissions


class PermissionsTests(unittest.TestCase):
    def tearDown(self) -> None:
        permissions.permission_pipeline.cache_clear()
        permissions.permission_filter.cache_clear()
        permissions._permission_slug_allowlist.cache_clear()
        permissions.lives_permission_pipeline.cache_clear()
        permissions.lives_permission_filter.cache_clear()

    def test_load_match_pipeline_accepts_ordered_match_stages(self) -> None:
        stages = [{"$match": {"book": "Medicare"}}, {"$match": {"slug": "aetna"}}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chatbot.permissions.json"
            path.write_text(json.dumps(stages))
            loaded = permissions._load_match_pipeline(path)

        self.assertEqual(loaded, tuple(stages))

    def test_load_match_pipeline_rejects_empty_or_non_match_stages(self) -> None:
        invalid_pipelines = ([], {"$match": {}}, [{"$project": {"slug": 1}}])
        for pipeline in invalid_pipelines:
            with self.subTest(pipeline=pipeline), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "chatbot.permissions.json"
                path.write_text(json.dumps(pipeline))
                with self.assertRaises(RuntimeError):
                    permissions._load_match_pipeline(path)

    def test_flatten_matches_preserves_single_match(self) -> None:
        match = {"slug": {"$in": ["aetna"]}}
        self.assertIs(
            permissions._flatten_matches(({"$match": match},)),
            match,
        )

    def test_flatten_matches_ands_multiple_matches_in_order(self) -> None:
        stages = (
            {"$match": {"book": {"$ne": "Federal/Other"}}},
            {"$match": {"slug": {"$in": ["aetna"]}}},
        )
        self.assertEqual(
            permissions._flatten_matches(stages),
            {"$and": [stages[0]["$match"], stages[1]["$match"]]},
        )

    def test_scoped_query_handles_missing_and_supplied_query(self) -> None:
        permission_filter = {"book": {"$ne": "Federal/Other"}}
        query = {"organization": "Aetna"}
        with patch("chatbot.permissions.permission_filter", return_value=permission_filter):
            self.assertIs(permissions.scoped_query(), permission_filter)
            self.assertIs(permissions.scoped_query({}), permission_filter)
            self.assertEqual(
                permissions.scoped_query(query),
                {"$and": [permission_filter, query]},
            )

    def test_slug_allowlist_reads_access_pipeline_and_stringifies_values(self) -> None:
        stages = (
            {"$match": {"book": "Medicare"}},
            {"$match": {"slug": {"$in": ["aetna", 42]}}},
        )
        with patch("chatbot.permissions.permission_pipeline", return_value=stages):
            self.assertEqual(
                permissions._permission_slug_allowlist(),
                ("aetna", "42"),
            )

    def test_slug_allowlist_requires_a_list(self) -> None:
        with patch(
            "chatbot.permissions.permission_pipeline",
            return_value=({"$match": {"slug": "aetna"}},),
        ):
            with self.assertRaisesRegex(RuntimeError, "no slug"):
                permissions._permission_slug_allowlist()

    def test_lives_filter_combines_lives_access_and_book_scopes(self) -> None:
        lives_stages = (
            {"$match": {"source": "DRG"}},
            {"$match": {"coverage": "Pharmacy"}},
        )
        with (
            patch("chatbot.permissions.lives_permission_pipeline", return_value=lives_stages),
            patch(
                "chatbot.permissions._permission_slug_allowlist",
                return_value=("aetna", "humana"),
            ),
        ):
            result = permissions.lives_permission_filter()

        self.assertEqual(
            result,
            {
                "$and": [
                    {"$and": [{"source": "DRG"}, {"coverage": "Pharmacy"}]},
                    {
                        "book": {"$ne": "Federal/Other"},
                        "slug": {"$in": ["aetna", "humana"]},
                    },
                ]
            },
        )

    def test_scoped_lives_query_handles_missing_and_supplied_query(self) -> None:
        permission_filter = {"source": "DRG"}
        query = {"territoryName": "CA"}
        with patch(
            "chatbot.permissions.lives_permission_filter",
            return_value=permission_filter,
        ):
            self.assertIs(permissions.scoped_lives_query(), permission_filter)
            self.assertEqual(
                permissions.scoped_lives_query(query),
                {"$and": [permission_filter, query]},
            )

    def test_permission_prefixes_preserve_stage_order_and_input(self) -> None:
        query_pipeline = [{"$group": {"_id": "$book"}}]
        access_stages = ({"$match": {"book": "Medicare"}},)
        lives_stages = ({"$match": {"source": "DRG"}},)
        with (
            patch("chatbot.permissions.permission_pipeline", return_value=access_stages),
            patch(
                "chatbot.permissions.lives_permission_pipeline",
                return_value=lives_stages,
            ),
            patch(
                "chatbot.permissions._permission_slug_allowlist",
                return_value=("aetna",),
            ),
        ):
            access = permissions.with_permission_prefix(query_pipeline)
            lives = permissions.with_lives_permission_prefix(query_pipeline)

        self.assertEqual(access, [*access_stages, *query_pipeline])
        self.assertEqual(
            lives,
            [
                *lives_stages,
                {
                    "$match": {
                        "book": {"$ne": "Federal/Other"},
                        "slug": {"$in": ["aetna"]},
                    }
                },
                *query_pipeline,
            ],
        )
        self.assertEqual(query_pipeline, [{"$group": {"_id": "$book"}}])


if __name__ == "__main__":
    unittest.main()
