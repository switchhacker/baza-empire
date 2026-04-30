#!/usr/bin/env python3
"""One-shot backfill for the Vision catalogue.

Walks all of `dashboard/artifacts/`, finds files marked private (either by
sitting under a `.private*` dir, by a `.private` sentinel in their dir, or
by their `.meta` sidecar), and creates pending `assets` rows for each.
Idempotent — re-runs skip already-ingested files.

The set of paths considered private is determined by
`dashboard.private_inbound.is_private(abs_path)`, the project's
single source of truth.

Usage:
    venv/bin/python -m dashboard.vision.migrate_existing
    venv/bin/python -m dashboard.vision.migrate_existing --dry-run
    venv/bin/python -m dashboard.vision.migrate_existing --include-public  # rare; cataloguing public images too
"""
from __future__ import annotations

import argparse
import os
import sys

from dashboard.private_inbound import is_private
from dashboard.vision.db import DEFAULT_DB_PATH, connect, init_db
from dashboard.vision.ingest import observe

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS_DIR = os.path.join(DASHBOARD_DIR, "artifacts")

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}


def _agent_hint(path: str) -> str | None:
    """Best-effort agent-id derived from the path. Used as origin_agent;
    not load-bearing — just a hint for browsing/debug."""
    rel = os.path.relpath(path, ARTIFACTS_DIR)
    parts = rel.split(os.sep)
    if not parts:
        return None
    head = parts[0]
    # Strip "-uploads" suffix used for agent-specific upload dirs.
    if head.endswith("-uploads"):
        return head[:-len("-uploads")]
    # Common case: file under project dir, but filename starts with the agent.
    fn = os.path.basename(path).lower()
    for agent in ("sam_axe", "phil_hass", "nova_sterling", "claw_batto",
                  "simon_bately", "duke_harmon", "scout_reeves", "rex_valor",
                  "specter_voss"):
        if fn.startswith(agent + "_"):
            return agent
    return head


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    ap.add_argument("--include-public", action="store_true",
                    help="ingest public images too — usually not what you want")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap number of new rows per run (0 = no cap)")
    args = ap.parse_args()

    init_db(args.db)
    if not os.path.isdir(ARTIFACTS_DIR):
        print(f"[migrate] {ARTIFACTS_DIR} missing — nothing to do.")
        return 0

    con = connect(args.db)
    try:
        before_count = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    finally:
        con.close()

    seen = skipped = added_estimate = 0
    for root, dirs, files in os.walk(ARTIFACTS_DIR):
        # Skip the vision-engine's own derived artifacts to avoid feedback loops.
        dirs[:] = [d for d in dirs if d not in (".vision-generated", ".vision-scraped", ".vision-crops")]
        for fn in files:
            if fn.endswith(".meta") or fn.endswith(".private"):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in IMG_EXTS:
                continue
            path = os.path.join(root, fn)
            if not args.include_public and not is_private(path):
                continue
            seen += 1
            if args.dry_run:
                print(f"[would-add] {path}")
                continue
            if args.limit and added_estimate >= args.limit:
                continue
            try:
                observe(path, source="inbound", db_path=args.db,
                        origin_agent=_agent_hint(path))
                added_estimate += 1
            except Exception as e:
                skipped += 1
                print(f"[skip] {path}: {e}", file=sys.stderr)

    if args.dry_run:
        print(f"[migrate] seen={seen} (dry-run, nothing written)")
        return 0

    con = connect(args.db)
    try:
        after_count = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    finally:
        con.close()
    new = after_count - before_count
    deduped = seen - new - skipped
    print(f"[migrate] seen={seen} new={new} deduped={deduped} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
