"""Tests for agents/phil_hass/crons/invoice_followup.py (Task 14 of the
cron-improvements plan, item 23).

All external calls are mocked: LLM (agents.cron_helpers.ollama_generate) and
the suggest_action.py approval-card delivery
(agents.phil_hass.crons.invoice_followup.send_suggestion). No test spawns a
real subprocess or sends a real Telegram message. DBs are tmp SQLite files --
ahb_invoices' DDL is copied verbatim from `sqlite3 dashboard/baza_projects.db
".schema ahb_invoices"` (ahb_projects/ahb_clients likewise), same approach as
tests/test_weather_watch.py and tests/test_geocode.py. cron_health.db is
pointed at a tmp path via BAZA_CRON_HEALTH_DB + a fresh reimport (mirrors
tests/test_cron_helpers_routing.py's `ch` fixture).
"""
import datetime
import importlib
import sqlite3
import sys
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


# ── Real DDL, copied via `sqlite3 dashboard/baza_projects.db '.schema ...'` ──

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

TODAY = "2026-07-02"
NOW = datetime.datetime(2026, 7, 2, 8, 30, 0)


def insert_invoice(conn, id, invoice_number="INV-0001", status="Sent", due_date="2026-06-01",
                    total=1000.0, amount_due=None, client_name="", client_email="",
                    project_id=None, client_id=None, project_name=""):
    conn.execute(
        "INSERT INTO ahb_invoices (id, invoice_number, status, due_date, total, amount_due, "
        "client_name, client_email, project_id, client_id, project_name) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (id, invoice_number, status, due_date, total, amount_due, client_name, client_email,
         project_id, client_id, project_name),
    )
    conn.commit()


def insert_project(conn, id, title="Test Project", client_name="", client_email="", client_id=None):
    conn.execute(
        "INSERT INTO ahb_projects (id, title, client_name, client_email, client_id) "
        "VALUES (?,?,?,?,?)",
        (id, title, client_name, client_email, client_id),
    )
    conn.commit()


def insert_client(conn, id, name="", email=""):
    conn.execute("INSERT INTO ahb_clients (id, name, email) VALUES (?,?,?)", (id, name, email))
    conn.commit()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh core.cron_health_db (tmp path) + agents.cron_helpers (business
    DB_PATH pointed at a tmp file) + a fresh agents.phil_hass.crons.invoice_followup
    import, so its module-level `chdb`/`get_db`/`send_suggestion` etc. bindings
    all resolve against this test's tmp state. Mirrors
    tests/test_weather_watch.py's `env` fixture."""
    ch_db_path = tmp_path / "cron_health.db"
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", str(ch_db_path))

    for mod in (
        "core.cron_health_db",
        "agents.cron_helpers",
        "agents.phil_hass.crons.invoice_followup",
    ):
        sys.modules.pop(mod, None)

    chdb = importlib.import_module("core.cron_health_db")
    chdb.init()

    ch = importlib.import_module("agents.cron_helpers")

    biz_db_path = tmp_path / "baza_projects.db"
    conn = sqlite3.connect(str(biz_db_path))
    conn.executescript(AHB_INVOICES_DDL)
    conn.executescript(AHB_PROJECTS_DDL)
    conn.executescript(AHB_CLIENTS_DDL)
    conn.commit()
    conn.close()

    monkeypatch.setattr(ch, "DB_PATH", str(biz_db_path))

    iff = importlib.import_module("agents.phil_hass.crons.invoice_followup")

    return {"iff": iff, "ch": ch, "chdb": chdb, "biz_db": str(biz_db_path)}


@pytest.fixture()
def suggested(monkeypatch, env):
    """Recorder standing in for invoice_followup.send_suggestion -- the seam
    this cron uses to hand a drafted reminder to the suggest_action.py
    approval flow. No test lets a real subprocess/Telegram card fire."""
    calls = []

    def fake_send_suggestion(args, agent_id="phil_hass", timeout=None):
        calls.append(args)
        return True

    monkeypatch.setattr(env["iff"], "send_suggestion", fake_send_suggestion)
    return calls


