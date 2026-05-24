import json
import os
import sys
import sqlite3
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    from dashboard.app import _ensure_scaffold_tables
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
        con.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, project_id TEXT, title TEXT, description TEXT, assigned_to TEXT, status TEXT DEFAULT 'pending', priority INTEGER DEFAULT 5, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        _ensure_scaffold_tables(con)
        con.execute("INSERT INTO projects(id, name) VALUES('p1', 'T')")
        con.commit()
    finally:
        con.close()
    yield path


def test_runner_picks_runnable_nodes(db):
    from core.scaffold_runner import tick_project
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    nid = eng.create_node("p1", node_type="research", title="x", status="pending")
    started = tick_project("p1", db_path=db)
    assert nid in started
    n = eng.get_node(nid)
    assert n["status"] == "in_progress"


def test_runner_assigns_agent_by_type(db):
    from core.scaffold_runner import tick_project
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    nid = eng.create_node("p1", node_type="firmware", title="fw", status="pending")
    tick_project("p1", db_path=db)
    n = eng.get_node(nid)
    assert n["agent_assigned"] == "phil_hass"


def test_runner_creates_task_row(db):
    from core.scaffold_runner import tick_project
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    eng.create_node("p1", node_type="research", title="x", status="pending")
    tick_project("p1", db_path=db)
    con = sqlite3.connect(db)
    count = con.execute("SELECT COUNT(*) FROM tasks WHERE project_id='p1'").fetchone()[0]
    assert count == 1
    con.close()


def test_runner_skips_manual_step(db):
    from core.scaffold_runner import tick_project
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    nid = eng.create_node("p1", node_type="manual_step", title="solder", status="pending")
    started = tick_project("p1", db_path=db)
    assert nid not in started
    n = eng.get_node(nid)
    assert n["status"] == "pending"


def test_runner_respects_pause(db):
    from core.scaffold_runner import tick_project
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    eng.create_node("p1", node_type="research", title="x", status="pending")
    con = sqlite3.connect(db)
    con.execute("UPDATE projects SET scaffold_paused=1 WHERE id='p1'")
    con.commit(); con.close()
    started = tick_project("p1", db_path=db)
    assert started == []


def test_runner_respects_deps(db):
    from core.scaffold_runner import tick_project
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    a = eng.create_node("p1", node_type="research", title="a", status="pending")
    b = eng.create_node("p1", node_type="research", title="b", status="pending")
    eng.add_edge("p1", from_node=a, to_node=b, edge_type="depends_on")
    started = tick_project("p1", db_path=db)
    assert a in started
    assert b not in started
