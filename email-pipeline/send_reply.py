#!/usr/bin/env python3
"""
Baza Email Pipeline — Stage 4: Send Reply
Usage: python send_reply.py <email_id> [custom reply text]
       python send_reply.py <email_id>   → sends the drafted reply
       python send_reply.py <email_id> "Custom text here" → sends custom text
"""
import os
import sys
import sqlite3
import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import googleapiclient.discovery

logging.basicConfig(level=logging.INFO, format='%(asctime)s [send_reply] %(message)s')
log = logging.getLogger(__name__)

DIR           = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK_DIR = os.path.dirname(DIR)
TOKEN         = os.path.join(DIR, 'token.json')
DB_PATH       = os.path.join(FRAMEWORK_DIR, 'dashboard', 'baza_projects.db')
SCOPES        = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
]


def get_creds():
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN, 'w') as f:
            f.write(creds.to_json())
    return creds


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def run(email_id, custom_text=None):
    db    = get_db()
    email = db.execute('SELECT * FROM emails WHERE id=?', (email_id,)).fetchone()

    if not email:
        log.error(f"Email not found: {email_id}")
        sys.exit(1)

    email = dict(email)
    reply_text = custom_text or email.get('suggested_reply', '')

    if not reply_text:
        log.error("No reply text — provide custom text or run summarize first.")
        sys.exit(1)

    creds   = get_creds()
    service = googleapiclient.discovery.build('gmail', 'v1', credentials=creds)

    # Build MIME reply
    msg = MIMEMultipart()
    msg['To']         = email['from_addr']
    msg['Subject']    = f"Re: {email['subject']}"
    msg['In-Reply-To'] = email['gmail_id']
    msg['References']  = email['gmail_id']
    msg.attach(MIMEText(reply_text, 'plain'))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')

    try:
        result = service.users().messages().send(
            userId='me',
            body={'raw': raw, 'threadId': email['thread_id']}
        ).execute()
        log.info(f"✅ Reply sent. Gmail message ID: {result['id']}")
    except Exception as e:
        log.error(f"Failed to send: {e}")
        db.close()
        sys.exit(1)

    # Mark as sent
    db.execute("UPDATE emails SET status='sent', updated_at=datetime('now') WHERE id=?", (email_id,))
    db.commit()
    db.close()
    log.info(f"Email {email_id} marked as sent.")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python send_reply.py <email_id> [custom reply text]")
        sys.exit(1)
    email_id    = sys.argv[1]
    custom_text = sys.argv[2] if len(sys.argv) > 2 else None
    run(email_id, custom_text)
