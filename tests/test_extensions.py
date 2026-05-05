"""Tests for R1-R4: develop loop, approvals, templates, git operations."""
import importlib
import json
import os
import sqlite3
import sys
import tempfile

import pytest


@pytest.fixture()
def env(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="ext_")
    proj_root = os.path.join(tmp, "projects")
    db_path = os.path.join(tmp, "t.db")
    monkeypatch.setenv("BAZA_PROJECTS_ROOT", proj_root)
    monkeypatch.setenv("BAZA_TASK_EVENTS_DB", db_path)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    for m in ("core.intent_dispatcher", "core.intent_router", "core.baza_projects",
              "core.task_events", "core.baza_project_templates"):
        if m in sys.modules:
            del sys.modules[m]
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, description TEXT,
            status TEXT, launch_date TEXT, owner TEXT, created_at TEXT);
        CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT, title TEXT,
            description TEXT, assigned_to TEXT, status TEXT, priority TEXT,
            due_date TEXT, notes TEXT, updated_at TEXT, is_subtask INTEGER,
            parent_task_id TEXT, created_at TEXT, depends_on TEXT,
            dispatch_count INTEGER, last_dispatched_at TEXT,
            dispatch_history TEXT, reassignment_count INTEGER);
    """)
    conn.close()
    bp = importlib.import_module("core.baza_projects")
    bp.ensure_schema()
    te = importlib.import_module("core.task_events")
    te.init_schema()
    return {
        "bp": bp,
        "te": te,
        "tpl": importlib.import_module("core.baza_project_templates"),
        "router": importlib.import_module("core.intent_router"),
        "dispatcher": importlib.import_module("core.intent_dispatcher"),
        "db_path": db_path,
    }


# ── R1: develop loop ────────────────────────────────────────────────────────

def test_develop_creates_pending_task(env):
    p = env["bp"].create_project(name="App", type_="web-app")
    out = env["dispatcher"].dispatch(
        env["router"].parse_intent(f"/develop {p['id']} Add a contact form")
    )
    assert out["status"] == 201
    tid = out["result"]["task_id"]
    conn = sqlite3.connect(env["db_path"])
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "pending"
    assert row["assigned_to"] == "claw_batto"
    assert row["project_id"] == p["id"]
    assert "baza_proj" in (row["description"] or "")  # skill hint baked in


def test_develop_missing_project_returns_404(env):
    out = env["dispatcher"].dispatch(
        env["router"].parse_intent("/develop no-such-project Do a thing")
    )
    assert out["status"] == 404


def test_develop_requires_goal(env):
    p = env["bp"].create_project(name="App2", type_="web-app")
    out = env["dispatcher"].dispatch(
        env["router"].parse_intent(f"/develop {p['id']}")
    )
    assert out["status"] == 400
    assert "goal" in out["result"]["error"]


# ── R2: approvals ───────────────────────────────────────────────────────────

def test_approvals_list_and_state(env):
    te = env["te"]
    # Two approval requests; one we'll grant
    te.emit("approval_requested", agent_id="a1", project_id="p1",
            payload={"action": "deploy", "details": {}})
    te.emit("approval_requested", agent_id="a1", project_id="p2",
            payload={"action": "deploy", "details": {}})
    te.emit("approval_granted", agent_id="a1", project_id="p1",
            payload={"action": "deploy", "by": "user"})

    pending = te.list_approvals(state="pending")
    assert len(pending) == 1
    assert pending[0]["project_id"] == "p2"
    assert pending[0]["state"] == "pending"

    granted = te.list_approvals(state="granted")
    assert len(granted) == 1
    assert granted[0]["project_id"] == "p1"

    all_ = te.list_approvals(state="all")
    assert len(all_) == 2


def test_approvals_denied_state(env):
    te = env["te"]
    te.emit("approval_requested", agent_id="a2", project_id="p9",
            payload={"action": "ahb.clients_delete", "details": {"id": "x"}})
    te.emit("approval_denied", agent_id="a2", project_id="p9",
            payload={"action": "ahb.clients_delete", "by": "user", "note": "wrong client"})
    denied = te.list_approvals(state="denied")
    assert len(denied) == 1
    assert denied[0]["decision"]["kind"] == "approval_denied"


# ── R3: templates ──────────────────────────────────────────────────────────

def test_templates_listed(env):
    tpl = env["tpl"]
    ids = [t["id"] for t in tpl.list_templates()]
    assert "flask-min" in ids
    assert "fastapi-min" in ids
    assert "react-vite-min" in ids
    assert "esp-idf-blink" in ids
    assert "library-min" in ids


def test_create_with_template(env):
    p = env["bp"].create_project(name="Demo Flask App", template_id="flask-min")
    assert os.path.isfile(os.path.join(p["path"], "app.py"))
    assert os.path.isfile(os.path.join(p["path"], "tests", "test_app.py"))
    # Template type should override default "other"
    assert p["type"] == "dashboard"


def test_create_with_explicit_type_overrides_template(env):
    """If user picks a type explicitly that's not 'other', honor it."""
    p = env["bp"].create_project(name="Override App", type_="library", template_id="flask-min")
    assert p["type"] == "library"  # explicit type wins


def test_template_idempotent_no_overwrite(env):
    """If we re-apply a template, existing files are kept."""
    tpl = env["tpl"]
    p = env["bp"].create_project(name="Tpl Idem", template_id="library-min")
    pkg_name = p["id"].replace("-", "_")
    target = os.path.join(p["path"], "src", pkg_name, "core.py")
    assert os.path.isfile(target), f"expected scaffold at {target}"
    with open(target, "w") as f:
        f.write("# user-modified\n")
    # Apply again — should NOT overwrite
    written = tpl.apply_template("library-min", p["path"], p["id"])
    assert os.path.relpath(target, p["path"]) not in written
    with open(target) as f:
        assert "user-modified" in f.read()


# ── R4: git status + commit ────────────────────────────────────────────────

def test_git_status_clean_after_create(env):
    p = env["bp"].create_project(name="Git App", type_="library")
    s = env["bp"].git_status(p["id"])
    assert s["files"] == []  # auto-init committed everything


def test_git_commit_after_modification(env):
    p = env["bp"].create_project(name="Git Mod", type_="library")
    # Modify README
    with open(os.path.join(p["path"], "README.md"), "a") as f:
        f.write("\nadded a line\n")
    s = env["bp"].git_status(p["id"])
    paths = [f["path"] for f in s["files"]]
    assert "README.md" in paths

    res = env["bp"].git_commit(p["id"], "test: append to README")
    assert res["committed"] is True
    assert res["head"]
    s2 = env["bp"].git_status(p["id"])
    assert s2["files"] == []


def test_git_commit_empty_message_rejected(env):
    p = env["bp"].create_project(name="GitMsg", type_="library")
    res = env["bp"].git_commit(p["id"], "")
    assert res["committed"] is False


def test_git_commit_nothing_to_commit(env):
    p = env["bp"].create_project(name="Clean", type_="library")
    res = env["bp"].git_commit(p["id"], "nothing")
    assert res["committed"] is False
    assert "nothing" in (res["error"] or "").lower() or "nothing to commit" in (res["error"] or "").lower()
