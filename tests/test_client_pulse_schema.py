"""Regression test for the ahb_chat_messages schema-drift bug in
agents/nova_sterling/crons/client_pulse.py's collect_data().

Background: ahb_chat_messages used to have visitor_name/message columns.
Production schema drifted to chat_id/role/content/agent_id (visitor_name/
message no longer exist at all) and collect_data() kept querying the old
columns, crashing every run with sqlite3.OperationalError: no such column:
visitor_name. Nova's daily client-pulse cron was failing silently in
production as a result.

Fix: query the real columns (role='user' rows are the "the client said
this" proxy, since there's no visitor_name column anymore -- role='assistant'
is Nova's own reply), wrapped in a try/except OperationalError that degrades
to "No recent chats" instead of raising, so a *future* drift on this table
still can't crash the whole cron.

DDL below is copied verbatim from `sqlite3 dashboard/baza_projects.db
".schema <table>"` (same convention as tests/test_invoice_followup.py /
tests/test_weather_watch.py).
"""
import importlib
import os
import sqlite3
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


AHB_CLIENTS_DDL = """
CREATE TABLE ahb_clients (
            id TEXT PRIMARY KEY,
            name TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            city TEXT DEFAULT 'Philadelphia',
            source TEXT,
            status TEXT DEFAULT 'lead',
            notes TEXT,
            assigned_agent TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        , company TEXT DEFAULT '', tags TEXT DEFAULT '');
"""

AHB_PROJECTS_DDL = """
CREATE TABLE ahb_projects (
            id TEXT PRIMARY KEY,
            client_id TEXT,
            title TEXT,
            address TEXT,
            scope TEXT,
            description TEXT,
            budget_low REAL,
            budget_high REAL,
            status TEXT DEFAULT 'estimate',
            start_date TEXT,
            end_date TEXT,
            assigned_agents TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        , acquisition_type TEXT DEFAULT '', value REAL DEFAULT 0, client_email TEXT DEFAULT '', contact_info TEXT DEFAULT '', location TEXT DEFAULT '', client_name TEXT DEFAULT '', year TEXT DEFAULT '', latitude REAL, longitude REAL, geocoded_at TEXT, commission_pct REAL DEFAULT 10, commission_value REAL DEFAULT 0, commission_beneficiary TEXT DEFAULT '', terms_conditions TEXT, payment_terms TEXT DEFAULT '');
"""

AHB_INVOICES_DDL = """
CREATE TABLE ahb_invoices (
            id TEXT PRIMARY KEY,
            client_id TEXT,
            project_id TEXT,
            invoice_number TEXT,
            line_items TEXT,
            subtotal REAL,
            tax REAL,
            total REAL,
            status TEXT DEFAULT 'draft',
            due_date TEXT,
            paid_date TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        , client_name TEXT DEFAULT '', project_name TEXT DEFAULT '', terms TEXT DEFAULT '', date TEXT DEFAULT '', parent_invoice_id TEXT DEFAULT '', is_change_order INTEGER DEFAULT 0, overdue_since TEXT DEFAULT '', overdue_interest_per_week REAL DEFAULT 50, company_name TEXT DEFAULT 'All Home Building Co', contractor_name TEXT DEFAULT 'Sergey Tkach', client_address TEXT DEFAULT '', client_email TEXT DEFAULT '', client_phone TEXT DEFAULT '', project_address TEXT DEFAULT '', year TEXT DEFAULT '', is_primary INTEGER DEFAULT 0, milestone_label TEXT DEFAULT '', milestone_index INTEGER DEFAULT -1, amount_due REAL, terms_snapshot TEXT DEFAULT '');
"""

# Real current schema (post schema-drift) -- no visitor_name/message columns.
AHB_CHAT_MESSAGES_DDL = """
CREATE TABLE ahb_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            role TEXT,
            content TEXT,
            agent_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
"""

# The schema client_pulse.py used to (wrongly) assume. Kept only to prove the
# try/except degrades gracefully if this table drifts again in some other
# direction -- the cron must never die on ahb_chat_messages.
LEGACY_CHAT_MESSAGES_DDL = """
CREATE TABLE ahb_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visitor_name TEXT,
            message TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
"""


def _make_biz_db(tmp_path, chat_messages_ddl=AHB_CHAT_MESSAGES_DDL):
    biz_db_path = tmp_path / "baza_projects.db"
    conn = sqlite3.connect(str(biz_db_path))
    conn.executescript(AHB_CLIENTS_DDL)
    conn.executescript(AHB_PROJECTS_DDL)
    conn.executescript(AHB_INVOICES_DDL)
    conn.executescript(chat_messages_ddl)
    conn.commit()
    conn.close()
    return str(biz_db_path)


