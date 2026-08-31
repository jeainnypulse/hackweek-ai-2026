#!/usr/bin/env python3
"""
Run orchestration / lives eval from the terminal (no UI).

Usage (from server/):
  python scripts/generate_decision_path_eval.py
  python scripts/run_orchestration_eval.py
  python scripts/run_orchestration_eval.py --classify-only
  python scripts/run_orchestration_eval.py --limit 10
  python scripts/run_orchestration_eval.py --lives
  python scripts/run_orchestration_eval.py --lives --csv /path/to/eval.csv
  python scripts/run_orchestration_eval.py --joint
  python scripts/run_orchestration_eval.py --joint --csv /path/to/eval.csv

Results go to hackweek-ai-2026-test-results/results/<input_stem>_results.csv.
If that file exists, the name increments: _results_1, _results_2, ...
Pass --out only to override the destination.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = (
    Path.home() / "Desktop" / "work" / "hackweek-ai-2026-test-results" / "results"
)
sys.path.insert(0, str(SERVER_DIR))

from dotenv import load_dotenv

load_dotenv(SERVER_DIR / ".env")
os.environ.setdefault("ENTITY_LINK_METHOD", "cascade")

from evaluation.orchestration import (  # noqa: E402
    EVAL_LIVES_CSV_PATH,
    FALLBACK_CSV_PATH,
    load_joint_questions,
    load_lives_questions,
    load_orchestration_questions,
    orchestration_results_to_csv,
    run_orchestration_questions,
)


def _unused_results_path(base: Path) -> Path:
    """Return base, or stem_1 / stem_2 / ... if that file already exists.

    For an auto path ending in ``_results.csv``, collisions become
    ``_results_1.csv``, ``_results_2.csv``, ...
    """
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix or ".csv"
    n = 1
    while True:
        candidate = base.with_name(f"{stem}_{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _read_eval_csv(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] == b"PK":
        raise SystemExit(
            f"{path} is a Numbers/Excel file (zip), not CSV.\n"
            "In Numbers: File → Export To → CSV, then pass that .csv to --csv."
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SystemExit(
            f"{path} is not UTF-8 text ({exc}). Export a CSV and try again."
        ) from exc
    return text


def _auto_results_path(csv_path: Path) -> Path:
    suffix = csv_path.suffix or ".csv"
    return RESULTS_DIR / f"{csv_path.stem}_results{suffix}"


def _print_row(index: int, total: int, row: dict) -> None:
    comparison = row.get("comparison") or {}
    ok = comparison.get("match") is True
    tag = "PASS" if ok else "FAIL"
    line = (
        f"[{index}/{total}] {tag} id={row.get('id')} "
        f"intent {comparison.get('expected_intent')}->{comparison.get('actual_intent')} "
        f"behavior {comparison.get('expected_behavior') or 'any'}->"
        f"{comparison.get('observed_behavior')}"
    )
    if comparison.get("expected_lives") is not None:
        line += (
            f" lives {comparison.get('expected_lives')}->"
            f"{comparison.get('actual_lives')}"
        )
    if comparison.get("slots_match") is not None:
        line += f" slots={comparison.get('slots_match')}"
    line += f" {row.get('question')!r}"
    print(line, flush=True)
    if row.get("error"):
        print(f"         error: {row['error']}", flush=True)
    mismatches = comparison.get("mismatched_fields") or []
    if mismatches and not ok:
        for item in mismatches:
            print(
                f"         slot {item.get('field')}: "
                f"expected {item.get('expected')!r} got {item.get('actual')!r}",
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Orchestration / lives eval (terminal)")
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument(
        "--lives",
        action="store_true",
        help="Run the simple lives lookup eval (intent + retrieve + slots + gold counts)",
    )
    parser.add_argument(
        "--joint",
        action="store_true",
        help="Run joint single-payer and out-of-scope eval rows.",
    )
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
        default=None,
        help=(
            "Results CSV path. Default: "
            "~/Desktop/work/hackweek-ai-2026-test-results/results/<input>_results.csv "
            "(increments _results_1, _results_2, ... if the file exists)"
        ),
    )
    args = parser.parse_args()

    if args.joint:
        loader = load_joint_questions
        default_csv = FALLBACK_CSV_PATH
    elif args.lives:
        default_csv = EVAL_LIVES_CSV_PATH
        loader = load_lives_questions
    else:
        default_csv = FALLBACK_CSV_PATH
        loader = load_orchestration_questions
    csv_arg = args.csv or default_csv
    csv_path = csv_arg if csv_arg.is_absolute() else (Path.cwd() / csv_arg)
    if not csv_path.exists():
        csv_path = csv_arg if csv_arg.exists() else default_csv
    questions = loader(_read_eval_csv(csv_path))
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        raise SystemExit(f"No eval questions in {csv_path}")

    print(
        f"Running {len(questions)} questions from {csv_path} "
        f"({'classify-only' if args.classify_only else 'full first turn'}"
        f"{', joint' if args.joint else ', lives' if args.lives else ''})",
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
    out_path = _unused_results_path(
        (args.out if args.out else _auto_results_path(csv_path)).expanduser()
    )
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
