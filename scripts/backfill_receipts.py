#!/usr/bin/env python3
"""
Baza Empire — Backfill existing ahb_receipts rows

Updates TWO fields only:
  * vendor / store_name — normalized via vendor_kb (e.g. "The homedepot" → "Home Depot")
  * receipt_date — re-extracted from ocr_raw when current value is blank or
    equals the created_at date (i.e. was defaulted to upload time, not the
    date actually printed on the receipt)

Never touches: total, amount, subtotal, tax_amount, category, items_json,
created_by, ocr_raw, ocr_structured, file_path, project_id, payment_method,
teller_name (already-populated fields stay).

Category is ALSO filled ONLY when it's currently empty AND vendor_kb gave a
strong match with a category hint — same policy as new-receipt filing. If
the row already has a category, we leave it alone.

Usage:
    python3 scripts/backfill_receipts.py              # dry run, shows changes
    python3 scripts/backfill_receipts.py --apply      # actually write
    python3 scripts/backfill_receipts.py --apply --limit 50   # batch test

Every change is also logged into ahb_receipt_corrections so the audit trail
records the backfill. changed_by = 'backfill'.
"""
from __future__ import annotations

import os
import re
import sys
import json
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent
FRAMEWORK_DIR = HERE.parent
sys.path.insert(0, str(FRAMEWORK_DIR / "skills" / "shared"))

from vendor_kb import match_vendor  # noqa: E402

DASHBOARD_DB = FRAMEWORK_DIR / "dashboard" / "baza_projects.db"

# Patterns for pulling a date out of ocr_raw
DATE_PATTERNS = [
    re.compile(r'\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b'),   # YYYY-MM-DD
    re.compile(r'\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b'),    # MM-DD-YYYY
    re.compile(r'\b(\d{1,2})[-/](\d{1,2})[-/](\d{2})\b'),    # MM-DD-YY
]


def _normalize_date(s: str) -> str:
    """Return ISO YYYY-MM-DD if the raw text contains a date, else ''."""
    if not s:
        return ""
    # First pattern: YYYY-MM-DD already
    m = DATE_PATTERNS[0].search(s)
    if m:
        y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
        return f"{y}-{mo}-{d}"
    # Second pattern: MM-DD-YYYY
    m = DATE_PATTERNS[1].search(s)
    if m:
        mo, d, y = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
        return f"{y}-{mo}-{d}"
    # Third pattern: MM-DD-YY (infer century)
    m = DATE_PATTERNS[2].search(s)
    if m:
        mo, d, yy = m.group(1).zfill(2), m.group(2).zfill(2), int(m.group(3))
        # Receipts <50 → 2000s, >=50 → 1900s (won't matter for AHBCO's timeframe)
        y = 2000 + yy if yy < 50 else 1900 + yy
        return f"{y}-{mo}-{d}"
    return ""


def _is_default_date(receipt_date: str, created_at: str) -> bool:
    """A receipt_date equals the upload date (or is blank) → was defaulted."""
    if not receipt_date:
        return True
    # Treat missing as default
    rd = receipt_date.strip()[:10]
    ca = (created_at or "").strip()[:10]
    return rd == ca


def _log_correction(conn, rid, field, old, new):
    try:
        conn.execute(
            """INSERT INTO ahb_receipt_corrections
                    (receipt_id, changed_by, field, old_value, new_value)
               VALUES (?, 'backfill', ?, ?, ?)""",
            (rid, field, str(old or ''), str(new or '')),
        )
    except Exception as e:
        # Corrections table may not exist yet on older DBs
        if "no such table" not in str(e).lower():
            print(f"warn: could not log correction for {rid}: {e}", file=sys.stderr)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="Actually write changes (default: dry run)")
    p.add_argument("--limit", type=int, default=0,
                   help="Process only N rows (for testing)")
    p.add_argument("--min-conf", type=float, default=0.85,
                   help="Minimum vendor_kb confidence to rename (default: 0.85)")
    args = p.parse_args()

    if not DASHBOARD_DB.exists():
        print(f"DB not found: {DASHBOARD_DB}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DASHBOARD_DB))
    conn.row_factory = sqlite3.Row

    q = """SELECT id, vendor, store_name, category, receipt_date, ocr_raw,
                  ocr_structured, created_at
             FROM ahb_receipts
         ORDER BY created_at DESC"""
    if args.limit > 0:
        q += f" LIMIT {int(args.limit)}"
    rows = conn.execute(q).fetchall()

    total = len(rows)
    vendor_changes = 0
    date_changes = 0
    category_fills = 0
    unchanged = 0
    examples = []

    for r in rows:
        rid = r["id"]
        updates: dict[str, str] = {}

        # ── Vendor / store_name normalization ────────────────────────────────
        old_store = (r["store_name"] or "").strip()
        old_vendor = (r["vendor"] or "").strip()
        # Use whichever is populated to query vendor_kb
        query = old_store or old_vendor
        if query:
            canonical, cat_hint, conf = match_vendor(query)
            if canonical and conf >= args.min_conf and canonical != query:
                updates["vendor"] = canonical
                updates["store_name"] = canonical
                vendor_changes += 1
                # Fill category ONLY when it was empty and we got a strong hint
                if not (r["category"] or "").strip() and cat_hint:
                    updates["category"] = cat_hint
                    category_fills += 1

        # ── Receipt date backfill ────────────────────────────────────────────
        if _is_default_date(r["receipt_date"], r["created_at"]):
            # First try the structured OCR JSON
            structured = {}
            if r["ocr_structured"]:
                try:
                    structured = json.loads(r["ocr_structured"])
                except Exception:
                    structured = {}
            extracted = (
                _normalize_date(structured.get("purchase_date") or "")
                or _normalize_date(r["ocr_raw"] or "")
            )
            if extracted and extracted != (r["receipt_date"] or ""):
                updates["receipt_date"] = extracted
                updates["year"] = extracted[:4]
                date_changes += 1

        if not updates:
            unchanged += 1
            continue

        if len(examples) < 10:
            examples.append({
                "id": rid,
                "before": {
                    "store_name": old_store, "vendor": old_vendor,
                    "category": r["category"], "receipt_date": r["receipt_date"],
                },
                "after": updates,
            })

        if args.apply:
            try:
                set_clauses = ", ".join(f"{k} = ?" for k in updates)
                vals = list(updates.values()) + [rid]
                conn.execute(
                    f"UPDATE ahb_receipts SET {set_clauses} WHERE id = ?",
                    vals,
                )
                for k, v in updates.items():
                    old_v = r[k] if k in r.keys() else ""
                    _log_correction(conn, rid, k, old_v, v)
            except Exception as e:
                print(f"err updating {rid}: {e}", file=sys.stderr)

    if args.apply:
        conn.commit()
    conn.close()

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[{mode}] scanned {total} receipts")
    print(f"  vendor_renames  : {vendor_changes}")
    print(f"  category_fills  : {category_fills}")
    print(f"  date_backfills  : {date_changes}")
    print(f"  unchanged       : {unchanged}")
    if examples:
        print("\nexamples (first 10 changes):")
        print(json.dumps(examples, indent=2))


if __name__ == "__main__":
    main()
