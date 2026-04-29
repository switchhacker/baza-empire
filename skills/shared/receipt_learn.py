#!/usr/bin/env python3
"""
Baza Empire — Receipt Correction Learner

Reads ahb_receipt_corrections (populated whenever Serge edits a receipt in
the AHB123 dashboard) and emits receipt_learn.json — a compact rules file
that vendor_kb loads alongside its seed dictionary and ahb_receipts history.

Rules we promote:
  - vendor / store_name corrections with >= 2 matching old→new pairs become
    aliases: future OCRs of the raw string normalize to the canonical.
  - category corrections with >= 2 matching vendor→category pairs become
    vendor category overrides: future receipts from that vendor get the
    corrected category by default.

Run standalone:
    python3 skills/shared/receipt_learn.py
    python3 skills/shared/receipt_learn.py --window-days 180  (default)

The skill is intentionally conservative — it only promotes a rule when we
see the same correction at least twice. Serge's one-off edits don't become
rules.
"""
from __future__ import annotations

import os
import sys
import json
import sqlite3
import argparse
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRAMEWORK_DIR = HERE.parent.parent
DASHBOARD_DB = FRAMEWORK_DIR / "dashboard" / "baza_projects.db"
OUT_PATH = HERE / "receipt_learn.json"

MIN_OBSERVATIONS = 2  # promote a rule only after N matching corrections


def _load_corrections(window_days: int) -> list[dict]:
    if not DASHBOARD_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(DASHBOARD_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT receipt_id, field, old_value, new_value, changed_at
                 FROM ahb_receipt_corrections
                WHERE changed_at > datetime('now', ?)""",
            (f"-{int(window_days)} days",),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"warn: could not load corrections: {e}", file=sys.stderr)
        return []


def _canonical_vendor_for(receipt_id: str, conn) -> str:
    """Lookup the current vendor/store_name on a receipt row — used to
    key category rules by canonical vendor, not by raw OCR string."""
    try:
        row = conn.execute(
            """SELECT COALESCE(NULLIF(store_name,''), vendor) AS n
                 FROM ahb_receipts WHERE id = ?""",
            (receipt_id,),
        ).fetchone()
        return (row[0] or "").strip() if row else ""
    except Exception:
        return ""


def derive_rules(corrections: list[dict]) -> dict:
    """Return {"vendor_aliases": {...}, "vendor_category_overrides": {...}}"""
    # 1. Vendor/store_name aliases: raw_old → canonical_new
    vendor_pairs: Counter[tuple[str, str]] = Counter()
    for c in corrections:
        if c["field"] in ("vendor", "store_name"):
            old = (c["old_value"] or "").strip().lower()
            new = (c["new_value"] or "").strip()
            if old and new and old.lower() != new.lower():
                vendor_pairs[(old, new)] += 1

    aliases: dict[str, str] = {}
    for (old, new), n in vendor_pairs.items():
        if n >= MIN_OBSERVATIONS:
            aliases[old] = new

    # 2. Category overrides: need canonical vendor → category
    try:
        conn = sqlite3.connect(str(DASHBOARD_DB))
    except Exception:
        conn = None

    vendor_cat_pairs: dict[str, Counter] = defaultdict(Counter)
    for c in corrections:
        if c["field"] == "category" and conn is not None:
            new_cat = (c["new_value"] or "").strip()
            if not new_cat:
                continue
            vendor = _canonical_vendor_for(c["receipt_id"], conn)
            if vendor:
                vendor_cat_pairs[vendor][new_cat] += 1

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    category_overrides: dict[str, str] = {}
    for vendor, cnt in vendor_cat_pairs.items():
        best_cat, n = cnt.most_common(1)[0]
        if n >= MIN_OBSERVATIONS:
            category_overrides[vendor] = best_cat

    return {
        "vendor_aliases": aliases,
        "vendor_category_overrides": category_overrides,
        "_stats": {
            "corrections_analyzed": len(corrections),
            "vendor_aliases_promoted": len(aliases),
            "category_overrides_promoted": len(category_overrides),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-days", type=int, default=180,
                        help="Look back this many days of corrections (default: 180)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print rules to stdout without writing receipt_learn.json")
    args = parser.parse_args()

    corrections = _load_corrections(args.window_days)
    rules = derive_rules(corrections)

    if args.dry_run:
        print(json.dumps(rules, indent=2, sort_keys=True))
        return

    try:
        OUT_PATH.write_text(json.dumps(rules, indent=2, sort_keys=True))
        print(f"✓ wrote {OUT_PATH}")
        print(json.dumps(rules["_stats"], indent=2))
    except Exception as e:
        print(f"error writing {OUT_PATH}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