@pytest.fixture()
def ollama(monkeypatch, env):
    """Patch agents.cron_helpers.ollama_generate. invoice_followup calls the
    bare `ollama_generate` name (bound into its own module globals via the
    `from agents.cron_helpers import *` wildcard import), so patching it on
    the invoice_followup module itself is what actually takes effect for
    calls made from within that module -- mirrors test_weather_watch.py's/
    test_cron_helpers_routing.py's convention of patching the *consuming*
    module's binding, not the origin module's."""
    calls = []

    def _set(fn):
        def wrapped(*a, **k):
            calls.append(a)
            return fn(*a, **k)
        monkeypatch.setattr(env["iff"], "ollama_generate", wrapped)

    return calls, _set


def _biz_conn(env):
    conn = sqlite3.connect(env["biz_db"])
    conn.row_factory = sqlite3.Row
    return conn


GOOD_DRAFT = (
    "Hi there,\n\nThis is a friendly reminder that invoice {inv} is now past due. "
    "The outstanding balance is ${amt}. Please let us know if you have any questions "
    "or need to arrange payment.\n\nThank you,\nPhil Hass, AHBCO LLC"
)


# ── test_selects_only_overdue_unpaid ────────────────────────────────────────

def test_selects_only_overdue_unpaid(env):
    iff = env["iff"]
    conn = _biz_conn(env)
    # Included: Sent, past due, balance outstanding.
    insert_invoice(conn, "inv-overdue", invoice_number="INV-OVERDUE", status="Sent",
                    due_date="2026-06-01", total=5000.0)
    # Excluded: Paid.
    insert_invoice(conn, "inv-paid", invoice_number="INV-PAID", status="Paid",
                    due_date="2026-06-01", total=5000.0)
    # Excluded: paid (lowercase, real data has this drift).
    insert_invoice(conn, "inv-paid-lc", invoice_number="INV-PAID-LC", status="paid",
                    due_date="2026-06-01", total=5000.0)
    # Excluded: Draft status.
    insert_invoice(conn, "inv-draft", invoice_number="INV-DRAFT", status="draft",
                    due_date="2026-06-01", total=5000.0)
    # Excluded: Void status.
    insert_invoice(conn, "inv-void", invoice_number="INV-VOID", status="Void",
                    due_date="2026-06-01", total=5000.0)
    # Excluded: not yet due (due_date in the future relative to TODAY).
    insert_invoice(conn, "inv-future", invoice_number="INV-FUTURE", status="Sent",
                    due_date="2026-08-01", total=5000.0)
    # Excluded: due today exactly (not yet overdue).
    insert_invoice(conn, "inv-duetoday", invoice_number="INV-DUETODAY", status="Sent",
                    due_date=TODAY, total=5000.0)
    # Excluded: no due_date on file.
    insert_invoice(conn, "inv-nodue", invoice_number="INV-NODUE", status="Sent",
                    due_date="", total=5000.0)
    # Excluded: balance fully paid down (amount_due explicitly 0).
    insert_invoice(conn, "inv-zerobalance", invoice_number="INV-ZEROBAL", status="Sent",
                    due_date="2026-06-01", total=5000.0, amount_due=0)
    conn.close()

    rows = iff._get_overdue_invoices(_biz_conn(env), TODAY)
    ids = {r["id"] for r in rows}
    assert ids == {"inv-overdue"}


def test_amount_due_falls_back_to_total_when_unset(env):
    iff = env["iff"]
    conn = _biz_conn(env)
    insert_invoice(conn, "inv-noamtdue", invoice_number="INV-1", status="Sent",
                    due_date="2026-06-01", total=1234.5, amount_due=None)
    conn.close()

    rows = iff._get_overdue_invoices(_biz_conn(env), TODAY)
    assert len(rows) == 1
    invoice = iff._row_to_invoice(rows[0], NOW)
    assert invoice["amount"] == 1234.5


