#!/usr/bin/env python3
"""
Skill: suggest_action
Specter proposes an action/correction/change to Serge via Telegram.
Serge replies yes/no. If yes, Specter executes; if no, logged and dropped.

This is the "General's suggestion engine" — when Specter sees something
that needs attention (anomaly, opportunity, improvement), he uses this
to raise it with Serge.

Usage:
    SKILL_ARGS='{
        "category": "correction|improvement|alert|idea",
        "title": "Short title for the suggestion",
        "reasoning": "Why Specter thinks this matters",
        "proposed_action": "What would happen if approved",
        "auto_execute": "optional command to run if approved"
    }'
"""
import os
import sys
import json
import time
import socket
import urllib.request
import subprocess
from datetime import datetime

# Force IPv6 preference
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET6] or _orig(*a, **k)

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
category = args.get("category", "idea")
title = args.get("title", "Untitled suggestion")
reasoning = args.get("reasoning", "")
proposed_action = args.get("proposed_action", "")
auto_execute = args.get("auto_execute", "")
timeout = int(args.get("timeout", 300))

BOT_TOKEN = os.environ.get("TELEGRAM_SPECTER_VOSS", "")
SERGE_CHAT_ID = os.environ.get("SERGE_CHAT_ID", "8551331144")

if not BOT_TOKEN:
    print("Error: TELEGRAM_SPECTER_VOSS not set")
    sys.exit(1)

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

ICONS = {
    "correction": "🔧",
    "improvement": "⬆️",
    "alert": "🚨",
    "idea": "💡",
}
icon = ICONS.get(category, "💡")


def tg_send(text, reply_markup=None):
    data = {"chat_id": SERGE_CHAT_ID, "text": text[:4000], "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    req = urllib.request.Request(f"{API}/sendMessage",
                                  data=json.dumps(data).encode(),
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tg_updates(offset=0):
    try:
        req = urllib.request.Request(f"{API}/getUpdates?offset={offset}&timeout=5")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("result", [])
    except Exception:
        return []


def wait_for_reply():
    """Poll for Serge's yes/no reply."""
    updates = tg_updates()
    offset = (updates[-1]["update_id"] + 1) if updates else 0
    start = time.time()
    while time.time() - start < timeout:
        updates = tg_updates(offset)
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message", {})
            text = msg.get("text", "").strip().lower()
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if chat_id == str(SERGE_CHAT_ID) and text:
                return text
        time.sleep(3)
    return "timeout"


# ── Build the suggestion message ──────────────────────────────────────────────
msg = f"{icon} <b>SPECTER SUGGESTION</b>\n"
msg += f"{'━' * 28}\n\n"
msg += f"<b>Category:</b> {category}\n"
msg += f"<b>Title:</b> {title}\n\n"
if reasoning:
    msg += f"<b>Why it matters:</b>\n{reasoning}\n\n"
if proposed_action:
    msg += f"<b>What I propose:</b>\n{proposed_action}\n\n"
if auto_execute:
    msg += f"<b>Auto-execute (if yes):</b>\n<code>{auto_execute[:400]}</code>\n\n"
msg += f"{'━' * 28}\n"
msg += f"Reply <b>yes</b> to approve · <b>no</b> to drop\n"
msg += f"<i>Times out in {timeout}s</i>"

result = tg_send(msg)
if not result.get("ok"):
    print(f"Failed to send: {result.get('error')}")
    sys.exit(1)

print(f"[{category}] Sent: {title}")
print(f"Waiting for Serge's reply ({timeout}s max)...")

reply = wait_for_reply()

approved = reply in ("yes", "y", "approved", "approve", "do it", "go", "go ahead", "ok", "proceed", "confirm")
denied = reply in ("no", "n", "deny", "denied", "stop", "cancel", "abort", "nope", "pass")

# Log the suggestion to the team activity
try:
    import psycopg2
    conn = psycopg2.connect(
        host=os.environ.get("BAZA_DB_HOST", "100.127.118.103"),
        port=5432,
        dbname=os.environ.get("BAZA_DB_NAME", "baza_agents"),
        user=os.environ.get("BAZA_DB_USER", "switchhacker"),
        password=os.environ.get("DB_PASSWORD", "baza2026"),
    )
    cur = conn.cursor()
    status = "approved" if approved else ("denied" if denied else "timeout")
    cur.execute(
        """INSERT INTO task_journal (agent_id, task_type, task_description, result, success, chat_id, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        ("specter_voss", f"suggestion:{category}",
         f"{title} — {reasoning[:200]}",
         f"{status}: {proposed_action[:200]}",
         approved, str(SERGE_CHAT_ID), datetime.now())
    )
    conn.commit()
    cur.close()
    conn.close()
except Exception as e:
    print(f"Warning: journal log failed: {e}")

if approved:
    tg_send(f"✓ <b>Approved.</b> Executing: {title}")
    print("APPROVED")
    if auto_execute:
        try:
            proc = subprocess.run(auto_execute, shell=True, capture_output=True, text=True, timeout=180)
            output = (proc.stdout or proc.stderr)[:1500]
            tg_send(f"📋 <b>Execution result:</b>\n<pre>{output}</pre>")
            print(f"Executed: {output[:500]}")
        except Exception as e:
            tg_send(f"❌ Execution failed: {e}")
            print(f"Exec error: {e}")
elif denied:
    tg_send(f"✗ <b>Dropped.</b> {title}")
    print("DENIED")
else:
    tg_send(f"⏱ <b>Timed out.</b> Dropped: {title}")
    print("TIMEOUT")
