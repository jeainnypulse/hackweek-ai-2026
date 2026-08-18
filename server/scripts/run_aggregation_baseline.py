#!/usr/bin/env python3
"""
Baseline: run aggregation eval questions through the current tuple pipeline
(exact_match_turn) and score against expected_behavior.

Usage (from server/):
  python scripts/run_aggregation_baseline.py
  python scripts/run_aggregation_baseline.py --limit 10
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import traceback
from collections import Counter
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from dotenv import load_dotenv

load_dotenv(SERVER_DIR / ".env")
os.environ.setdefault("ENTITY_LINK_METHOD", "cascade")

from retrieval.exact_match import (  # noqa: E402
    clarify_needed,
    exact_match_turn,
)

DATA_DIR = SERVER_DIR / "data"
DEFAULT_CSV = DATA_DIR / "pulse_eval_orca_aggregation.csv"
OUT_CSV = DATA_DIR / "pulse_eval_orca_aggregation_baseline_results.csv"


def _parse_expected_set(raw: str) -> list[dict]:
    raw = (raw or "").strip()
    if not raw or raw == "[]":
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _observed_behavior(turn: dict) -> str:
    status = (turn.get("status") or "").strip()
    if status in ("needs_input", "needs_cascade_input"):
        return "clarify"
    if status == "error":
        return "error"
    if status == "complete":
        records = turn.get("records") or []
        if not records:
            return "empty"
        return "return_set"
    if clarify_needed(turn.get("link_info")):
        return "clarify"
    return status or "unknown"


def _record_summary(records: list[dict]) -> dict:
    orgs = sorted({r.get("organization") for r in records if r.get("organization")})
    books = sorted({r.get("book") for r in records if r.get("book")})
    access = sorted(
        {
            (r.get("accessData") or {}).get("access") or r.get("access")
            for r in records
            if (r.get("accessData") or {}).get("access") or r.get("access")
        }
    )
    return {
        "n_records": len(records),
        "n_orgs": len(orgs),
        "orgs": orgs,
        "books": books,
        "access": access,
    }


def _score_row(expected_behavior: str, observed: str, turn: dict, expected_set: list) -> str:
    exp = (expected_behavior or "").strip().lower()
    if exp == "clarify":
        return "pass" if observed == "clarify" else "fail"
    if exp == "refuse":
        # Tuple path has no refuse — only pass if empty + out-of-scope product linked away
        if observed == "empty":
            return "soft_pass"
        if observed == "clarify":
            return "soft_pass"
        return "fail"
    if exp == "return_set":
        if observed != "return_set":
            return "fail"
        records = turn.get("records") or []
        summary = _record_summary(records)
        expected_orgs = {
            item.get("org")
            for item in expected_set
            if isinstance(item, dict) and item.get("org")
        }
        expected_counts = [
            item.get("count")
            for item in expected_set
            if isinstance(item, dict) and "count" in item
        ]
        if expected_counts:
            # count questions — tuple path almost never answers correctly
            got = summary["n_orgs"]
            want = expected_counts[0]
            return "pass" if got == want else "fail"
        if expected_orgs:
            got_orgs = set(summary["orgs"])
            if got_orgs == expected_orgs:
                return "pass"
            if got_orgs & expected_orgs:
                return "partial"
            return "fail"
        # qualitative / note-only gold — just note that something returned
        return "returned"
    return "unscored"


def run(path: Path, limit: int | None = None) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("question") or "").strip()]
    if limit:
        rows = rows[:limit]

    results = []
    for i, row in enumerate(rows, 1):
        qid = row.get("id") or str(i)
        question = row["question"].strip()
        expected_behavior = (row.get("expected_behavior") or "").strip()
        expected_set = _parse_expected_set(row.get("expected_result_set") or "")
        print(f"\n[{i}/{len(rows)}] id={qid} type={row.get('type')}/{row.get('sub_type')}")
        print(f"  Q: {question[:120]}")
        try:
            turn = exact_match_turn(question=question)
            observed = _observed_behavior(turn)
            summary = _record_summary(turn.get("records") or [])
            score = _score_row(expected_behavior, observed, turn, expected_set)
            message = (turn.get("message") or "")[:200]
            confirmed = turn.get("confirmed_slots") or {}
            result = {
                "id": qid,
                "type": row.get("type"),
                "sub_type": row.get("sub_type"),
                "question": question,
                "expected_behavior": expected_behavior,
                "observed_behavior": observed,
                "score": score,
                "status": turn.get("status"),
                "message": message,
                "confirmed_slots": json.dumps(confirmed, ensure_ascii=False),
                "n_records": summary["n_records"],
                "n_orgs": summary["n_orgs"],
                "books": "|".join(summary["books"]),
                "access": "|".join(str(a) for a in summary["access"] if a),
                "orgs_sample": "|".join(summary["orgs"][:8]),
                "eval_notes": row.get("eval_notes") or "",
            }
            print(
                f"  -> status={result['status']} observed={observed} "
                f"score={score} n_records={summary['n_records']} "
                f"n_orgs={summary['n_orgs']}"
            )
        except Exception as exc:
            traceback.print_exc()
            result = {
                "id": qid,
                "type": row.get("type"),
                "sub_type": row.get("sub_type"),
                "question": question,
                "expected_behavior": expected_behavior,
                "observed_behavior": "error",
                "score": "error",
                "status": "error",
                "message": str(exc)[:200],
                "confirmed_slots": "",
                "n_records": 0,
                "n_orgs": 0,
                "books": "",
                "access": "",
                "orgs_sample": "",
                "eval_notes": row.get("eval_notes") or "",
            }
            print(f"  -> ERROR: {exc}")
        results.append(result)
    return results


def write_results(results: list[dict], out: Path) -> None:
    if not results:
        return
    fields = list(results[0].keys())
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)


def summarize(results: list[dict]) -> None:
    by_score = Counter(r["score"] for r in results)
    by_type = Counter()
    type_pass = Counter()
    for r in results:
        key = f"{r['type']}|{r['expected_behavior']}"
        by_type[key] += 1
        if r["score"] in ("pass", "soft_pass", "returned"):
            type_pass[key] += 1
    print("\n=== Baseline summary (tuple pipeline on aggregation bank) ===")
    print(f"Total: {len(results)}")
    for k, v in by_score.most_common():
        print(f"  {k}: {v}")
    print("\nBy type|expected_behavior (pass-ish / total):")
    for key, total in sorted(by_type.items()):
        print(f"  {key}: {type_pass[key]}/{total}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out", type=Path, default=OUT_CSV)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    results = run(args.csv, limit=args.limit)
    write_results(results, args.out)
    summarize(results)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