def test_amount_due_used_when_partially_paid(env):
    iff = env["iff"]
    conn = _biz_conn(env)
    insert_invoice(conn, "inv-partial", invoice_number="INV-2", status="Sent",
                    due_date="2026-06-01", total=28537.29, amount_due=7000.0)
    conn.close()

    rows = iff._get_overdue_invoices(_biz_conn(env), TODAY)
    invoice = iff._row_to_invoice(rows[0], NOW)
    assert invoice["amount"] == 7000.0


def test_client_name_email_join_falls_back_through_project_and_client(env):
    iff = env["iff"]
    conn = _biz_conn(env)
    insert_client(conn, "client-1", name="Client Table Name", email="client-table@example.com")
    insert_project(conn, "proj-1", title="Kitchen Remodel", client_name="", client_email="",
                    client_id="client-1")
    insert_invoice(conn, "inv-join", invoice_number="INV-JOIN", status="Sent",
                    due_date="2026-06-01", total=2000.0, client_name="", client_email="",
                    project_id="proj-1", client_id=None)
    conn.close()

    rows = iff._get_overdue_invoices(_biz_conn(env), TODAY)
    assert len(rows) == 1
    invoice = iff._row_to_invoice(rows[0], NOW)
    assert invoice["client_name"] == "Client Table Name"
    assert invoice["client_email"] == "client-table@example.com"
    assert invoice["project_name"] == "Kitchen Remodel"


def test_client_name_email_prefer_invoice_level_when_present(env):
    iff = env["iff"]
    conn = _biz_conn(env)
    insert_client(conn, "client-2", name="Fallback Name", email="fallback@example.com")
    insert_project(conn, "proj-2", title="Bathroom", client_name="Project Name",
                    client_email="project@example.com", client_id="client-2")
    insert_invoice(conn, "inv-preferred", invoice_number="INV-PREF", status="Sent",
                    due_date="2026-06-01", total=2000.0,
                    client_name="Invoice Name", client_email="invoice@example.com",
                    project_id="proj-2", client_id="client-2")
    conn.close()

    rows = iff._get_overdue_invoices(_biz_conn(env), TODAY)
    invoice = iff._row_to_invoice(rows[0], NOW)
    assert invoice["client_name"] == "Invoice Name"
    assert invoice["client_email"] == "invoice@example.com"


# ── test_weekly_dedup ───────────────────────────────────────────────────────

def test_weekly_dedup(env, suggested, ollama):
    iff = env["iff"]
    _, set_ollama = ollama
    set_ollama(lambda *a, **k: GOOD_DRAFT.format(inv="INV-DEDUP", amt="5,000.00"))

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-dedup", invoice_number="INV-DEDUP", status="Sent",
                    due_date="2026-06-01", total=5000.0)
    conn.close()

    iff.main(now=NOW)
    assert len(suggested) == 1

    # Second run same day (well within the 168h/7-day renotify window) must
    # NOT draft or suggest again for the same invoice.
    iff.main(now=NOW)
    assert len(suggested) == 1


def test_weekly_dedup_fires_again_after_renotify_window(env, suggested, ollama):
    iff = env["iff"]
    chdb = env["chdb"]
    _, set_ollama = ollama
    set_ollama(lambda *a, **k: GOOD_DRAFT.format(inv="INV-DEDUP2", amt="5,000.00"))

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-dedup2", invoice_number="INV-DEDUP2", status="Sent",
                    due_date="2026-06-01", total=5000.0)
    conn.close()

    iff.main(now=NOW)
    assert len(suggested) == 1

    # Backdate last_seen past the 168h window and confirm it fires again.
    key = "invfu:inv-dedup2"
    backdated = (NOW - datetime.timedelta(hours=169)).isoformat(timespec="seconds")
    with chdb.connect() as c:
        c.execute("UPDATE cron_alert_state SET last_seen = ? WHERE key = ?", (backdated, key))
        c.commit()

    later = NOW + datetime.timedelta(hours=169)
    iff.main(now=later)
    assert len(suggested) == 2


