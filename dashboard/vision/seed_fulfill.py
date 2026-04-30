#!/usr/bin/env python3
"""Specter mode 2 — fulfill open seed_demand rows.

For people/faces/body paths: GENERATE via SD Forge.
For scrape paths (style, scenes): scrape route hands off to seed_fulfill_scrape (Phase 8).

Picks the oldest unfulfilled demand. Acquires GPU lease before generating.
On lease contention, requeues by leaving fulfilled_at NULL — picked up next tick.
"""
from __future__ import annotations

import argparse
import sys
import time

from dashboard.vision.db import DEFAULT_DB_PATH, connect, init_db
from dashboard.vision.gpu_lease import acquire as lease_acquire, release as lease_release
from dashboard.vision.ingest import observe
from dashboard.vision.prompt_map import prompt_for_path
from dashboard.vision.sd_forge import save_generated, txt2img

GENERATE_PREFIXES = (
    "/Catalogue/People",
    "/Catalogue/Faces",
    "/Catalogue/Body",
    "/Catalogue/Mood",
)
SCRAPE_FIRST_PREFIXES = (
    "/Catalogue/Scenes",
    "/Catalogue/Style",
)


def _strategy(path: str) -> str:
    if any(path.startswith(p) for p in GENERATE_PREFIXES):
        return "generate"
    if any(path.startswith(p) for p in SCRAPE_FIRST_PREFIXES):
        return "scrape"
    return "skip"


def fulfill_one_generate(con, demand_row) -> bool:
    path = demand_row["taxonomy_path"]
    needed = demand_row["needed"]
    try:
        prompt = prompt_for_path(path)
    except ValueError as e:
        con.execute("UPDATE seed_demand SET fulfilled_at=?, fulfilled_by='unsupported' WHERE id=?",
                    (time.time(), demand_row["id"]))
        print(f"[skip] {path}: {e}")
        return False

    if not lease_acquire("rtx3070", "specter", ttl=600,
                         db_path=None, purpose=f"seed:{path}"):
        print(f"[lease-busy] {path} requeued")
        return False
    try:
        for n in range(needed):
            print(f"[gen {n+1}/{needed}] {path}")
            try:
                png = txt2img(prompt["prompt"], prompt["negative"])
                abs_path = save_generated(png, path)
                observe(abs_path, source="generated", origin_agent="specter")
            except Exception as e:
                print(f"[gen-fail] {path}: {e}")
                # Continue — partial fulfillment is fine.
        con.execute(
            "UPDATE seed_demand SET fulfilled_at=?, fulfilled_by='generate' WHERE id=?",
            (time.time(), demand_row["id"]),
        )
        return True
    finally:
        lease_release("rtx3070", "specter")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    ap.add_argument("--limit", type=int, default=2, help="max demands per run")
    args = ap.parse_args()

    init_db(args.db)
    con = connect(args.db)
    rows = con.execute(
        """SELECT id, taxonomy_path, needed, reason FROM seed_demand
            WHERE fulfilled_at IS NULL
            ORDER BY (reason='agent-request') DESC, requested_at ASC
            LIMIT ?""",
        (args.limit,),
    ).fetchall()

    fulfilled = 0
    for row in rows:
        strategy = _strategy(row["taxonomy_path"])
        if strategy == "skip":
            con.execute("UPDATE seed_demand SET fulfilled_at=?, fulfilled_by='skip' WHERE id=?",
                        (time.time(), row["id"]))
            continue
        if strategy == "scrape":
            print(f"[defer-scrape] {row['taxonomy_path']} (Phase 8 will handle)")
            continue
        if fulfill_one_generate(con, row):
            fulfilled += 1

    print(f"[seed-fulfill] fulfilled={fulfilled} of {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
