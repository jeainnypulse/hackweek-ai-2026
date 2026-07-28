#!/usr/bin/env python3
"""
Entity-linking bakeoff against Mongo payerLatestAccess catalogs.

Usage (from server/):
  python scripts/run_linker_bakeoff.py
  python scripts/run_linker_bakeoff.py --methods none,catalog,synonym,fuzzy,cascade
  python scripts/run_linker_bakeoff.py --methods synonym,cascade,hypothesize_verify

Writes:
  data/linker_bakeoff_results.csv
  prints summary table to stdout

Note: hypothesize_verify / cascade (on synonym miss) / embed / claude_* call Bedrock.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from dotenv import load_dotenv

load_dotenv(SERVER_DIR / ".env")

from entity_linking import (  # noqa: E402
    load_catalogs_from_db,
    resolve,
)

DATA_DIR = SERVER_DIR / "data"
GOLD_PATH = DATA_DIR / "entity_linking_gold.csv"
OUT_PATH = DATA_DIR / "linker_bakeoff_results.csv"

DEFAULT_METHODS = [
    "none",
    "catalog",
    "synonym",
    "fuzzy",
    "cascade",
    "hypothesize_verify",
    "phonetic",
    "regex",
    "embed",
    "claude_regex",
    "claude_pick",
]

SLOW_METHODS = frozenset(
    {"hypothesize_verify", "cascade", "embed", "claude_regex", "claude_pick"}
)


def load_gold(path: Path) -> list[dict]:
    rows = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            canonical = (row.get("canonical") or "").strip()
            rows.append(
                {
                    "id": row["id"],
                    "dimension": row["dimension"].strip(),
                    "mention": row["mention"].strip(),
                    "canonical": canonical or None,
                    "notes": row.get("notes", ""),
                }
            )
    return rows


def expected_status(canonical: str | None, notes: str) -> str:
    if "trap_ambiguous" in notes:
        return "ambiguous"
    if canonical is None or "trap_unknown" in notes or "trap_oos" in notes:
        return "unknown"
    return "matched"


def score_row(pred: dict, gold_canonical: str | None, notes: str) -> dict:
    exp = expected_status(gold_canonical, notes)
    status = pred.get("status")
    value = pred.get("value")

    top1 = False
    false_accept = False
    status_ok = status == exp

    if exp == "matched":
        top1 = status == "matched" and value == gold_canonical
        false_accept = status == "matched" and value != gold_canonical
        status_ok = top1
    elif exp == "ambiguous":
        # Prefer ambiguous; unknown is acceptable; matched is false accept
        status_ok = status in ("ambiguous", "unknown")
        false_accept = status == "matched"
        top1 = status == "ambiguous"
    else:  # unknown
        status_ok = status in ("unknown", "ambiguous")
        false_accept = status == "matched"
        top1 = status == "unknown"

    candidates = pred.get("candidates") or []
    in_top3 = bool(
        gold_canonical and gold_canonical in candidates[:3]
    ) or top1

    return {
        "expected_status": exp,
        "top1": top1,
        "status_ok": status_ok,
        "false_accept": false_accept,
        "in_top3": in_top3,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Entity linker bakeoff")
    parser.add_argument(
        "--methods",
        default=",".join(DEFAULT_METHODS),
        help="Comma-separated methods",
    )
    parser.add_argument("--gold", type=Path, default=GOLD_PATH)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    gold = load_gold(args.gold)

    cats = load_catalogs_from_db(force=True)
    print(
        "[bakeoff] loaded Mongo catalogs:",
        {k: len(v) for k, v in cats.items()},
    )

    if "embed" in methods:
        from entity_linking import (
            _catalog_cache,
            _ensure_catalog_embeddings,
        )

        print("[bakeoff] pre-embedding catalogs for embed method...")
        try:
            for dim, values in _catalog_cache.items():
                _ensure_catalog_embeddings(dim, values)
        except Exception as exc:
            print(f"[bakeoff] embed pre-warm failed ({exc}); skipping embed")
            methods = [m for m in methods if m != "embed"]

    results = []
    summary: dict[str, dict] = defaultdict(
        lambda: {
            "n": 0,
            "top1": 0,
            "status_ok": 0,
            "false_accept": 0,
            "latency_ms": 0.0,
        }
    )
    bedrock_skip: set[str] = set()

    total = len(gold) * len(methods)
    done = 0
    for row in gold:
        for method in methods:
            if method in bedrock_skip:
                continue
            t0 = time.perf_counter()
            try:
                pred = resolve(row["dimension"], row["mention"], method=method)
            except Exception as exc:
                if method in SLOW_METHODS:
                    print(
                        f"[bakeoff] {method} failed ({exc}); "
                        f"skipping remaining rows for this method"
                    )
                    bedrock_skip.add(method)
                    continue
                raise
            elapsed_ms = (time.perf_counter() - t0) * 1000
            scored = score_row(pred, row["canonical"], row["notes"])
            rec = {
                "id": row["id"],
                "dimension": row["dimension"],
                "mention": row["mention"],
                "canonical": row["canonical"] or "",
                "notes": row["notes"],
                "method": method,
                "pred_status": pred.get("status"),
                "pred_value": pred.get("value") or "",
                "pred_method_detail": pred.get("method") or method,
                "candidates": "|".join(pred.get("candidates") or []),
                "expected_status": scored["expected_status"],
                "top1": int(scored["top1"]),
                "status_ok": int(scored["status_ok"]),
                "false_accept": int(scored["false_accept"]),
                "in_top3": int(scored["in_top3"]),
                "latency_ms": round(elapsed_ms, 3),
            }
            results.append(rec)
            s = summary[method]
            s["n"] += 1
            s["top1"] += scored["top1"]
            s["status_ok"] += scored["status_ok"]
            s["false_accept"] += scored["false_accept"]
            s["latency_ms"] += elapsed_ms
            done += 1
            if method in SLOW_METHODS and (
                done % 5 == 0 or done == total
            ):
                print(
                    f"[bakeoff] {done}/{total} "
                    f"{method} {row['mention']!r} -> {pred.get('status')}"
                )

    methods = [m for m in methods if m not in bedrock_skip]
    if not results:
        raise SystemExit("[bakeoff] no results produced")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    print(f"\nWrote {len(results)} rows -> {args.out}\n")
    print(
        f"{'method':<14} {'n':>4} {'top1%':>8} {'ok%':>8} "
        f"{'false_acc':>10} {'avg_ms':>10}"
    )
    print("-" * 62)
    for method in methods:
        s = summary[method]
        n = max(s["n"], 1)
        print(
            f"{method:<14} {s['n']:>4} "
            f"{100 * s['top1'] / n:>7.1f}% "
            f"{100 * s['status_ok'] / n:>7.1f}% "
            f"{s['false_accept']:>10} "
            f"{s['latency_ms'] / n:>10.1f}"
        )

    # Highlight trap failures
    traps = [
        r
        for r in results
        if r["false_accept"] and ("trap_" in r["notes"] or not r["canonical"])
    ]
    if traps:
        print("\nFalse accepts on traps / empty-canonical rows:")
        for r in traps:
            print(
                f"  [{r['method']}] {r['dimension']} {r['mention']!r} "
                f"-> {r['pred_value']!r}"
            )


if __name__ == "__main__":
    main()