def test_dedup_gate_checked_before_llm_call(env, suggested, ollama):
    """Controller note: should_alert must gate BEFORE spending an LLM call --
    a still-in-window invoice must not draft at all."""
    iff = env["iff"]
    calls, set_ollama = ollama

    def boom(*a, **k):
        raise AssertionError("ollama_generate must not be called for a deduped invoice")
    set_ollama(boom)

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-prededuped", invoice_number="INV-PRE", status="Sent",
                    due_date="2026-06-01", total=5000.0)
    conn.close()

    # Pre-seed the dedup key as already-alerted (within window).
    env["chdb"].should_alert("invfu:inv-prededuped", 168, {"title": "seed"})

    iff.main(now=NOW)
    assert len(suggested) == 0
    assert len(calls) == 0


# ── test_draft_contains_invoice_facts ───────────────────────────────────────

def test_draft_prompt_contains_invoice_facts():
    from agents.phil_hass.crons import invoice_followup as iff
    invoice = {
        "invoice_number": "INV-00093", "amount": 54000.0, "due_date": "2026-06-01",
        "days_overdue": 31, "client_name": "Madhi", "project_name": "Deck Rebuild",
    }
    system, user = iff._build_draft_prompt(invoice)
    assert "INV-00093" in user
    assert "54,000.00" in user
    assert "31" in user
    assert "Madhi" in user
    assert "no legal threats" in system.lower() or "legal threats" in system.lower()


def test_draft_contains_invoice_facts_end_to_end(env, suggested, ollama):
    iff = env["iff"]
    _, set_ollama = ollama
    set_ollama(lambda *a, **k: GOOD_DRAFT.format(inv="INV-FACTS", amt="9,999.00"))

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-facts", invoice_number="INV-FACTS", status="Sent",
                    due_date="2026-06-01", total=9999.0)
    conn.close()

    iff.main(now=NOW)

    assert len(suggested) == 1
    args = suggested[0]
    assert "INV-FACTS" in args["proposed_action"]
    assert "INV-FACTS" in args["title"]
    assert "9,999.00" in args["title"] or "9,999.00" in args["reasoning"]


# ── test_no_llm_no_send ─────────────────────────────────────────────────────

def test_no_llm_no_send_when_unavailable(env, suggested, ollama):
    iff = env["iff"]
    _, set_ollama = ollama
    set_ollama(lambda *a, **k: "(LLM unavailable: connection refused)")

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-noLLM", invoice_number="INV-NOLLM", status="Sent",
                    due_date="2026-06-01", total=1000.0)
    conn.close()

    iff.main(now=NOW)
    assert len(suggested) == 0


def test_no_llm_no_send_when_raises(env, suggested, ollama):
    iff = env["iff"]
    _, set_ollama = ollama

    def raise_it(*a, **k):
        raise RuntimeError("ollama down")
    set_ollama(raise_it)

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-raises", invoice_number="INV-RAISES", status="Sent",
                    due_date="2026-06-01", total=1000.0)
    conn.close()

    iff.main(now=NOW)
    assert len(suggested) == 0


def test_no_llm_no_send_when_garbage(env, suggested, ollama):
    """Garbage output (doesn't even mention the invoice it was drafted for)
    must be treated the same as an unavailable LLM -- skip, never send."""
    iff = env["iff"]
    _, set_ollama = ollama
    set_ollama(lambda *a, **k: "Sorry, I can't help with that request today.")

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-garbage", invoice_number="INV-GARBAGE", status="Sent",
                    due_date="2026-06-01", total=1000.0)
    conn.close()

    iff.main(now=NOW)
    assert len(suggested) == 0


def test_no_llm_no_send_when_empty(env, suggested, ollama):
    iff = env["iff"]
    _, set_ollama = ollama
    set_ollama(lambda *a, **k: "")

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-empty", invoice_number="INV-EMPTY", status="Sent",
                    due_date="2026-06-01", total=1000.0)
    conn.close()

    iff.main(now=NOW)
    assert len(suggested) == 0


# ── auto_execute never set (never auto-sends) ───────────────────────────────

def test_suggestion_never_carries_auto_execute(env, suggested, ollama):
    iff = env["iff"]
    _, set_ollama = ollama
    set_ollama(lambda *a, **k: GOOD_DRAFT.format(inv="INV-NOAUTO", amt="500.00"))

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-noauto", invoice_number="INV-NOAUTO", status="Sent",
                    due_date="2026-06-01", total=500.0)
    conn.close()

    iff.main(now=NOW)
    assert len(suggested) == 1
    assert suggested[0]["auto_execute"] == ""


