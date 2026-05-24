import sqlite3
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def db(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    monkeypatch.setenv("BAZA_PROJECTS_DB", path)
    from dashboard.app import _ensure_scaffold_tables
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
        con.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, project_id TEXT, title TEXT, description TEXT, assigned_to TEXT, status TEXT DEFAULT 'pending', priority INTEGER DEFAULT 5, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        _ensure_scaffold_tables(con)
        con.execute("INSERT INTO projects(id, name) VALUES('p1', 'Test')")
        con.commit()
    finally:
        con.close()
    yield path


def test_scaffold_decompose_creates_task_for_claw(db):
    from core.intent_dispatcher import dispatch
    envelope = {
        "intent": "scaffold_decompose",
        "project_id": "p1",
        "root_node_id": 1,
        "description": "build a thing",
        "actor": "user",
    }
    result = dispatch(envelope, extra={})
    # Accept either dict or (dict, status) return
    if isinstance(result, tuple):
        body, status = result
    else:
        body, status = result, 200
    assert body.get("task_id") is not None
    assert body.get("agent") == "claw_batto"
    # Verify task row
    import sqlite3
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT assigned_to, status, title FROM tasks WHERE id=?",
        (body["task_id"],)
    ).fetchone()
    con.close()
    assert row is not None
    assert row[0] == "claw_batto"
    assert row[1] == "pending"
    assert "scaffold decompose" in row[2]


def test_scaffold_decompose_missing_args():
    from core.intent_dispatcher import dispatch
    result = dispatch({"intent": "scaffold_decompose"}, extra={})
    if isinstance(result, tuple):
        body, status = result
    else:
        body, status = result, 200
    assert "error" in body
