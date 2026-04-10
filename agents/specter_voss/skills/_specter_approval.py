#!/usr/bin/env python3
"""
Shared approval gate helper for all Specter `create_*` skills.
Sends a formatted proposal to Serge via Telegram and waits for yes/no.

Usage (from another skill):
    from _specter_approval import request_approval
    ok = request_approval(
        category="skill",
        title="Create skill: weather_alerts",
        details="...",
        preview="...",
        timeout=300,
    )
"""
import os
import json
import time
import socket
import urllib.request

# Force IPv6 preference
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET6] or _orig_gai(*a, **k)

BOT_TOKEN = os.environ.get("TELEGRAM_SPECTER_VOSS", "")
SERGE_CHAT_ID = os.environ.get("SERGE_CHAT_ID", "8551331144")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

ICONS = {
    "skill": "🛠",
    "memory": "🧠",
    "knowledge": "📚",
    "cron": "⏲",
    "task": "📋",
    "tool": "⚙️",
    "delegation": "👥",
}


def _tg(method, params):
    try:
        req = urllib.request.Request(
            f"{API}/{method}",
            data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _tg_updates(offset=0):
    try:
        url = f"{API}/getUpdates?offset={offset}&timeout=5"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=10) as r:
            return json.loads(r.read()).get("result", [])
    except Exception:
        return []


def request_approval(category, title, details="", preview="", timeout=300):
    """Send an approval request to Serge and block until yes/no/timeout.
    Returns True on approve, False on deny/timeout."""
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_SPECTER_VOSS not set")
        return False

    icon = ICONS.get(category, "•")
    msg = f"{icon} <b>SPECTER CREATE REQUEST</b>\n{'━'*28}\n\n"
    msg += f"<b>Type:</b> {category}\n"
    msg += f"<b>Title:</b> {title}\n"
    if details:
        msg += f"\n<b>Details:</b>\n{details[:800]}\n"
    if preview:
        msg += f"\n<b>Preview:</b>\n<pre>{preview[:1500]}</pre>\n"
    msg += f"\n{'━'*28}\n"
    msg += "Reply <b>yes</b> to create · <b>no</b> to cancel\n"
    msg += f"<i>Times out in {timeout}s</i>"

    result = _tg("sendMessage", {"chat_id": SERGE_CHAT_ID, "text": msg[:4000], "parse_mode": "HTML"})
    if not result.get("ok"):
        print(f"ERROR: failed to send approval request: {result.get('error')}")
        return False

    # Poll for reply
    updates = _tg_updates()
    offset = (updates[-1]["update_id"] + 1) if updates else 0
    start = time.time()
    while time.time() - start < timeout:
        updates = _tg_updates(offset)
        for u in updates:
            offset = u["update_id"] + 1
            m = u.get("message", {})
            text = m.get("text", "").strip().lower()
            chat_id = str(m.get("chat", {}).get("id", ""))
            if chat_id == str(SERGE_CHAT_ID) and text:
                if text in ("yes", "y", "approved", "approve", "do it", "go", "ok", "proceed", "confirm"):
                    _tg("sendMessage", {"chat_id": SERGE_CHAT_ID,
                                        "text": f"✓ Approved: {title}",
                                        "parse_mode": "HTML"})
                    return True
                if text in ("no", "n", "deny", "denied", "cancel", "abort", "stop", "nope"):
                    _tg("sendMessage", {"chat_id": SERGE_CHAT_ID,
                                        "text": f"✗ Denied: {title}",
                                        "parse_mode": "HTML"})
                    return False
        time.sleep(3)

    _tg("sendMessage", {"chat_id": SERGE_CHAT_ID,
                        "text": f"⏱ Timed out: {title}",
                        "parse_mode": "HTML"})
    return False


def log_creation(agent_id, category, title, approved, metadata=None):
    """Log the creation to task_journal for team_pulse visibility."""
    try:
        import psycopg2
        from datetime import datetime
        conn = psycopg2.connect(
            host=os.environ.get("BAZA_DB_HOST", "100.127.118.103"),
            port=5432,
            dbname=os.environ.get("BAZA_DB_NAME", "baza_agents"),
            user=os.environ.get("BAZA_DB_USER", "switchhacker"),
            password=os.environ.get("DB_PASSWORD", "baza2026"),
        )
        cur = conn.cursor()
        desc = title + (f" :: {json.dumps(metadata)[:200]}" if metadata else "")
        status = "created" if approved else "denied"
        cur.execute(
            """INSERT INTO task_journal (agent_id, task_type, task_description, result, success, chat_id, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (agent_id, f"create_{category}", desc[:500], status, approved, str(SERGE_CHAT_ID), datetime.now())
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Warning: journal log failed: {e}")
