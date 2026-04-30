#!/usr/bin/env python3
"""One-shot backfill: walk `dashboard/artifacts/.private-inbound/` and create
pending `assets` rows for everything already on disk. Idempotent — re-runs
skip rows already present.

Usage:
    venv/bin/python -m dashboard.vision.migrate_existing
    venv/bin/python -m dashboard.vision.migrate_existing --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

from dashboard.vision.db import init_db, DEFAULT_DB_PATH
from dashboard.vision.ingest import observe

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS_DIR = os.path.join(DASHBOARD_DIR, "artifacts")
PRIVATE_INBOUND_DIR = os.path.join(ARTIFACTS_DIR, ".private-inbound")

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}


def _agent_from_path(path: str) -> str | None:
    rel = os.path.relpath(path, PRIVATE_INBOUND_DIR)
    parts = rel.split(os.sep)
    return parts[0] if parts else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    args = ap.parse_args()

    init_db(args.db)
    if not os.path.isdir(PRIVATE_INBOUND_DIR):
        print(f"[migrate] {PRIVATE_INBOUND_DIR} missing — nothing to do.")
        return 0

    seen = added = skipped = 0
    for root, _dirs, files in os.walk(PRIVATE_INBOUND_DIR):
        for fn in files:
            if fn.endswith(".meta"):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in IMG_EXTS:
                continue
            path = os.path.join(root, fn)
            seen += 1
            if args.dry_run:
                print(f"[would-add] {path}")
                continue
            try:
                aid = observe(path, source="inbound", db_path=args.db,
                              origin_agent=_agent_from_path(path))
                if aid:
                    added += 1
            except Exception as e:
                skipped += 1
                print(f"[skip] {path}: {e}", file=sys.stderr)

    print(f"[migrate] seen={seen} added={added} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
