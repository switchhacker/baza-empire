#!/usr/bin/env python3
"""
One-shot cleanup of stale in-progress tasks in dashboard/baza_projects.db.

Marks targeted tasks as status='archived' (task_runner filters this out, so
they stop rotating) and prepends an [ARCHIVED <date>: <reason>] line to
notes so the audit trail survives.

Default = --dry-run. Pass --apply to actually update.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "dashboard", "baza_projects.db")
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

ArchiveRule = tuple[str, str, str]  # (sql_where, reason, label)


# Each rule = (extra WHERE clause matching tasks to archive, archive reason, short label).
# The rules are independent; a task matched by any rule is archived. Already-completed
# and already-archived tasks are excluded everywhere.
RULES: list[ArchiveRule] = [
    (
        # ahb123.com pre-launch — site is live since April
        "project_id = '69c2bce7928bb0babb49a0a5'",
        "ahb123.com is live; pre-launch task superseded. Track follow-on work under a Baza site-optimization project.",
        "ahb123-prelaunch",
    ),
    (
        # April 5 roadmap seeds — many remain unstarted; clean slate, re-create via /develop or new Baza Project if still relevant.
        "title LIKE 'Roadmap:%' AND date(created_at) = '2026-04-05'",
        "April-5 roadmap seed left zombied. Re-create as a Baza Project via /create new baza project or assign concrete subtasks via /develop.",
        "april5-roadmap-seed",
    ),
    (
        # Review-leads-pipeline duplicates: cron kept generating the same task. Keep the newest, archive the rest.
        # Implemented as a separate dedupe pass below since SQL "keep newest" is awkward inline.
        "1=0",  # placeholder so this rule index is reserved
        "(handled by dedupe pass)",
        "review-leads-dedupe",
    ),
]

# Title patterns whose duplicates should be archived, keeping only the newest in_progress entry.
DEDUPE_TITLES: list[str] = [
    "Review leads pipeline and prepare follow-up templates",
    "Review client communication history and draft outreach",
    "Review financial records and prepare compliance updates",
]


def candidates_for_rules(conn: sqlite3.Connection):
    """Yield (task_dict, reason, label) for tasks matched by direct SQL rules."""
    for where, reason, label in RULES:
        if where == "1=0":
            continue
        for row in conn.execute(
            f"SELECT id, title, assigned_to, project_id, status, notes, created_at "
            f"FROM tasks WHERE status NOT IN ('completed', 'archived') AND ({where}) "
            f"ORDER BY created_at"
        ).fetchall():
            yield dict(row), reason, label


def candidates_for_dedupe(conn: sqlite3.Connection):
    """For each DEDUPE_TITLES entry, keep the newest in_progress task per
    (assigned_to, title) and archive the rest."""
    for pattern in DEDUPE_TITLES:
        rows = conn.execute(
            "SELECT id, title, assigned_to, project_id, status, notes, created_at "
            "FROM tasks WHERE status NOT IN ('completed','archived') AND title = ? "
            "ORDER BY assigned_to, created_at DESC",
            (pattern,),
        ).fetchall()
        per_agent: dict[str, list[dict]] = {}
        for r in rows:
            d = dict(r)
            per_agent.setdefault(d["assigned_to"] or "", []).append(d)
        for agent, group in per_agent.items():
            if len(group) <= 1:
                continue
            keep = group[0]  # newest first due to ORDER BY DESC
            for stale in group[1:]:
                yield (
                    stale,
                    f"duplicate of {keep['id']} ({keep['created_at'][:10]}); cron created multiple copies of the same generic task",
                    "review-leads-dedupe",
                )


def archive_task(conn: sqlite3.Connection, task_id: str, reason: str, label: str):
    archive_note = f"[ARCHIVED {TODAY} reason={label}] {reason}"
    existing = conn.execute("SELECT notes FROM tasks WHERE id = ?", (task_id,)).fetchone()
    notes = (existing["notes"] if existing else "") or ""
    new_notes = (archive_note + "\n\n" + notes).strip()
    conn.execute(
        "UPDATE tasks SET status='archived', notes=?, updated_at=? WHERE id=?",
        (new_notes, datetime.utcnow().isoformat(), task_id),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually mutate the DB (default = dry-run)")
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    seen: set[str] = set()
    actions: list[tuple[dict, str, str]] = []
    for rec, reason, label in candidates_for_rules(conn):
        if rec["id"] in seen:
            continue
        seen.add(rec["id"])
        actions.append((rec, reason, label))
    for rec, reason, label in candidates_for_dedupe(conn):
        if rec["id"] in seen:
            continue
        seen.add(rec["id"])
        actions.append((rec, reason, label))

    if not actions:
        print("Nothing to archive — your task DB looks clean.")
        return 0

    print(f"\n{'DRY RUN — no changes will be saved' if not args.apply else 'APPLYING'}")
    print("=" * 78)
    by_label: dict[str, int] = {}
    for rec, reason, label in actions:
        by_label[label] = by_label.get(label, 0) + 1
        print(f"  [{label}] {rec['id'][:8]}  {(rec['title'] or '')[:60]:60s}  "
              f"→ {rec['assigned_to'] or '?'}")

    print("\nSummary:")
    for label, n in sorted(by_label.items()):
        print(f"  {label:30s}  {n}")
    print(f"  {'TOTAL':30s}  {len(actions)}")

    if args.apply:
        for rec, reason, label in actions:
            archive_task(conn, rec["id"], reason, label)
        conn.commit()
        print(f"\n✓ Archived {len(actions)} task(s).")
    else:
        print("\nRe-run with --apply to commit these changes.")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
