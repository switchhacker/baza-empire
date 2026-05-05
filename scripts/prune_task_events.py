#!/usr/bin/env python3
"""Nightly retention pruner for task_events.

Runs from cron — see scripts/sync-agent-crons.py / crontab. Default keeps 90 days.
Override with: BAZA_TASK_EVENTS_RETENTION_DAYS=N
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.task_events import init_schema, prune_older_than


def main() -> int:
    days = int(os.environ.get("BAZA_TASK_EVENTS_RETENTION_DAYS", "90"))
    init_schema()
    deleted = prune_older_than(days=days)
    print(f"[prune_task_events] retained={days}d deleted={deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
