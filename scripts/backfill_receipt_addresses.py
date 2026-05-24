#!/usr/bin/env python3
"""
Baza Empire — Backfill ahb_receipts.store_location using vendor_locations KB

For every receipt whose store_location is a hallucinated placeholder
("123 Main St / Anytown / USA") or empty, attempt to resolve a real
address from `ocr_raw` + `ocr_structured` against the seeded vendor
locations DB. If resolved, replace the value; otherwise NULL it out
so the field stops lying.

Online vendors (Amazon, eBay) get their store_location nulled
unconditionally — for an online order the printed "address" is the
customer's shipping address, which is Serge's home, not a store.

Every change is logged into ahb_receipt_corrections (changed_by='backfill').

Usage:
    python3 scripts/backfill_receipt_addresses.py           # dry run
    python3 scripts/backfill_receipt_addresses.py --apply
    python3 scripts/backfill_receipt_addresses.py --apply --limit 20
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRAMEWORK_DIR = HERE.parent
sys.path.insert(0, str(FRAMEWORK_DIR / "skills" / "shared"))

from vendor_locations import (  # noqa: E402
    init_vendor_locations_table,
    seed_vendor_locations,
    is_placeholder_address,
    lookup_location,
    format_location,
    vendor_is_online,
)
from vendor_kb import match_vendor  # noqa: E402

DASHBOARD_DB = FRAMEWORK_DIR / "dashboard" / "baza_projects.db"


def _log_correction(conn: sqlite3.Connection, receipt_id: str, field: str,
                    old_value: str | None, new_value: str | None) -> None:
    conn.execute(
        "INSERT INTO ahb_receipt_corrections "
        "(receipt_id, changed_by, changed_at, field, old_value, new_value) "
        "VALUES (?,?,?,?,?,?)",
        (receipt_id, "backfill",
         datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
         field, old_value or "", new_value or ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually write changes (default is dry-run)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process only the first N candidate rows (0=all)")
    ap.add_argument("--db", default=str(DASHBOARD_DB))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    # Ensure schema + seed are present (idempotent).
    init_vendor_locations_table(conn)
    seed_vendor_locations(conn)

    # Candidate rows: placeholder address, empty address, OR online vendor with
    # a non-empty address (Amazon receipts that captured shipping address).
    rows = conn.execute(
        "SELECT id, store_name, vendor, store_location, ocr_raw, ocr_structured "
        "FROM ahb_receipts ORDER BY rowid DESC"
    ).fetchall()

    changes = []
    seen = 0
    for r in rows:
        raw_loc = (r["store_location"] or "").strip()
        store = (r["store_name"] or r["vendor"] or "").strip()
        if not store and not raw_loc:
            continue

        # Map raw store name to canonical
        canon = store
        if store:
            cn, _cat, conf = match_vendor(store)
            if cn and conf >= 0.82:
                canon = cn

        is_online = vendor_is_online(canon, conn=conn)
        is_placeholder = is_placeholder_address(raw_loc)
        new_loc: str | None = None
        reason = None

        if is_online and raw_loc:
            # Online vendors — null the address; it's not a real store address.
            new_loc = ""
            reason = "online-vendor"
        elif is_placeholder or not raw_loc:
            # Try resolver on raw OCR text first, then on ocr_structured
            ocr_raw = r["ocr_raw"] or ""
            hay = ocr_raw
            if r["ocr_structured"]:
                try:
                    s = json.loads(r["ocr_structured"])
                    extra = " ".join(str(s.get(k, "")) for k in
                                     ("store_location", "store_name"))
                    hay = (hay + "\n" + extra).strip()
                except Exception:
                    pass
            # For placeholder cleanup we accept a moderate match (25) — even
            # ZIP-only is better than the fabricated "123 Main St".
            # For empty addresses we require strong evidence (40+) — phone
            # match or street+number — so we don't fabricate a guess.
            min_score = 25 if is_placeholder else 40
            resolved = (
                lookup_location(canon, hay, conn=conn, min_score=min_score)
                if canon else None
            )
            if resolved:
                new_loc = format_location(resolved)
                reason = "resolver-match"
            elif is_placeholder:
                new_loc = ""
                reason = "placeholder-cleared"
            # else: empty + no strong match → leave as-is (nothing to do)

        if new_loc is None:
            continue
        if (new_loc or "") == raw_loc:
            continue

        changes.append({
            "id": r["id"],
            "store": store,
            "canonical": canon,
            "old": raw_loc,
            "new": new_loc,
            "reason": reason,
        })
        seen += 1
        if args.limit and seen >= args.limit:
            break

    # Print summary
    print(f"\n=== Receipt address backfill ===")
    print(f"DB: {args.db}")
    print(f"Candidate changes: {len(changes)}")
    for i, c in enumerate(changes, 1):
        print(f"  {i:>3}. [{c['reason']:<22}] {c['id'][:8]} {c['canonical']:<25}")
        print(f"        OLD: {c['old']!r}")
        print(f"        NEW: {c['new']!r}")

    if not args.apply:
        print(f"\nDry run — pass --apply to write changes.")
        conn.close()
        return 0

    # Apply
    written = 0
    for c in changes:
        new_val = c["new"] if c["new"] != "" else None
        conn.execute(
            "UPDATE ahb_receipts SET store_location=? WHERE id=?",
            (new_val, c["id"]),
        )
        _log_correction(conn, c["id"], "store_location", c["old"], c["new"])
        written += 1
    conn.commit()
    conn.close()
    print(f"\nWrote {written} rows + {written} correction entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
