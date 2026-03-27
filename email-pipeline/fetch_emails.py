#!/usr/bin/env python3
"""
Baza Email Pipeline — Stage 1: Fetch
Pulls new Gmail messages since last history_id → stores in local SQLite.
Runs every 15 min via systemd timer.
"""
import os
import sys
import json
import sqlite3
import base64
import logging
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import googleapiclient.discovery

logging.basicConfig(level=logging.INFO, format='%(asctime)s [fetch_emails] %(message)s')
log = logging.getLogger(__name__)

DIR      = os.path.dirname(os.path.abspath(__file__))
TOKEN    = os.path.join(DIR, 'token.json')
SCOPES   = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
]

FRAMEWORK_DIR = os.path.dirname(DIR)
DB_PATH  = os.path.join(FRAMEWORK_DIR, 'dashboard', 'baza_projects.db')
STATE_FILE = os.path.join(DIR, 'sync_state.json')


def get_creds():
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN, 'w') as f:
            f.write(creds.to_json())
    return creds


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Ensure emails table exists
    conn.execute('''
        CREATE TABLE IF NOT EXISTS emails (
            id TEXT PRIMARY KEY,
            gmail_id TEXT UNIQUE,
            thread_id TEXT,
            from_addr TEXT,
            to_addr TEXT,
            subject TEXT,
            body_snippet TEXT,
            full_body TEXT,
            received_at TEXT,
            status TEXT DEFAULT 'new',
            summary TEXT,
            suggested_reply TEXT,
            priority TEXT DEFAULT 'normal',
            labels TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.commit()
    return conn


def decode_body(payload):
    """Extract plain text body from Gmail message payload."""
    body = ''
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' and part.get('body', {}).get('data'):
                body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='replace')
                break
            elif part['mimeType'].startswith('multipart/'):
                body = decode_body(part)
                if body:
                    break
    elif payload.get('body', {}).get('data'):
        body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='replace')
    return body


def run():
    if not os.path.exists(TOKEN):
        log.error("No token.json found. Run gmail_auth.py first.")
        sys.exit(1)

    creds   = get_creds()
    service = googleapiclient.discovery.build('gmail', 'v1', credentials=creds)
    state   = load_state()
    db      = get_db()

    # Get current profile
    profile = service.users().getProfile(userId='me').execute()
    current_history_id = str(profile['historyId'])

    if 'history_id' not in state:
        # First run — just save state, nothing to fetch yet
        state['history_id']     = current_history_id
        state['email_address']  = profile['emailAddress']
        save_state(state)
        log.info(f"First run — initialized. Email: {profile['emailAddress']}, historyId: {current_history_id}")
        db.close()
        return

    prev_history_id = state['history_id']
    log.info(f"Fetching history since {prev_history_id}")

    # Fetch history
    try:
        history_resp = service.users().history().list(
            userId='me',
            startHistoryId=prev_history_id,
            historyTypes=['messageAdded']
        ).execute()
    except Exception as e:
        log.error(f"History fetch failed: {e}")
        # Reset if history is invalid (too old)
        state['history_id'] = current_history_id
        save_state(state)
        db.close()
        return

    history = history_resp.get('history', [])
    if not history:
        log.info("No new messages.")
        state['history_id'] = current_history_id
        save_state(state)
        db.close()
        return

    # Collect unique message IDs
    message_ids = set()
    for entry in history:
        for msg in entry.get('messagesAdded', []):
            message_ids.add(msg['message']['id'])

    log.info(f"Found {len(message_ids)} new message(s)")
    added = 0

    for msg_id in message_ids:
        # Check if already in DB
        existing = db.execute('SELECT id FROM emails WHERE gmail_id=?', (msg_id,)).fetchone()
        if existing:
            continue

        try:
            msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
        except Exception as e:
            log.warning(f"Could not fetch message {msg_id}: {e}")
            continue

        headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
        from_addr  = headers.get('From', '')
        to_addr    = headers.get('To', '')
        subject    = headers.get('Subject', '(no subject)')
        date_str   = headers.get('Date', '')
        snippet    = msg.get('snippet', '')
        thread_id  = msg.get('threadId', '')
        full_body  = decode_body(msg.get('payload', {}))
        labels     = ','.join(msg.get('labelIds', []))

        import uuid
        record_id = str(uuid.uuid4())

        db.execute('''
            INSERT OR IGNORE INTO emails
            (id, gmail_id, thread_id, from_addr, to_addr, subject, body_snippet, full_body, received_at, status, priority, labels)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 'normal', ?)
        ''', (record_id, msg_id, thread_id, from_addr, to_addr, subject, snippet, full_body, date_str, labels))
        added += 1
        log.info(f"  + Stored: [{subject[:50]}] from {from_addr[:40]}")

    db.commit()
    db.close()

    # Update state
    state['history_id'] = current_history_id
    save_state(state)
    log.info(f"Done. {added} new email(s) stored.")


if __name__ == '__main__':
    run()
