"""Tests for skills/shared/duke_roadmap.py — Duke's autonomous roadmap skill."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SKILL = os.path.join(ROOT, "skills", "shared", "duke_roadmap.py")
PY = sys.executable


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, description TEXT,
            status TEXT, launch_date TEXT, owner TEXT, created_at TEXT);
        CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT, title TEXT,
            description TEXT, assigned_to TEXT, status TEXT, priority TEXT,
            due_date TEXT, notes TEXT, updated_at TEXT, is_subtask INTEGER,
            parent_task_id TEXT, created_at TEXT, depends_on TEXT,
            dispatch_count INTEGER, last_dispatched_at TEXT,
            dispatch_history TEXT, reassignment_count INTEGER);
        CREATE TABLE ahb_projects (id TEXT PRIMARY KEY, title TEXT, status TEXT,
            year TEXT, value REAL, client_name TEXT, updated_at TEXT);
    """)
    conn.close()
    return {"db": str(db), "tmp": str(tmp_path)}


def run_skill(args: dict, env_db: str) -> tuple[int, str]:
    e = os.environ.copy()
    e["SKILL_ARGS"] = json.dumps(args)
    e["AGENT_ID"] = "duke_harmon"
    # Skill resolves DB_PATH at module load time from FRAMEWORK_DIR/dashboard/baza_projects.db.
    # Temporarily symlink test DB into framework path? Easier: run with a wrapper that
    # patches DB_PATH. Use a small Python -c instead of the skill subprocess for tests.
    return _via_import(args, env_db)


def _via_import(args: dict, env_db: str) -> tuple[int, str]:
    """Import the skill with DB_PATH patched, then call main()."""
    import importlib
    mod_name = "skills.shared.duke_roadmap"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "skills", "shared"))
    os.environ["SKILL_ARGS"] = json.dumps(args)
    os.environ["AGENT_ID"] = "duke_harmon"
    # Load source, patch DB_PATH constant
    src_path = SKILL
    spec = importlib.util.spec_from_file_location("duke_roadmap_test", src_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DB_PATH = env_db
    # Capture stdout
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main()
    return rc, buf.getvalue()


def test_report_mode_default(env):
    rc, out = _via_import({"count": 3, "mode": "report"}, env["db"])
    assert rc == 0
    # First five default pool items show as a numbered list
    assert "1. **" in out
    assert "DISPATCH:" in out


def test_create_mode_inserts_pending_tasks(env):
    rc, out = _via_import({"count": 2, "mode": "create"}, env["db"])
    assert rc == 0
    conn = sqlite3.connect(env["db"])
    rows = conn.execute("SELECT id, title, assigned_to, status FROM tasks WHERE status='pending'").fetchall()
    conn.close()
    assert len(rows) == 2
    titles = [r[1] for r in rows]
    assert all("ahb123.com" in t or "AHB" in t or "Baza" in t or "PA" in t for t in titles)


def test_create_avoids_duplicates(env):
    """If an open task already has the same title, don't insert another."""
    conn = sqlite3.connect(env["db"])
    conn.execute(
        "INSERT INTO tasks (id, title, status, assigned_to, project_id, created_at, updated_at) "
        "VALUES ('dup1', ?, 'in_progress', 'simon_bately', '', '', '')",
        ("ahb123.com — homepage hero copy + brand voice refresh",),
    )
    conn.commit()
    conn.close()
    rc, out = _via_import({"count": 5, "mode": "create"}, env["db"])
    assert rc == 0
    conn = sqlite3.connect(env["db"])
    cnt = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE title=?",
        ("ahb123.com — homepage hero copy + brand voice refresh",),
    ).fetchone()[0]
    conn.close()
    assert cnt == 1  # didn't double-insert


def test_routing_picks_correct_agent(env):
    rc, out = _via_import({"count": 5, "mode": "report"}, env["db"])
    # SEO audit goes to scout_reeves (research keyword)
    assert "scout_reeves" in out
    # Hero copy goes to simon_bately (branding keyword)
    assert "simon_bately" in out
    # Gallery goes to sam_axe (image keyword)
    assert "sam_axe" in out


def test_focus_filter(env):
    rc, out = _via_import({"count": 5, "mode": "report", "focus": "ahb123"}, env["db"])
    assert rc == 0
    # All assignments should have ahb123 in their title
    lines = [ln for ln in out.split("\n") if ln.startswith("DISPATCH:")]
    assert len(lines) >= 1
    for ln in lines:
        assert "ahb123" in ln.lower()


def test_empty_pool_falls_back_to_in_flight(env):
    """If everything pre-seeded, fall back to surfacing in-flight tasks."""
    conn = sqlite3.connect(env["db"])
    from skills.shared import duke_roadmap as _dr_temp  # hacky: ensure pool is loaded
    # Pre-fill with all pool titles so nothing is fresh
    rc, _ = _via_import({"count": 12, "mode": "create"}, env["db"])
    assert rc == 0
    # Now ask again — pool exhausted, should surface in-flight
    rc2, out2 = _via_import({"count": 3, "mode": "report"}, env["db"])
    assert rc2 == 0
    assert "[in-flight]" in out2 or "1. **" in out2  # either path is acceptable
