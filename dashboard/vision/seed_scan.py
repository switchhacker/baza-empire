#!/usr/bin/env python3
"""Specter mode 1 — gap detector.

Walks the taxonomy, counts ok assets per leaf node, inserts seed_demand
rows for empty/thin bins (subject to a 24h dedup window so we don't pile
up duplicates each run).
"""
from __future__ import annotations

import argparse
import sys
import time

from dashboard.vision.db import DEFAULT_DB_PATH, connect, init_db
from dashboard.vision.search import count_for_node
from dashboard.vision.taxonomy import all_nodes, ancestor_filters

DEDUP_WINDOW = 24 * 3600   # don't re-request a thin bin within 24h
NEVER_SEED_PREFIXES = ("/Inbound", "/Generated", "/Scraped")


def is_leaf(node) -> bool:
    return not node.children


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    init_db(args.db)
    con = connect(args.db)
    now = time.time()
    requested = 0

    for node in all_nodes():
        if not is_leaf(node):
            continue
        if any(node.path.startswith(p) for p in NEVER_SEED_PREFIXES):
            continue
        filters = ancestor_filters(node.path)
        if not filters:
            continue

        count = count_for_node(con, filters)
        if count >= node.target:
            continue

        recent = con.execute(
            """SELECT id FROM seed_demand
                WHERE taxonomy_path = ?
                  AND fulfilled_at IS NULL
                  AND requested_at > ?""",
            (node.path, now - DEDUP_WINDOW),
        ).fetchone()
        if recent:
            if args.verbose:
                print(f"[skip-dup] {node.path} (count={count}/{node.target})")
            continue

        reason = "empty" if count == 0 else "thin"
        con.execute(
            """INSERT INTO seed_demand (taxonomy_path, needed, reason, requested_at)
               VALUES (?, ?, ?, ?)""",
            (node.path, node.target, reason, now),
        )
        requested += 1
        if args.verbose:
            print(f"[demand] {node.path} count={count} target={node.target} reason={reason}")

    print(f"[seed-scan] queued {requested} demands")
    return 0


if __name__ == "__main__":
    sys.exit(main())
