#!/usr/bin/env python3
"""
Run orchestration eval from the terminal (no UI).

Usage (from server/):
  python scripts/run_orchestration_eval.py
  python scripts/run_orchestration_eval.py --classify-only
  python scripts/run_orchestration_eval.py --limit 10
  python scripts/run_orchestration_eval.py --csv data/pulse_eval_orchestration.csv
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path.home() / "Desktop" / "work" / "hackweek-ai-2026-test-results"
sys.path.insert(0, str(SERVER_DIR))

from dotenv import load_dotenv

load_dotenv(SERVER_DIR / ".env")
os.environ.setdefault("ENTITY_LINK_METHOD", "cascade")

from eval_orchestration import (  # noqa: E402
    FALLBACK_CSV_PATH,
    load_orchestration_questions,
    orchestration_results_to_csv,
    run_orchestration_questions,
)


def _print_row(index: int, total: int, row: dict) -> None:
    comparison = row.get("comparison") or {}
    ok = comparison.get("match") is True
    tag = "PASS" if ok else "FAIL"
    print(
        f"[{index}/{total}] {tag} id={row.get('id')} "
        f"intent {comparison.get('expected_intent')}->{comparison.get('actual_intent')} "
        f"behavior {comparison.get('expected_behavior') or 'any'}->"
        f"{comparison.get('observed_behavior')} "
        f"{row.get('question')!r}",
        flush=True,
    )
    if row.get("error"):
        print(f"         error: {row['error']}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Orchestration eval (terminal)")
    parser.add_argument("--csv", type=Path, default=FALLBACK_CSV_PATH)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="Score intent classifier only (no extract/link/retrieve)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.75,
        help="Seconds to wait between questions (0 to disable)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=RESULTS_DIR / "pulse_eval_orchestration_results.csv",
        help="Where to save the results CSV (default: ~/Desktop/work/hackweek-ai-2026-test-results)",
    )
    args = parser.parse_args()

    csv_path = args.csv if args.csv.is_absolute() else (Path.cwd() / args.csv)
    if not csv_path.exists():
        csv_path = args.csv if args.csv.exists() else FALLBACK_CSV_PATH
    questions = load_orchestration_questions(csv_path.read_text(encoding="utf-8"))
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        raise SystemExit(f"No orchestration questions in {csv_path}")

    print(
        f"Running {len(questions)} questions from {csv_path} "
        f"({'classify-only' if args.classify_only else 'full first turn'})",
        flush=True,
    )

    def on_row(index: int, total: int, row: dict) -> None:
        _print_row(index, total, row)
        if args.sleep and index < total:
            time.sleep(args.sleep)

    result = run_orchestration_questions(
        questions, classify_only=args.classify_only, on_row=on_row
    )
    csv_body = orchestration_results_to_csv(result["results"])
    out_path = args.out.expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(csv_body, encoding="utf-8")
    print(
        f"\npassed={result['passed']}/{result['scored']} "
        f"pass_rate={result['pass_rate']}",
        flush=True,
    )
    print(f"Saved: {out_path}", flush=True)
    sys.exit(0 if result["passed"] == result["scored"] else 1)


if __name__ == "__main__":
    main()
