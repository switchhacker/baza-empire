#!/usr/bin/env python3
"""
Baza Email Pipeline — Stage 2: Summarize + Draft
Reads 'new' emails from local SQLite → Ollama → Telegram notification to Serge.
Runs every 15 min (offset 7 min after fetch) via systemd timer.
"""
import os
import sys
import json
import sqlite3
import requests
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [summarize] %(message)s')
log = logging.getLogger(__name__)

DIR           = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK_DIR = os.path.dirname(DIR)
DB_PATH       = os.path.join(FRAMEWORK_DIR, 'dashboard', 'baza_projects.db')

# Load secrets
def load_env():
    env = {}
    env_path = os.path.join(FRAMEWORK_DIR, 'configs', 'secrets.env')
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env

ENV            = load_env()
OLLAMA_URL     = ENV.get('OLLAMA_URL', 'http://localhost:11434')
OLLAMA_MODEL   = ENV.get('OLLAMA_MODEL', 'mistral-small:22b')
TELEGRAM_TOKEN = ENV.get('TELEGRAM_SIMON_BATELY', '')   # use Simon's bot for email alerts
SERGE_CHAT_ID  = ENV.get('SERGE_CHAT_ID', '8551331144')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ollama_generate(prompt):
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=120
        )
        return resp.json().get('response', '').strip()
    except Exception as e:
        log.error(f"Ollama error: {e}")
        return ''


def send_telegram(text):
    if not TELEGRAM_TOKEN or not SERGE_CHAT_ID:
        log.warning("Telegram not configured — skipping notification")
        return
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": SERGE_CHAT_ID, "text": chunk},
                timeout=15
            )
        except Exception as e:
            log.error(f"Telegram send error: {e}")


def run():
    db = get_db()
    new_emails = db.execute(
        "SELECT * FROM emails WHERE status='new' ORDER BY received_at DESC LIMIT 10"
    ).fetchall()

    if not new_emails:
        log.info("No new emails to summarize.")
        db.close()
        return

    log.info(f"Summarizing {len(new_emails)} email(s)...")

    summaries = []
    for email in new_emails:
        email = dict(email)
        prompt = f"""Email received at Baza Empire (contactahbco@gmail.com):

From: {email.get('from_addr', '')}
Subject: {email.get('subject', '')}
Body: {(email.get('full_body') or email.get('body_snippet', ''))[:1500]}

Tasks:
1. Summarize this email in 1-2 sentences (key points only, plain text)
2. Write a short professional reply (2-3 sentences, plain text, no placeholders)

Format exactly like this:
SUMMARY: [your summary here]
REPLY: [your reply here]"""

        log.info(f"  Processing: {email.get('subject', '')[:50]}")
        output = ollama_generate(prompt)

        summary = ''
        reply   = ''
        if output:
            import re
            sm = re.search(r'SUMMARY:\s*(.+?)(?=REPLY:|$)', output, re.DOTALL)
            rm = re.search(r'REPLY:\s*(.+?)$', output, re.DOTALL)
            summary = sm.group(1).strip() if sm else output[:200]
            reply   = rm.group(1).strip() if rm else ''

        # Update DB
        db.execute('''
            UPDATE emails SET summary=?, suggested_reply=?, status='reply_drafted', updated_at=datetime('now')
            WHERE id=?
        ''', (summary, reply, email['id']))
        db.commit()

        summaries.append({**email, 'summary': summary, 'suggested_reply': reply})
        log.info(f"  ✓ Done: {email.get('subject', '')[:50]}")

    db.close()

    # Build Telegram notification
    msg  = "📧 New Emails — Baza Empire\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

    for e in summaries:
        msg += f"📌 From: {e.get('from_addr', '')}\n"
        msg += f"📝 Subject: {e.get('subject', '')}\n\n"
        if e.get('summary'):
            msg += f"Summary: {e['summary']}\n\n"
        if e.get('suggested_reply'):
            msg += f"Draft Reply:\n{e['suggested_reply']}\n\n"
        msg += f"ID: {e['id']}\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

    msg += "Reply APPROVE [ID] or ignore to skip."

    send_telegram(msg)
    log.info(f"Telegram notification sent for {len(summaries)} email(s).")


if __name__ == '__main__':
    run()
