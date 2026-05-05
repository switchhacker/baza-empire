#!/usr/bin/env python3
"""
Duke's Morning Digest — runs every weekday morning via cron.

What it does:
  1. Calls duke_roadmap mode=create count=3 → queues 3 fresh tasks
  2. Sends Serge a Telegram message with the assignment list
  3. Emits intent_parsed + task_started events so /chains shows them

Recommended cron:
  0 7 * * 1-5 /home/switchhacker/baza-empire/agent-framework-v3/venv/bin/python \
      /home/switchhacker/baza-empire/agent-framework-v3/scripts/duke_morning_digest.py \
      >> /home/switchhacker/baza-empire/agent-framework-v3/logs/duke_morning.log 2>&1

Skip-conditions (won't fire even on schedule):
  * Existing pending+in_progress tasks per agent already > BAZA_DUKE_MAX_OPEN
    (default 3) — Duke doesn't pile work onto agents already drowning
  * BAZA_DUKE_DIGEST_DISABLED=1 in env

Override count via BAZA_DUKE_DIGEST_COUNT (default 3).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(FRAMEWORK_DIR, "configs", "secrets.env"))

DUKE_TOKEN = os.environ.get("TELEGRAM_DUKE_HARMON")
SERGE_CHAT_ID = os.environ.get("SERGE_CHAT_ID")
COUNT = int(os.environ.get("BAZA_DUKE_DIGEST_COUNT", "3"))
MAX_OPEN_PER_AGENT = int(os.environ.get("BAZA_DUKE_MAX_OPEN", "3"))


def already_overloaded() -> bool:
    """Refuse to queue more if any agent has > MAX_OPEN_PER_AGENT open tasks."""
    import sqlite3
    db = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
    if not os.path.isfile(db):
        return False
    try:
        conn = sqlite3.connect(db, timeout=10)
        rows = conn.execute(
            "SELECT assigned_to, COUNT(*) FROM tasks "
            "WHERE status IN ('pending','in_progress') "
            "GROUP BY assigned_to"
        ).fetchall()
        conn.close()
    except Exception:
        return False
    overloaded = [a for a, n in rows if a and n > MAX_OPEN_PER_AGENT]
    if overloaded:
        print(f"[duke_digest] skipping — overloaded: {', '.join(overloaded)}")
        return True
    return False


def run_roadmap_skill() -> str | None:
    skill_path = os.path.join(FRAMEWORK_DIR, "skills", "shared", "duke_roadmap.py")
    env = os.environ.copy()
    env["SKILL_ARGS"] = json.dumps({"count": COUNT, "mode": "create"})
    env["AGENT_ID"] = "duke_harmon"
    try:
        r = subprocess.run(
            [sys.executable, skill_path],
            capture_output=True, text=True, env=env, timeout=60,
        )
        if r.returncode != 0:
            print(f"[duke_digest] skill failed: rc={r.returncode} stderr={r.stderr[:300]}")
            return None
        return r.stdout
    except Exception as e:
        print(f"[duke_digest] skill exception: {e}")
        return None


def send_telegram(text: str) -> bool:
    if not DUKE_TOKEN or not SERGE_CHAT_ID:
        print("[duke_digest] missing TELEGRAM_DUKE_HARMON or SERGE_CHAT_ID — printing instead:")
        print(text)
        return False
    # Telegram caps message length around 4096; chunk if needed
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)] or [text]
    ok_all = True
    for chunk in chunks:
        payload = json.dumps({
            "chat_id": SERGE_CHAT_ID, "text": chunk, "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{DUKE_TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read())
                if not d.get("ok"):
                    print(f"[duke_digest] telegram error: {d}")
                    ok_all = False
        except Exception as e:
            print(f"[duke_digest] telegram exception: {e}")
            ok_all = False
    return ok_all


def main() -> int:
    if os.environ.get("BAZA_DUKE_DIGEST_DISABLED", "0") in ("1", "true", "yes"):
        print("[duke_digest] disabled via env")
        return 0
    if already_overloaded():
        return 0

    today = datetime.now().strftime("%A %B %d")
    out = run_roadmap_skill()
    if not out:
        return 1

    header = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Duke — Morning Roadmap, {today}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Tasks queued and ready for the team. Each will auto-run on the next "
        f"baza-task-runner tick. Watch progress at /chains.\n\n"
    )
    send_telegram(header + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
