"""Minimal pytest fixtures for dashboard tests.

Builds a Flask test app by importing the email_studio blueprint and
wiring it to a temp SQLite DB — no live Gmail calls.
"""
import os
import sqlite3
import sys
import tempfile

import pytest
from flask import Flask

# Make sure `dashboard/` is importable as a flat package from here.
DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)

import email_studio


@pytest.fixture
def tmp_db(tmp_path):
    """Create a minimal SQLite DB with the email schema and return its path."""
    db_path = str(tmp_path / "test_emails.db")
    con = sqlite3.connect(db_path)
    con.executescript("""
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
            updated_at TEXT DEFAULT (datetime('now')),
            is_unread INTEGER DEFAULT 1,
            is_starred INTEGER DEFAULT 0,
            category TEXT,
            action_items TEXT,
            ai_summary TEXT,
            last_synced TEXT,
            history_id TEXT,
            account_id TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
            gmail_id UNINDEXED, subject, from_addr, body,
            tokenize='porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS email_accounts (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            label TEXT,
            token_path TEXT NOT NULL,
            is_active INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            last_used TEXT
        );
    """)
    con.commit()
    con.close()
    return db_path


@pytest.fixture
def app(tmp_db, monkeypatch):
    """Flask test app with email_studio blueprint, using a temp DB."""
    monkeypatch.setenv("BAZA_DASHBOARD_DB", tmp_db)

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret"

    # Re-register a fresh blueprint instance to avoid state bleed between tests.
    # email_bp is a module-level Blueprint — safe to register on a fresh app.
    flask_app.register_blueprint(email_studio.email_bp)

    yield flask_app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()
