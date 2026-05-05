"""Tests for core/intent_dispatcher.py — shared HTTP+Telegram dispatcher."""
import importlib
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def dispatcher(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="dispatcher_")
    proj_root = os.path.join(tmp, "projects")
    db_path = os.path.join(tmp, "test.db")
    monkeypatch.setenv("BAZA_PROJECTS_ROOT", proj_root)
    monkeypatch.setenv("BAZA_TASK_EVENTS_DB", db_path)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    for mod in ("core.intent_dispatcher", "core.intent_router",
                "core.baza_projects", "core.task_events"):
        if mod in sys.modules:
            del sys.modules[mod]
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE projects (
            id TEXT PRIMARY KEY, name TEXT, description TEXT,
            status TEXT DEFAULT 'active', launch_date TEXT, owner TEXT,
            created_at TEXT
        );
    """)
    conn.close()
    bp = importlib.import_module("core.baza_projects")
    bp.ensure_schema()
    d = importlib.import_module("core.intent_dispatcher")
    r = importlib.import_module("core.intent_router")
    return d, r, bp


def test_help(dispatcher):
    d, r, _ = dispatcher
    out = d.dispatch(r.parse_intent("/help"))
    assert out["status"] == 200
    assert "help" in out["result"]


def test_create_baza_project(dispatcher):
    d, r, _ = dispatcher
    out = d.dispatch(r.parse_intent("/create new baza project demo type=library"))
    assert out["status"] == 201
    assert out["result"]["project"]["type"] == "library"


def test_test_unknown_project(dispatcher):
    d, r, _ = dispatcher
    out = d.dispatch(r.parse_intent("/test no-such-project"))
    assert out["status"] == 404
    assert "not found" in out["result"]["error"]


def test_deploy_requires_approval(dispatcher):
    d, r, bp = dispatcher
    p = bp.create_project(name="DepProj", type_="web-app")
    out = d.dispatch(r.parse_intent(f"/deploy {p['id']}"))
    assert out["status"] == 202
    assert out["result"]["approval_required"] is True


def test_deploy_approved_passes_through(dispatcher):
    d, r, bp = dispatcher
    p = bp.create_project(name="DepProj2", type_="web-app")
    out = d.dispatch(r.parse_intent(f"/deploy {p['id']}"), extra={"approved": True})
    # Reaches run_command; that returns success/failure based on the manifest
    # echo command. The point is it didn't get stopped at the approval gate.
    assert out["status"] == 200
    assert "approval_required" not in (out["result"] or {})


def test_pending_intent_returns_202(dispatcher):
    d, r, _ = dispatcher
    out = d.dispatch(r.parse_intent("/develop x Add a thing"))
    assert out["status"] == 202
    assert out["result"]["pending"] is True


def test_telegram_format_help(dispatcher):
    d, r, _ = dispatcher
    out = d.dispatch(r.parse_intent("/help"))
    msg = d.telegram_format(out)
    assert "Directives" in msg


def test_telegram_format_create(dispatcher):
    d, r, _ = dispatcher
    out = d.dispatch(r.parse_intent("/create new baza project tg-test"))
    msg = d.telegram_format(out)
    assert "Created" in msg
    assert "tg-test" in msg


def test_telegram_format_unknown(dispatcher):
    d, r, _ = dispatcher
    out = d.dispatch(r.parse_intent("/floob"))
    msg = d.telegram_format(out)
    assert "didn't understand" in msg
