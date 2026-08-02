#!/usr/bin/env python3
"""
E2E cascade linker eval on pinned June-22-style questions.

Usage (from server/):
  python scripts/run_e2e_cascade.py
  python scripts/run_e2e_cascade.py --questions data/june22_e2e_questions.csv

Writes:
  data/e2e_cascade_results.csv
  data/e2e_cascade_triage.md
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from dotenv import load_dotenv

load_dotenv(SERVER_DIR / ".env")

# Force production linker for this script
os.environ.setdefault("ENTITY_LINK_METHOD", "cascade")

from entity_linking import link_slots  # noqa: E402
from retrieval.exact_match import ENTITY_LINK_METHOD, extract_slots  # noqa: E402
from db import get_db  # noqa: E402
from permissions import scoped_query  # noqa: E402

DATA_DIR = SERVER_DIR / "data"
DEFAULT_QUESTIONS = DATA_DIR / "june22_e2e_questions.csv"
OUT_CSV = DATA_DIR / "e2e_cascade_results.csv"
OUT_MD = DATA_DIR / "e2e_cascade_triage.md"


def load_questions(path: Path) -> list[dict]:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("question") or "").strip():
                rows.append(row)
    return rows


def triage_row(
    *,
    slots: dict,
    link_info: dict,
    linked: dict,
    n_records: int,
    expected: dict,
) -> str:
    reasons = []
    for dim in ("organization", "regimen", "indication", "book"):
        exp = (expected.get(dim) or "").strip()
        if not exp:
            continue
        got = linked.get(dim)
        info = link_info.get(dim) or {}
        if got == exp:
            continue
        if info.get("status") == "matched" and got != exp:
            reasons.append(f"link_wrong:{dim} got={got!r} want={exp!r}")
        elif info.get("status") in ("ambiguous", "unknown"):
            reasons.append(
                f"link_{info.get('status')}:{dim} raw={info.get('raw')!r} "
                f"cands={info.get('candidates')}"
            )
        elif not slots.get(dim):
            reasons.append(f"extract_miss:{dim}")
        else:
            reasons.append(f"link_miss:{dim} slot={slots.get(dim)!r} got={got!r}")

    if n_records == 0 and not reasons:
        reasons.append("empty_result")
    elif n_records == 0:
        reasons.append("empty_result")
    if not reasons:
        return "ok"
    return "; ".join(reasons)


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E cascade ask eval")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--out-csv", type=Path, default=OUT_CSV)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    questions = load_questions(args.questions)
    print(
        f"[e2e] method={ENTITY_LINK_METHOD} n={len(questions)} "
        f"questions={args.questions}"
    )

    results = []
    for row in questions:
        q = row["question"].strip()
        print(f"\n[e2e] Q{row.get('id')}: {q}")
        try:
            slots = extract_slots(q)
        except Exception as exc:
            results.append(
                {
                    **{k: row.get(k, "") for k in row},
                    "slots": "",
                    "linked": "",
                    "link_info": str(exc),
                    "n_records": 0,
                    "triage": f"extract_error:{exc}",
                }
            )
            continue

        linked, link_info = link_slots(slots, method=ENTITY_LINK_METHOD)
        query = {k: v for k, v in linked.items() if v is not None and v != ""}
        n_records = 0
        if query:
            n_records = get_db().payerLatestAccess.count_documents(
                scoped_query(query)
            )
            # cap check similar to retrieve
            n_records = min(n_records, 50)

        expected = {
            "organization": row.get("expected_organization", ""),
            "regimen": row.get("expected_regimen", ""),
            "indication": row.get("expected_indication", ""),
            "book": row.get("expected_book", ""),
        }
        triage = triage_row(
            slots=slots,
            link_info=link_info,
            linked=linked,
            n_records=n_records,
            expected=expected,
        )
        print(f"  slots={slots}")
        print(f"  linked={linked}")
        print(f"  n_records={n_records} triage={triage}")
        results.append(
            {
                "id": row.get("id", ""),
                "question": q,
                "notes": row.get("notes", ""),
                "expected_organization": expected["organization"],
                "expected_regimen": expected["regimen"],
                "expected_indication": expected["indication"],
                "expected_book": expected["book"],
                "slots": str(slots),
                "linked": str(linked),
                "link_info": str(link_info),
                "n_records": n_records,
                "triage": triage,
            }
        )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    ok = sum(1 for r in results if r["triage"] == "ok")
    fail = [r for r in results if r["triage"] != "ok"]
    lines = [
        "# E2E cascade triage",
        "",
        f"- Method: `{ENTITY_LINK_METHOD}`",
        f"- Questions: {len(results)}",
        f"- OK (expected slots linked + ≥1 Mongo hit): **{ok}/{len(results)}**",
        "",
        "## Failures",
        "",
    ]
    if not fail:
        lines.append("_None_")
    else:
        for r in fail:
            lines.append(f"### Q{r['id']}: {r['question']}")
            lines.append(f"- triage: `{r['triage']}`")
            lines.append(f"- slots: `{r['slots']}`")
            lines.append(f"- linked: `{r['linked']}`")
            lines.append(f"- n_records: {r['n_records']}")
            lines.append("")

    lines.extend(
        [
            "## Failure buckets",
            "",
            "- `extract_miss:*` — slot extraction did not pull the field",
            "- `link_ambiguous:*` / `link_unknown:*` — cascade needs dropdown clarify",
            "- `link_wrong:*` — linked but not preferred canonical",
            "- `empty_result` — query returned 0 Mongo docs",
            "",
            f"CSV: `{args.out_csv}`",
            "",
        ]
    )
    args.out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[e2e] wrote {args.out_csv}")
    print(f"[e2e] wrote {args.out_md}")
    print(f"[e2e] summary: {ok}/{len(results)} ok")


if __name__ == "__main__":
    main()
