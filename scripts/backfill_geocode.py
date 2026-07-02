#!/usr/bin/env python3
"""
Baza Empire — Backfill ahb_projects.latitude/longitude via core/geocode.py

Iterates every ahb_projects row that has an address (or location) but no
latitude yet, geocodes it through Nominatim (core.geocode.ensure_project_coords,
which also persists the result), and sleeps 1.1s between calls per Nominatim's
usage policy (max 1 req/sec).

This is a one-shot deploy-time script, not a scheduled cron — later tasks in
the cron-improvements plan call `ensure_project_coords` directly (read-through
cache, no sleep needed) when they need a jobsite's coordinates for weather
alerts. Run this once to warm the cache for existing projects.

Usage:
    venv/bin/python scripts/backfill_geocode.py            # run against real DB
    venv/bin/python scripts/backfill_geocode.py --limit 20  # first 20 candidates only
    venv/bin/python scripts/backfill_geocode.py --db /path/to/other.db
"""
from __future__ import annotations

import argparse
import os
import sys
import time

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK_DIR)

from core.geocode import ensure_project_coords  # noqa: E402

DASHBOARD_DB = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")

RATE_LIMIT_SECONDS = 1.1  # Nominatim usage policy: max 1 req/sec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DASHBOARD_DB, help="Path to baza_projects.db")
    ap.add_argument("--limit", type=int, default=0, help="Process only the first N candidates (0=all)")
    args = ap.parse_args()

    import sqlite3
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, title, address, location FROM ahb_projects "
        "WHERE latitude IS NULL "
        "AND (COALESCE(TRIM(address), '') != '' OR COALESCE(TRIM(location), '') != '') "
        "ORDER BY rowid"
    ).fetchall()

    if args.limit:
        rows = rows[: args.limit]

    total = len(rows)
    succeeded = 0
    failed = 0

    print(f"\n=== Geocode backfill ===")
    print(f"DB: {args.db}")
    print(f"Candidates: {total}")

    for i, row in enumerate(rows, 1):
        addr = row["address"] or row["location"]
        result = ensure_project_coords(conn, row["id"])
        if result:
            lat, lon = result
            succeeded += 1
            print(f"  [{i}/{total}] OK    {row['title'] or row['id']}: {addr!r} -> {lat:.5f},{lon:.5f}")
        else:
            failed += 1
            print(f"  [{i}/{total}] FAIL  {row['title'] or row['id']}: {addr!r}")
        if i < total:
            time.sleep(RATE_LIMIT_SECONDS)

    conn.close()
    print(f"\nDone. {succeeded} succeeded, {failed} failed, {total} total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