# ── test_never_writes_db ────────────────────────────────────────────────────

def test_never_writes_db(env, suggested, ollama, monkeypatch):
    iff = env["iff"]
    _, set_ollama = ollama
    set_ollama(lambda *a, **k: GOOD_DRAFT.format(inv="INV-RO", amt="1,000.00"))

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-ro", invoice_number="INV-RO", status="Sent",
                    due_date="2026-06-01", total=1000.0)
    conn.close()

    write_statements = []
    real_get_db = iff.get_db

    def traced_get_db():
        c = real_get_db()

        def tracer(sql):
            s = sql.strip().upper()
            if s.startswith(("INSERT", "UPDATE", "DELETE", "DROP", "ALTER")):
                if "AHB_INVOICES" in s or "AHB_PROJECTS" in s or "AHB_CLIENTS" in s:
                    write_statements.append(sql)
        c.set_trace_callback(tracer)
        return c

    monkeypatch.setattr(iff, "get_db", traced_get_db)

    iff.main(now=NOW)
    # Run twice (covers the dedup path too) -- still no writes.
    iff.main(now=NOW)

    assert write_statements == []
    assert len(suggested) == 1  # sanity: the run actually did something


def test_never_writes_db_using_authorizer(env, suggested, ollama, monkeypatch):
    """Belt-and-suspenders: sqlite3's authorizer callback, which can see
    every action including ones a plain trace-callback substring scan might
    miss (e.g. statements spread across executemany or triggers)."""
    iff = env["iff"]
    _, set_ollama = ollama
    set_ollama(lambda *a, **k: GOOD_DRAFT.format(inv="INV-AUTH", amt="1,000.00"))

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-auth", invoice_number="INV-AUTH", status="Sent",
                    due_date="2026-06-01", total=1000.0)
    conn.close()

    violations = []
    real_get_db = iff.get_db
    WRITE_ACTIONS = {
        sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
    }

    def traced_get_db():
        c = real_get_db()

        def authorizer(action, arg1, arg2, db_name, trigger_name):
            if action in WRITE_ACTIONS and arg1 and arg1.lower().startswith("ahb_"):
                violations.append((action, arg1))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        c.set_authorizer(authorizer)
        return c

    monkeypatch.setattr(iff, "get_db", traced_get_db)

    iff.main(now=NOW)

    assert violations == []


# ── dry-run ──────────────────────────────────────────────────────────────

def test_dry_run_never_calls_send_suggestion(env, suggested, ollama, monkeypatch, caplog):
    iff = env["iff"]
    _, set_ollama = ollama
    set_ollama(lambda *a, **k: GOOD_DRAFT.format(inv="INV-DRY", amt="1,000.00"))

    def boom(*a, **k):
        raise AssertionError("send_suggestion must not be called in --dry-run")
    monkeypatch.setattr(iff, "send_suggestion", boom)

    conn = _biz_conn(env)
    insert_invoice(conn, "inv-dry", invoice_number="INV-DRY", status="Sent",
                    due_date="2026-06-01", total=1000.0)
    conn.close()

    iff.main(now=NOW, dry_run=True)
    # No exception raised means send_suggestion (patched to boom) was never called.


# ── misc ─────────────────────────────────────────────────────────────────

def test_main_no_overdue_invoices_is_noop(env, suggested, ollama):
    iff = env["iff"]
    calls, set_ollama = ollama

    def boom(*a, **k):
        raise AssertionError("ollama_generate should not be called with no overdue invoices")
    set_ollama(boom)

    iff.main(now=NOW)
    assert len(suggested) == 0
    assert len(calls) == 0


def test_main_is_import_safe_and_standalone(env):
    iff = env["iff"]
    import inspect
    sig = inspect.signature(iff.main)
    assert list(sig.parameters) == ["now", "dry_run"]
    assert sig.parameters["now"].default is None
    assert sig.parameters["dry_run"].default is False
