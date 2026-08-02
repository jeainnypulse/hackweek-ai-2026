#!/usr/bin/env python3
"""
Repair expected_slots gold labels to exact payerLatestAccess catalog strings.

For each identity field (organization, regimen, indication, book), look up the
gold value against Mongo distinct catalogs using the same normalize_key as the
entity linker (and the synonym table as a fallback). On a unique hit, replace
gold with the catalog string. If gold is already an exact catalog value, leave
it unchanged. Ambiguous / unresolved fields are left unchanged and written to
a repair report for manual review.

Note: Mongo may contain multiple strings that share one normalize_key
(e.g. "Opdivo & Yervoy" and "Opdivo+Yervoy"). If gold is already one of those
catalog strings, it is treated as valid. Remaining linker-eval fails after a
clean repair pass usually mean the linker chose a *different* catalog value
than gold, not that gold used a non-catalog spelling.

Usage (from server/):
  python scripts/repair_eval_gold.py \\
    --input "/path/to/qa_pairs_hard_test_e2e.csv" \\
    --output "/path/to/qa_pairs_hard_test_e2e_repaired.csv"

Also writes a sibling report CSV: <output>_repair_report.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Optional

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from dotenv import load_dotenv

load_dotenv(SERVER_DIR / ".env")

from entity_linking import (  # noqa: E402
    LINK_DIMENSIONS,
    load_catalogs_from_db,
    load_synonyms,
    normalize_key,
)

IDENTITY_FIELDS = LINK_DIMENSIONS  # organization, regimen, indication, book


def _parse_slots(raw: str) -> Optional[dict]:
    if not raw or not str(raw).strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _dump_slots(slots: dict) -> str:
    return json.dumps(slots, ensure_ascii=False, separators=(",", ":"))


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return not text or text.lower() == "null"


def build_catalog_index(
    catalogs: dict[str, list[str]],
) -> dict[str, dict[str, list[str]]]:
    """dimension -> normalize_key -> [original catalog strings]."""
    index: dict[str, dict[str, list[str]]] = {}
    for dimension in IDENTITY_FIELDS:
        by_key: dict[str, list[str]] = {}
        for value in catalogs.get(dimension) or []:
            key = normalize_key(value)
            if not key:
                continue
            by_key.setdefault(key, []).append(value)
        index[dimension] = by_key
    return index


def resolve_gold_value(
    dimension: str,
    gold: str,
    catalog_index: dict[str, dict[str, list[str]]],
    synonyms: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """
    Resolve one gold string to a catalog canonical.

    Returns {status, value?, candidates?, note}.
    status: unchanged | repaired | unresolved | ambiguous
    """
    key = normalize_key(gold)
    if not key:
        return {"status": "unchanged", "value": gold, "note": "empty_after_normalize"}

    # Exact catalog match via normalize_key
    matches = list(catalog_index.get(dimension, {}).get(key) or [])
    if gold in matches:
        # Gold is already a real catalog string (even if siblings share a key,
        # e.g. "Opdivo & Yervoy" and "Opdivo+Yervoy" both in Mongo).
        return {"status": "unchanged", "value": gold, "note": "already_canonical"}
    if len(matches) == 1:
        return {
            "status": "repaired",
            "value": matches[0],
            "note": "catalog_normalize_key",
            "from": gold,
        }
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "value": gold,
            "candidates": matches,
            "note": "multiple_catalog_normalize_key",
        }

    # Exact string in catalog under a different normalize path (rare)
    for originals in (catalog_index.get(dimension) or {}).values():
        if gold in originals:
            return {"status": "unchanged", "value": gold, "note": "already_canonical"}

    # Synonym table: gold-as-alias -> canonical (must exist in catalog)
    syn_map = synonyms.get(dimension) or {}
    syn_canonical = syn_map.get(key)
    if syn_canonical:
        syn_key = normalize_key(syn_canonical)
        syn_matches = list(catalog_index.get(dimension, {}).get(syn_key) or [])
        if len(syn_matches) == 1:
            canonical = syn_matches[0]
            if canonical == gold:
                return {"status": "unchanged", "value": gold, "note": "synonym_already_canonical"}
            return {
                "status": "repaired",
                "value": canonical,
                "note": "synonym_then_catalog",
                "from": gold,
            }
        if len(syn_matches) > 1:
            return {
                "status": "ambiguous",
                "value": gold,
                "candidates": syn_matches,
                "note": "synonym_ambiguous_catalog",
            }
        return {
            "status": "unresolved",
            "value": gold,
            "note": "synonym_canonical_not_in_catalog",
            "synonym_canonical": syn_canonical,
        }

    return {
        "status": "unresolved",
        "value": gold,
        "note": "no_catalog_or_synonym_match",
    }


def repair_slots(
    slots: dict,
    catalog_index: dict[str, dict[str, list[str]]],
    synonyms: dict[str, dict[str, str]],
) -> tuple[dict, list[dict]]:
    """Return (repaired_slots, field_reports)."""
    repaired = dict(slots)
    reports: list[dict] = []

    for dimension in IDENTITY_FIELDS:
        gold = slots.get(dimension)
        if _is_null(gold):
            reports.append(
                {
                    "field": dimension,
                    "status": "skipped",
                    "from": gold,
                    "to": gold,
                    "note": "null",
                }
            )
            continue

        result = resolve_gold_value(
            dimension, str(gold), catalog_index, synonyms
        )
        status = result["status"]
        new_value = result.get("value", gold)
        if status == "repaired":
            repaired[dimension] = new_value
        reports.append(
            {
                "field": dimension,
                "status": status,
                "from": gold,
                "to": new_value if status == "repaired" else gold,
                "note": result.get("note", ""),
                "candidates": result.get("candidates"),
            }
        )

    return repaired, reports


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair eval CSV expected_slots to Mongo catalog strings"
    )
    parser.add_argument("--input", "-i", required=True, help="Input QA CSV path")
    parser.add_argument(
        "--output",
        "-o",
        help="Output repaired CSV path (default: <input>_repaired.csv)",
    )
    parser.add_argument(
        "--report",
        help="Repair report CSV path (default: <output> with _repair_report.csv)",
    )
    args = parser.parse_args()

    in_path = Path(args.input).expanduser().resolve()
    if not in_path.exists():
        raise SystemExit(f"Input not found: {in_path}")

    out_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else in_path.with_name(f"{in_path.stem}_repaired{in_path.suffix}")
    )
    report_path = (
        Path(args.report).expanduser().resolve()
        if args.report
        else out_path.with_name(f"{out_path.stem}_repair_report.csv")
    )

    print(f"Loading catalogs from payerLatestAccess...")
    catalogs = load_catalogs_from_db(force=True)
    synonyms = load_synonyms()
    catalog_index = build_catalog_index(catalogs)
    for dim in IDENTITY_FIELDS:
        print(f"  {dim}: {len(catalogs.get(dim) or [])} distinct")

    with in_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit("Input CSV has no header")
        fieldnames = list(reader.fieldnames)
        if "expected_slots" not in fieldnames:
            raise SystemExit("Input CSV missing expected_slots column")
        rows = list(reader)

    out_rows: list[dict] = []
    report_rows: list[dict] = []
    counts = {
        "rows": 0,
        "rows_changed": 0,
        "fields_repaired": 0,
        "fields_unresolved": 0,
        "fields_ambiguous": 0,
        "parse_errors": 0,
    }

    for row in rows:
        counts["rows"] += 1
        row_id = row.get("id", "")
        slots = _parse_slots(row.get("expected_slots") or "")
        if slots is None:
            counts["parse_errors"] += 1
            out_rows.append(row)
            report_rows.append(
                {
                    "id": row_id,
                    "type": row.get("type", ""),
                    "field": "",
                    "status": "parse_error",
                    "from": row.get("expected_slots", ""),
                    "to": "",
                    "note": "invalid_json",
                    "candidates": "",
                    "question": row.get("question", ""),
                }
            )
            continue

        repaired, field_reports = repair_slots(slots, catalog_index, synonyms)
        changed = repaired != slots
        if changed:
            counts["rows_changed"] += 1

        new_row = dict(row)
        new_row["expected_slots"] = _dump_slots(repaired)
        out_rows.append(new_row)

        for fr in field_reports:
            status = fr["status"]
            if status == "repaired":
                counts["fields_repaired"] += 1
            elif status == "unresolved":
                counts["fields_unresolved"] += 1
            elif status == "ambiguous":
                counts["fields_ambiguous"] += 1

            if status in ("repaired", "unresolved", "ambiguous", "parse_error"):
                cands = fr.get("candidates") or []
                report_rows.append(
                    {
                        "id": row_id,
                        "type": row.get("type", ""),
                        "field": fr.get("field", ""),
                        "status": status,
                        "from": fr.get("from", ""),
                        "to": fr.get("to", ""),
                        "note": fr.get("note", ""),
                        "candidates": " | ".join(cands) if cands else "",
                        "question": row.get("question", ""),
                    }
                )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out_rows)

    report_fields = [
        "id",
        "type",
        "field",
        "status",
        "from",
        "to",
        "note",
        "candidates",
        "question",
    ]
    with report_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=report_fields)
        writer.writeheader()
        writer.writerows(report_rows)

    print()
    print(f"Wrote repaired CSV: {out_path}")
    print(f"Wrote repair report: {report_path}")
    print(
        f"rows={counts['rows']} changed={counts['rows_changed']} "
        f"repaired_fields={counts['fields_repaired']} "
        f"unresolved_fields={counts['fields_unresolved']} "
        f"ambiguous_fields={counts['fields_ambiguous']} "
        f"parse_errors={counts['parse_errors']}"
    )
    if counts["fields_unresolved"] or counts["fields_ambiguous"]:
        print(
            "Review unresolved/ambiguous rows in the report and fix manually "
            "(script does not guess)."
        )


if __name__ == "__main__":
    main()