def _biz_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _fresh_client_pulse(tmp_path, monkeypatch, chat_messages_ddl=AHB_CHAT_MESSAGES_DDL):
    """Fresh agents.cron_helpers (DB_PATH -> tmp file) + fresh
    agents.nova_sterling.crons.client_pulse import, mirroring
    tests/test_invoice_followup.py's `env` fixture convention."""
    for mod in ("agents.cron_helpers", "agents.nova_sterling.crons.client_pulse"):
        sys.modules.pop(mod, None)

    ch = importlib.import_module("agents.cron_helpers")
    biz_db_path = _make_biz_db(tmp_path, chat_messages_ddl=chat_messages_ddl)
    monkeypatch.setattr(ch, "DB_PATH", biz_db_path)

    cp = importlib.import_module("agents.nova_sterling.crons.client_pulse")
    return cp, biz_db_path


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cp, biz_db_path = _fresh_client_pulse(tmp_path, monkeypatch)
    return {"cp": cp, "biz_db": biz_db_path}


def test_collect_data_returns_without_raising_on_empty_tables(env):
    """The headline regression case: collect_data() against the REAL current
    ahb_chat_messages DDL (chat_id/role/content/agent_id -- no visitor_name/
    message columns at all) must not raise sqlite3.OperationalError."""
    data = env["cp"].collect_data()
    assert isinstance(data, str)
    assert "CLIENT PULSE" in data
    assert "No recent chats" in data


def test_collect_data_surfaces_recent_client_chat_content(env):
    """role='user' rows are the "client said this" proxy (no visitor_name
    column exists anymore) -- their content should show up in the report,
    and the query must tolerate rows null in optional columns."""
    conn = _biz_conn(env["biz_db"])
    conn.execute(
        "INSERT INTO ahb_chat_messages (chat_id, role, content, agent_id) VALUES (?,?,?,?)",
        ("chat-abc12345", "user", "when can someone come look at my kitchen", None),
    )
    conn.execute(
        "INSERT INTO ahb_chat_messages (chat_id, role, content, agent_id) VALUES (?,?,?,?)",
        ("chat-abc12345", "assistant", "We can schedule a consult this week!", "nova_sterling"),
    )
    conn.commit()
    conn.close()

    data = env["cp"].collect_data()
    assert "when can someone come look at my kitchen" in data
    assert "No recent chats" not in data


def test_collect_data_tolerates_null_chat_id(env):
    """chat_id has no NOT NULL constraint -- formatting must not blow up on
    a NULL chat_id (this lives outside the query's try/except, so it has to
    be handled by the formatting itself, not just query error handling)."""
    conn = _biz_conn(env["biz_db"])
    conn.execute(
        "INSERT INTO ahb_chat_messages (chat_id, role, content) VALUES (?,?,?)",
        (None, "user", "hello from an anonymous session"),
    )
    conn.commit()
    conn.close()

    data = env["cp"].collect_data()
    assert "hello from an anonymous session" in data


def test_collect_data_degrades_instead_of_crashing_on_further_schema_drift(tmp_path, monkeypatch):
    """If ahb_chat_messages drifts again to some other shape client_pulse.py
    doesn't expect, collect_data() must degrade to "No recent chats" rather
    than raising -- the whole point of the fix is the cron can never die on
    this table again."""
    cp, biz_db_path = _fresh_client_pulse(tmp_path, monkeypatch, chat_messages_ddl=LEGACY_CHAT_MESSAGES_DDL)

    conn = _biz_conn(biz_db_path)
    conn.execute(
        "INSERT INTO ahb_chat_messages (visitor_name, message) VALUES (?,?)",
        ("Jim", "hello there"),
    )
    conn.commit()
    conn.close()

    data = cp.collect_data()
    assert isinstance(data, str)
    assert "No recent chats" in data


def test_collect_data_still_reports_clients_and_invoices_when_chats_empty(env):
    """The chat-messages fix shouldn't regress the rest of collect_data()'s
    output -- client overview / active projects / overdue invoices sections
    must still populate correctly."""
    conn = _biz_conn(env["biz_db"])
    conn.execute(
        "INSERT INTO ahb_clients (id, name, status) VALUES (?,?,?)",
        ("c1", "Jim Sora", "active"),
    )
    conn.execute(
        "INSERT INTO ahb_projects (id, client_id, title, status) VALUES (?,?,?,?)",
        ("p1", "c1", "Wine Cellar Build", "In Progress"),
    )
    conn.execute(
        "INSERT INTO ahb_invoices (id, client_name, project_name, total, status) VALUES (?,?,?,?,?)",
        ("i1", "Jim Sora", "Wine Cellar Build", 4200.0, "Overdue"),
    )
    conn.commit()
    conn.close()

    data = env["cp"].collect_data()
    assert "Jim Sora" in data
    assert "Wine Cellar Build" in data
    assert "OVERDUE INVOICES" in data
    assert "No recent chats" in data
