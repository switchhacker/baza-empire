#!/usr/bin/env python3
"""
Skill: suggest_action (shared, agent-agnostic — Graft 3)

Any agent can propose an action/correction/improvement to Serge via Telegram
and (optionally) auto-execute it on approval. The agent's identity is read
from the AGENT_ID env var (set by core/skills_engine.py for every skill call),
and the bot token is looked up via TELEGRAM_<AGENT_ID_UPPER>.

This is the LLM-callable cousin of core/approval.py:
  - approval.py: hard "I'm about to delete this — say yes" wall
  - suggest_action: soft "I notice we should do X — want me to?" proposal

Usage from an LLM response:
    ##SKILL:suggest_action{
        "category": "improvement",
        "title": "Reschedule the brand audit cron from daily to weekly",
        "reasoning": "Daily run produces near-identical reports; weekly is enough",
        "proposed_action": "Edit agents/sam_axe/crons/brand_monitor.py to weekly",
        "auto_execute": "##SKILL:create_task{\"title\":\"Reschedule brand cron\",...}##",
        "category": "improvement"
    }##

Reply yes → auto_execute runs (as a shell command). Reply no → dropped.
Times out in 300s by default → dropped.
"""
import os
import sys
import json
import time
import html
import urllib.request
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# ── Inputs ────────────────────────────────────────────────────────────────────
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
AGENT_ID  = os.environ.get("AGENT_ID", "unknown_agent")
category  = args.get("category", "idea")
title     = args.get("title", "Untitled suggestion")
reasoning = args.get("reasoning", "")
proposed_action = args.get("proposed_action", "")
auto_execute    = args.get("auto_execute", "")
timeout         = int(args.get("timeout", 300))

BOT_TOKEN     = os.environ.get(f"TELEGRAM_{AGENT_ID.upper()}", "")
SERGE_CHAT_ID = os.environ.get("SERGE_CHAT_ID", "")

if not BOT_TOKEN:
    print(f"Error: TELEGRAM_{AGENT_ID.upper()} not set — cannot send suggestion")
    sys.exit(1)
if not SERGE_CHAT_ID:
    print("Error: SERGE_CHAT_ID not set")
    sys.exit(1)

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
PRETTY_AGENT = AGENT_ID.replace("_", " ").upper()

ICONS = {
    "correction":  "🔧",
    "improvement": "⬆️",
    "alert":       "🚨",
    "idea":        "💡",
    "delete":      "🗑️",
    "spend":       "💸",
}
icon = ICONS.get(category, "💡")


def tg_send(text):
    try:
        from core.telegram_fmt import post_html
        return {"ok": post_html(BOT_TOKEN, SERGE_CHAT_ID, text, already_html=True)}
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
msg = f"{icon} <b>{html.escape(PRETTY_AGENT)} SUGGESTION</b>\n"
msg += f"{'━' * 28}\n\n"
msg += f"<b>Category:</b> {html.escape(category)}\n"
msg += f"<b>Title:</b> {html.escape(title)}\n\n"
if reasoning:
    msg += f"<b>Why it matters:</b>\n{html.escape(reasoning)}\n\n"
if proposed_action:
    msg += f"<b>What I propose:</b>\n{html.escape(proposed_action)}\n\n"
if auto_execute:
    # This is the shell command Serge is approving — the displayed preview
    # MUST match exactly what subprocess.run() below actually executes.
    # Only the DISPLAY is escaped/wrapped in <code>; auto_execute itself is
    # passed to subprocess.run() untouched.
    msg += f"<b>Auto-execute (if yes):</b>\n<code>{html.escape(auto_execute[:400])}</code>\n\n"
msg += f"{'━' * 28}\n"
msg += f"Reply <b>yes</b> to approve · <b>no</b> to drop\n"
msg += f"<i>Times out in {timeout}s</i>"

result = tg_send(msg)
if not result.get("ok"):
    print(f"Failed to send: {result.get('error')}")
    sys.exit(1)

print(f"[{AGENT_ID}/{category}] Sent: {title}")
print(f"Waiting for Serge's reply ({timeout}s max)...")

reply = wait_for_reply()

approved = reply in ("yes", "y", "approved", "approve", "do it", "go",
                     "go ahead", "ok", "okay", "proceed", "confirm", "yep", "yeah")
denied   = reply in ("no", "n", "deny", "denied", "stop", "cancel",
                     "abort", "nope", "pass")

# ── Journal the outcome ───────────────────────────────────────────────────────
try:
    import psycopg2
    conn = psycopg2.connect(
        host=os.environ.get("BAZA_DB_HOST", "localhost"),
        port=int(os.environ.get("BAZA_DB_PORT", "5432")),
        dbname=os.environ.get("BAZA_DB_NAME", "baza_agents"),
        user=os.environ.get("BAZA_DB_USER", "switchhacker"),
        password=os.environ.get("DB_PASSWORD", "baza2026"),
    )
    cur = conn.cursor()
    status = "approved" if approved else ("denied" if denied else "timeout")
    cur.execute(
        """INSERT INTO task_journal
           (agent_id, task_type, task_description, result, success, chat_id, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (AGENT_ID, f"suggestion:{category}",
         f"{title} — {reasoning[:200]}",
         f"{status}: {proposed_action[:200]}",
         approved, str(SERGE_CHAT_ID), datetime.now())
    )
    conn.commit()
    cur.close()
    conn.close()
except Exception as e:
    print(f"Warning: journal log failed: {e}")

# ── Outcome ───────────────────────────────────────────────────────────────────
if approved:
    tg_send(f"✓ <b>Approved.</b> Executing: {html.escape(title)}")
    print("APPROVED")
    if auto_execute:
        try:
            # auto_execute is passed to subprocess.run() EXACTLY as approved above —
            # only the result display below is escaped, not what runs.
            proc = subprocess.run(auto_execute, shell=True,
                                  capture_output=True, text=True, timeout=180)
            output = (proc.stdout or proc.stderr)[:1500]
            tg_send(f"📋 <b>Execution result:</b>\n<pre>{html.escape(output)}</pre>")
            print(f"Executed: {output[:500]}")
        except Exception as e:
            tg_send(f"❌ Execution failed: {html.escape(str(e))}")
            print(f"Exec error: {e}")
elif denied:
    tg_send(f"✗ <b>Dropped.</b> {html.escape(title)}")
    print("DENIED")
else:
    tg_send(f"⏱ <b>Timed out.</b> Dropped: {html.escape(title)}")
    print("TIMEOUT")
