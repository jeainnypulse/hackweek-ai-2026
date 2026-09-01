#!/usr/bin/env python3
"""Generate orchestration cases from the latest permission-scoped payer data."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from dotenv import load_dotenv

load_dotenv(SERVER_DIR / ".env")

from infrastructure.db import get_db  # noqa: E402
from chatbot.permissions import scoped_lives_query, scoped_query  # noqa: E402

DEFAULT_OUT = SERVER_DIR / "data" / "payer_latest_decision_paths.csv"
FIELDS = (
    "id",
    "type",
    "sub_type",
    "question",
    "expected_intent",
    "expected_behavior",
    "expected_slots",
    "expected_lives",
    "eval_notes",
)


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _sort_key(record: dict) -> tuple[str, ...]:
    return tuple(_text(record.get(field)).casefold() for field in sorted(record))


def _complete(record: dict, fields: Iterable[str]) -> bool:
    return all(_text(record.get(field)) for field in fields)


def _first(records: Iterable[dict], description: str) -> dict:
    choices = sorted(records, key=_sort_key)
    if not choices:
        raise RuntimeError(
            f"Latest payer data has no permission-scoped example for {description}."
        )
    return choices[0]


def _slots(**values: Any) -> str:
    return json.dumps(
        {key: value for key, value in values.items() if value not in (None, "")},
        ensure_ascii=False,
        sort_keys=True,
    )


def _question_variants(row: dict) -> list[str]:
    """Return ten deterministic phrasings for each decision-path archetype."""
    key = (row["type"], row["sub_type"])
    slots = json.loads(row["expected_slots"]) if row["expected_slots"] else {}
    organization = slots.get("organization", "")
    regimen = slots.get("regimen", "")
    indication = slots.get("indication", "")
    book = slots.get("book", "")
    coverage = slots.get("coverage", "")
    population = slots.get("population", "")
    line = slots.get("line", "")

    variants = {
        ("access_lookup", "data_complete"): [
            row["question"],
            f"Give me the QoA for {regimen} in {indication}, {population}, {line}, "
            f"at {organization} under {book} {coverage}.",
            f"Is {regimen} restricted for {indication} ({population}, {line}) on "
            f"{organization}'s {book} {coverage} benefit?",
            f"Does {organization} require PA for {regimen}, {indication}, "
            f"{population}, {line}, with {book} {coverage} coverage?",
            f"Look up the access score: payer={organization}; product={regimen}; "
            f"indication={indication}; population={population}; line={line}; "
            f"book={book}; coverage={coverage}.",
            f"For {organization} / {book} / {coverage}, what access does {regimen} "
            f"have in {indication} for {population} at {line}?",
            f"Can patients with {indication}, {population}, at {line} access "
            f"{regimen} through {organization} {book} {coverage}?",
            f"{organization.upper()} {book.upper()} {coverage.lower()}: "
            f"{regimen} access for {indication}, {population}, {line}?",
            f"What's the access status—not the criteria text—for {regimen} at "
            f"{organization} on {book} {coverage}, {indication}, {population}, {line}?",
            f"Check whether {regimen} is unrestricted at {organization}: "
            f"{indication}; {population}; {line}; {book}; {coverage}.",
        ],
        ("access_lookup", "data_auto_complete"): [
            row["question"],
            f"What's the QoA for {regimen}, {indication}, at {organization} on {book}?",
            f"Is {regimen} restricted by {organization} for {indication} under {book}?",
            f"Does {organization} require PA for {regimen} in {indication} on {book}?",
            f"Access score for {organization} / {regimen} / {indication} / {book}?",
            f"Can you check {regimen} access for {indication} with {organization}'s {book} plan?",
            f"For {book} members at {organization}, how is {regimen} covered for {indication}?",
            f"{organization.upper()} {book.lower()} — {regimen} QoA in {indication}?",
            f"Tell me whether {regimen} is unrestricted for {indication} at {organization} ({book}).",
            f"Simple lookup: {regimen} access, {organization}, {indication}, {book}.",
        ],
        ("access_lookup", "data_clarify_book"): [
            row["question"],
            f"What's the QoA for {regimen} in {indication} at {organization}?",
            f"Is {regimen} restricted by {organization} for {indication}?",
            f"Does {organization} require PA for {regimen} in {indication}?",
            f"Access score for {organization} / {regimen} / {indication}?",
            f"Can you look up {regimen} access at {organization} for {indication}?",
            f"For {indication}, how does {organization} cover {regimen}?",
            f"{organization.upper()}: {regimen} QoA for {indication}?",
            f"Tell me the access status for {regimen}, {indication}, at {organization}.",
            f"Is {regimen} unrestricted for {organization}'s {indication} patients?",
        ],
        ("access_lookup", "data_empty"): [
            row["question"],
            f"QoA for {regimen} in {indication} at {organization} on {book}?",
            f"Is {regimen} restricted by {organization} for {indication} under {book}?",
            f"Does {organization} require PA for {regimen}, {indication}, on {book}?",
            f"Access score: {organization}; {regimen}; {indication}; {book}.",
            f"Check {organization}'s {book} access to {regimen} for {indication}.",
            f"For {book} members with {indication}, how is {regimen} covered by {organization}?",
            f"{organization.upper()} {book.upper()} — {regimen} access in {indication}?",
            f"Look up the access status for {regimen} at {organization} ({book}, {indication}).",
            f"Is {regimen} unrestricted for {indication} on {organization}'s {book} plan?",
        ],
        ("lives_lookup", "data_complete"): [
            row["question"],
            f"What is {organization}'s national {book} {coverage} enrollment?",
            f"Give me the covered lives count for {organization}, {book} {coverage}, across the US.",
            f"What percent of nationwide {book} {coverage} lives does {organization} have?",
            f"{organization} — total US {book} {coverage} membership?",
            f"How many people are enrolled nationally with {organization} under {book} {coverage}?",
            f"National lives lookup: payer={organization}; book={book}; coverage={coverage}.",
            f"Across the United States, how many {book} {coverage} lives are with {organization}?",
            f"{organization.upper()} {book.lower()} {coverage.lower()} lives, nationally?",
            f"I only need enrollment: {organization}, {book}, {coverage}, national.",
        ],
        ("lives_lookup", "data_clarify_coverage"): [
            row["question"],
            f"What is {organization}'s national {book} enrollment?",
            f"How many covered lives does {organization} have on {book} across the US?",
            f"What percent of nationwide {book} lives belongs to {organization}?",
            f"{organization} — total US {book} membership?",
            f"How many people are enrolled nationally with {organization} under {book}?",
            f"National lives lookup: payer={organization}; book={book}.",
            f"Across the United States, how many {book} lives are with {organization}?",
            f"{organization.upper()} {book.lower()} lives, nationally?",
            f"I only need enrollment: {organization}, {book}, national.",
        ],
        ("lives_lookup", "data_clarify_book"): [
            row["question"],
            f"What is {organization}'s national {coverage} enrollment?",
            f"How many covered {coverage} lives does {organization} have across the US?",
            f"What percent of nationwide {coverage} lives belongs to {organization}?",
            f"{organization} — total US {coverage} membership?",
            f"How many people have {coverage} coverage nationally with {organization}?",
            f"National lives lookup: payer={organization}; coverage={coverage}.",
            f"Across the United States, how many {coverage} lives are with {organization}?",
            f"{organization.upper()} {coverage.lower()} lives, nationally?",
            f"I only need enrollment: {organization}, {coverage}, national.",
        ],
        ("lives_lookup", "data_refuse_unsupported_geography"): [
            row["question"],
            "How many Commercial Medical lives does Aetna have in the Midwest?",
            "What is UnitedHealth's Medicare enrollment in the Northeast?",
            "Count Humana's Pharmacy lives on the West Coast.",
            "How many covered lives does Aetna have in New York City?",
            "Give me Cigna's Commercial Medical membership in Los Angeles.",
            "What are Centene's Medicaid lives in Chicago?",
            "How many payer lives are in the San Francisco Bay Area?",
            "Show UnitedHealth enrollment across the Pacific Northwest.",
            "What is Aetna's membership in the tri-state area?",
        ],
        ("lives_lookup", "data_empty"): [
            row["question"],
            f"What is {organization}'s national {book} {coverage} enrollment?",
            f"How many covered lives does {organization} have on {book} {coverage} across the US?",
            f"What percent of nationwide {book} {coverage} lives belongs to {organization}?",
            f"{organization} — total US {book} {coverage} membership?",
            f"How many people are enrolled nationally with {organization} under {book} {coverage}?",
            f"National lives lookup: payer={organization}; book={book}; coverage={coverage}.",
            f"Across the United States, how many {book} {coverage} lives are with {organization}?",
            f"{organization.upper()} {book.lower()} {coverage.lower()} lives, nationally?",
            f"I only need enrollment: {organization}, {book}, {coverage}, national.",
        ],
        ("joint_lookup", "data_complete"): [
            row["question"],
            f"Give me {regimen} QoA for {indication} at {organization} on {book} "
            f"{coverage}, plus their national lives.",
            f"Is {regimen} restricted for {indication} by {organization} under {book} "
            f"{coverage}, and what is that payer's US enrollment?",
            f"Does {organization} require PA for {regimen} in {indication} on {book} "
            f"{coverage}; separately, how many national lives do they have?",
            f"Two facts for {organization}: {regimen} access in {indication} on {book} "
            f"{coverage}, and nationwide membership.",
            f"What is the access score for {regimen}, {indication}, at {organization} "
            f"({book} {coverage}) and their national covered-lives count?",
            f"For {organization}'s {book} {coverage} business, tell me {regimen} access "
            f"for {indication} and total US enrollment.",
            f"{organization.upper()}: {regimen} QoA, {indication}, {book} {coverage}; "
            "also national lives?",
            f"Can patients with {indication} access {regimen} through {organization} "
            f"{book} {coverage}, and how large is that payer nationally?",
            f"Look up both {regimen} access for {indication} and national enrollment "
            f"for {organization} under {book} {coverage}.",
        ],
        ("negative_control", "static_refuse"): [
            row["question"],
            "What exact prior-authorization criteria does Aetna use for Tregzi?",
            "Which documents must I submit for a Tregzi prior authorization?",
            "What formulary tier and copay does Humana assign to Brukinsa?",
            "How many Aetna lives are covered specifically for Tregzi?",
            "What percent of Commercial lives have a PA restriction for Tregzi?",
            "Compare Tregzi access at Aetna versus UnitedHealth.",
            "Rank the top five payers by unrestricted Brukinsa access.",
            "What do the NCCN guidelines recommend for CLL?",
            "Which PBM manages Aetna's pharmacy benefit?",
        ],
    }
    questions = variants.get(key)
    if questions is None:
        raise RuntimeError(f"No question variants configured for {key!r}.")
    if len(questions) != 10 or len(set(questions)) != 10:
        raise RuntimeError(f"Expected ten unique question variants for {key!r}.")
    return questions


def _expand_rows(rows: list[dict]) -> list[dict]:
    expanded = []
    for row in rows:
        for variant, question in enumerate(_question_variants(row), start=1):
            expanded.append(
                {
                    **row,
                    "question": question,
                    "eval_notes": f"{row['eval_notes']} Variant {variant}/10.",
                }
            )
    return [
        {"id": index, **row}
        for index, row in enumerate(expanded, start=1)
    ]


def _organization_by_slug(access_records: list[dict]) -> dict[str, str]:
    organizations: dict[str, set[str]] = defaultdict(set)
    for record in access_records:
        slug = _text(record.get("slug"))
        organization = _text(record.get("organization"))
        if slug and organization:
            organizations[slug].add(organization)
    return {
        slug: sorted(values, key=str.casefold)[0]
        for slug, values in organizations.items()
    }


def _access_book_mismatch(access_records: list[dict]) -> tuple[dict, str]:
    all_books = sorted(
        {_text(record.get("book")) for record in access_records if record.get("book")},
        key=str.casefold,
    )
    books_by_identity: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for record in access_records:
        key = (
            _text(record.get("organization")),
            _text(record.get("regimen")),
            _text(record.get("indication")),
        )
        books_by_identity[key].add(_text(record.get("book")))

    for record in sorted(access_records, key=_sort_key):
        key = (
            _text(record.get("organization")),
            _text(record.get("regimen")),
            _text(record.get("indication")),
        )
        alternate = next(
            (book for book in all_books if book not in books_by_identity[key]), None
        )
        if alternate:
            return record, alternate
    raise RuntimeError("Could not construct an access empty-result combination.")


def _lives_book_mismatch(lives_records: list[dict]) -> tuple[dict, str]:
    all_books = sorted(
        {_text(record.get("book")) for record in lives_records if record.get("book")},
        key=lambda book: (book == "MAC", book.casefold()),
    )
    books_by_lookup: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for record in lives_records:
        key = (
            _text(record.get("slug")),
            _text(record.get("coverage")),
            _text(record.get("territoryType")),
            _text(record.get("territoryName")),
        )
        books_by_lookup[key].add(_text(record.get("book")))

    for allow_mac in (False, True):
        for record in sorted(lives_records, key=_sort_key):
            key = (
                _text(record.get("slug")),
                _text(record.get("coverage")),
                _text(record.get("territoryType")),
                _text(record.get("territoryName")),
            )
            alternate = next(
                (
                    book
                    for book in all_books
                    if book not in books_by_lookup[key]
                    and (allow_mac or book != "MAC")
                ),
                None,
            )
            if alternate:
                return record, alternate
    raise RuntimeError("Could not construct a lives empty-result combination.")


def build_rows(access_records: list[dict], lives_records: list[dict]) -> list[dict]:
    """Build one current-data example for every public orchestration outcome."""
    organizations = _organization_by_slug(access_records)
    national_lives = [
        record
        for record in lives_records
        if record.get("territoryType") == "National"
        and record.get("territoryName") == "United States"
        and _text(record.get("slug")) in organizations
        and _complete(record, ("book", "coverage", "source", "lives"))
    ]
    complete_access = [
        record
        for record in access_records
        if _complete(
            record,
            (
                "slug",
                "organization",
                "regimen",
                "indication",
                "book",
                "coverage",
                "population",
                "line",
            ),
        )
    ]
    access = _first(complete_access, "access completion")
    lives = _first(national_lives, "lives completion")

    access_books: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for record in complete_access:
        access_books[
            (
                record["organization"],
                record["regimen"],
                record["indication"],
            )
        ].add(record["book"])
    access_clarify = _first(
        (
            record
            for record in complete_access
            if len(
                access_books[
                    (
                        record["organization"],
                        record["regimen"],
                        record["indication"],
                    )
                ]
            )
            > 1
        ),
        "access book clarification",
    )

    lives_coverages: dict[tuple[str, str], set[str]] = defaultdict(set)
    lives_books: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in national_lives:
        lives_coverages[(record["slug"], record["book"])].add(record["coverage"])
        lives_books[(record["slug"], record["coverage"])].add(record["book"])
    lives_faceted = _first(
        (
            record
            for record in national_lives
            if len(lives_coverages[(record["slug"], record["book"])]) > 1
        ),
        "lives coverage clarification",
    )
    lives_clarify = _first(
        (
            record
            for record in national_lives
            if len(lives_books[(record["slug"], record["coverage"])]) > 1
        ),
        "lives book clarification",
    )

    access_empty, unused_access_book = _access_book_mismatch(complete_access)
    lives_empty, unused_lives_book = _lives_book_mismatch(national_lives)

    lives_by_key = {
        (record["slug"], record["book"], record["coverage"]): record
        for record in national_lives
    }
    joint_access = _first(
        (
            record
            for record in complete_access
            if (record["slug"], record["book"], record["coverage"]) in lives_by_key
        ),
        "joint access and lives completion",
    )
    joint_lives = lives_by_key[
        (joint_access["slug"], joint_access["book"], joint_access["coverage"])
    ]

    def organization(record: dict) -> str:
        return organizations[_text(record["slug"])]

    rows = [
        {
            "type": "access_lookup",
            "sub_type": "data_complete",
            "question": (
                f"What is {access['organization']}'s {access['book']} "
                f"{access['coverage']} access for {access['regimen']} in "
                f"{access['indication']}, {access['population']}, {access['line']}?"
            ),
            "expected_intent": "access",
            "expected_behavior": "complete",
            "expected_slots": _slots(
                organization=access["organization"],
                regimen=access["regimen"],
                indication=access["indication"],
                book=access["book"],
                coverage=access["coverage"],
                population=access["population"],
                line=access["line"],
            ),
            "expected_lives": "",
            "eval_notes": "Latest access record; all slots supplied.",
        },
        {
            "type": "access_lookup",
            "sub_type": "data_auto_complete",
            "question": (
                f"What is {access['regimen']} access for {access['indication']} at "
                f"{access['organization']} on {access['book']}?"
            ),
            "expected_intent": "access",
            "expected_behavior": "complete",
            "expected_slots": _slots(
                organization=access["organization"],
                regimen=access["regimen"],
                indication=access["indication"],
                book=access["book"],
            ),
            "expected_lives": "",
            "eval_notes": "Sole permission-scoped facet values auto-complete.",
        },
        {
            "type": "access_lookup",
            "sub_type": "data_clarify_book",
            "question": (
                f"What is {access_clarify['regimen']} access for "
                f"{access_clarify['indication']} at "
                f"{access_clarify['organization']}?"
            ),
            "expected_intent": "access",
            "expected_behavior": "clarify",
            "expected_slots": _slots(
                organization=access_clarify["organization"],
                regimen=access_clarify["regimen"],
                indication=access_clarify["indication"],
            ),
            "expected_lives": "",
            "eval_notes": "Latest access identity has multiple books.",
        },
        {
            "type": "access_lookup",
            "sub_type": "data_empty",
            "question": (
                f"What is {access_empty['regimen']} access for "
                f"{access_empty['indication']} at {access_empty['organization']} "
                f"on {unused_access_book}?"
            ),
            "expected_intent": "access",
            "expected_behavior": "empty",
            "expected_slots": _slots(
                organization=access_empty["organization"],
                regimen=access_empty["regimen"],
                indication=access_empty["indication"],
                book=unused_access_book,
            ),
            "expected_lives": "",
            "eval_notes": "Canonical values from latest access data in an absent tuple.",
        },
        {
            "type": "lives_lookup",
            "sub_type": "data_complete",
            "question": (
                f"How many {lives['book']} {lives['coverage']} lives does "
                f"{organization(lives)} have nationally?"
            ),
            "expected_intent": "lives",
            "expected_behavior": "complete",
            "expected_slots": _slots(
                organization=organization(lives),
                book=lives["book"],
                coverage=lives["coverage"],
                source=lives["source"],
                territoryType=lives["territoryType"],
                territoryName=lives["territoryName"],
            ),
            "expected_lives": lives["lives"],
            "eval_notes": "Latest national lives record.",
        },
        {
            "type": "lives_lookup",
            "sub_type": "data_clarify_coverage",
            "question": (
                f"How many {lives_faceted['book']} lives does "
                f"{organization(lives_faceted)} have nationally?"
            ),
            "expected_intent": "lives",
            "expected_behavior": "clarify",
            "expected_slots": _slots(
                organization=organization(lives_faceted),
                book=lives_faceted["book"],
                source=lives_faceted["source"],
                territoryType=lives_faceted["territoryType"],
                territoryName=lives_faceted["territoryName"],
            ),
            "expected_lives": "",
            "eval_notes": "Latest lives group has multiple coverage values.",
        },
        {
            "type": "lives_lookup",
            "sub_type": "data_clarify_book",
            "question": (
                f"How many {lives_clarify['coverage']} lives does "
                f"{organization(lives_clarify)} have nationally?"
            ),
            "expected_intent": "lives",
            "expected_behavior": "clarify",
            "expected_slots": _slots(
                organization=organization(lives_clarify),
                coverage=lives_clarify["coverage"],
                source=lives_clarify["source"],
                territoryType=lives_clarify["territoryType"],
                territoryName=lives_clarify["territoryName"],
            ),
            "expected_lives": "",
            "eval_notes": "Latest lives group has multiple books.",
        },
        {
            "type": "lives_lookup",
            "sub_type": "data_refuse_unsupported_geography",
            "question": (
                f"How many {lives['book']} {lives['coverage']} lives does "
                f"{organization(lives)} have in Carolina?"
            ),
            "expected_intent": "out_of_scope",
            "expected_behavior": "refuse",
            "expected_slots": "",
            "expected_lives": "",
            "eval_notes": "Latest lives tuple with a geography that is not one state.",
        },
        {
            "type": "lives_lookup",
            "sub_type": "data_empty",
            "question": (
                f"How many {unused_lives_book} {lives_empty['coverage']} lives does "
                f"{organization(lives_empty)} have nationally?"
            ),
            "expected_intent": "lives",
            "expected_behavior": "empty",
            "expected_slots": _slots(
                organization=organization(lives_empty),
                book=unused_lives_book,
                coverage=lives_empty["coverage"],
                source=lives_empty["source"],
                territoryType=lives_empty["territoryType"],
                territoryName=lives_empty["territoryName"],
            ),
            "expected_lives": "",
            "eval_notes": "Canonical values from latest lives data in an absent tuple.",
        },
        {
            "type": "joint_lookup",
            "sub_type": "data_complete",
            "question": (
                f"What is {joint_access['organization']}'s {joint_access['book']} "
                f"{joint_access['coverage']} access for {joint_access['regimen']} in "
                f"{joint_access['indication']}, and how many lives do they have "
                "nationally?"
            ),
            "expected_intent": "joint",
            "expected_behavior": "complete",
            "expected_slots": _slots(
                organization=joint_access["organization"],
                regimen=joint_access["regimen"],
                indication=joint_access["indication"],
                book=joint_access["book"],
                coverage=joint_access["coverage"],
                source=joint_lives["source"],
                territoryType=joint_lives["territoryType"],
                territoryName=joint_lives["territoryName"],
            ),
            "expected_lives": joint_lives["lives"],
            "eval_notes": "Latest access tuple joined to matching national lives.",
        },
        {
            "type": "negative_control",
            "sub_type": "static_refuse",
            "question": "What is the weather tomorrow?",
            "expected_intent": "out_of_scope",
            "expected_behavior": "refuse",
            "expected_slots": "",
            "expected_lives": "",
            "eval_notes": "Static negative control; payer data cannot create OOS text.",
        },
    ]
    return _expand_rows(rows)


def load_latest_records() -> tuple[list[dict], list[dict]]:
    db = get_db()
    access_projection = {
        "_id": 0,
        "slug": 1,
        "organization": 1,
        "regimen": 1,
        "indication": 1,
        "book": 1,
        "coverage": 1,
        "population": 1,
        "line": 1,
    }
    lives_projection = {
        "_id": 0,
        "slug": 1,
        "book": 1,
        "coverage": 1,
        "territoryType": 1,
        "territoryName": 1,
        "source": 1,
        "lives": 1,
    }
    access_records = list(
        db.payerLatestAccess.find(scoped_query(), access_projection)
    )
    lives_records = list(
        db.payerLatestLives.find(scoped_lives_query(), lives_projection)
    )
    return access_records, lives_records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate first-turn decision-path eval cases from payer latest data."
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    access_records, lives_records = load_latest_records()
    rows = build_rows(access_records, lives_records)
    out = args.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    outcomes = sorted({row["expected_behavior"] for row in rows})
    print(
        f"Wrote {len(rows)} cases from {len(access_records)} access and "
        f"{len(lives_records)} lives records to {out}"
    )
    print(f"Outcomes: {', '.join(outcomes)}")


if __name__ == "__main__":
    main()
