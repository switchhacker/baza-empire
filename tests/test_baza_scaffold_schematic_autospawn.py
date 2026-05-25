import json
import os
import sqlite3
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    monkeypatch.setenv("BAZA_PROJECTS_DB", path)
    if "dashboard.app" in sys.modules:
        del sys.modules["dashboard.app"]
    if "dashboard.scaffold" in sys.modules:
        del sys.modules["dashboard.scaffold"]
    from dashboard.app import app, _ensure_scaffold_tables
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
        _ensure_scaffold_tables(con)
        con.execute("INSERT OR REPLACE INTO projects(id, name) VALUES('p1', 'Test')")
        con.commit()
    finally:
        con.close()
    app.config["TESTING"] = True
    yield app.test_client()


def test_schematic_autospawn_on_first_hw_bom(client):
    # Add a BOM row that matches a known hardware component
    r = client.post("/api/baza/projects/p1/bom",
                    json={"name": "ESP32 DevKit", "qty": 1})
    assert r.status_code == 201
    # Now verify a schematic node was auto-created
    r2 = client.get("/api/baza/projects/p1/scaffold")
    nodes = r2.get_json()["nodes"]
    schem = [n for n in nodes if n["node_type"] == "schematic"]
    assert len(schem) == 1
    # Verify payload has components
    payload = schem[0].get("payload", {})
    assert "schematic" in payload
    assert len(payload["schematic"]["components"]) >= 1


def test_schematic_not_duplicated_on_more_bom(client):
    client.post("/api/baza/projects/p1/bom", json={"name": "ESP32 DevKit"})
    client.post("/api/baza/projects/p1/bom", json={"name": "HC-SR04 Ultrasonic"})
    nodes = client.get("/api/baza/projects/p1/scaffold").get_json()["nodes"]
    schem = [n for n in nodes if n["node_type"] == "schematic"]
    assert len(schem) == 1  # still only one


def test_schematic_not_spawned_for_unmatched_bom(client):
    r = client.post("/api/baza/projects/p1/bom",
                    json={"name": "Random unknown widget xyz"})
    assert r.status_code == 201
    nodes = client.get("/api/baza/projects/p1/scaffold").get_json()["nodes"]
    schem = [n for n in nodes if n["node_type"] == "schematic"]
    assert len(schem) == 0


def test_decompose_prompt_mentions_schematic():
    """Verify Claw's prompt includes the schematic instruction."""
    import sqlite3
    fd, path = __import__('tempfile').mkstemp(suffix=".db"); os.close(fd)
    try:
        os.environ["BAZA_PROJECTS_DB"] = path
        from dashboard.app import _ensure_scaffold_tables
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
        con.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, project_id TEXT, title TEXT, description TEXT, assigned_to TEXT, status TEXT DEFAULT 'pending', priority INTEGER DEFAULT 5, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        _ensure_scaffold_tables(con)
        con.execute("INSERT INTO projects(id, name) VALUES('p1', 'T')")
        con.commit(); con.close()
        from core.intent_dispatcher import dispatch
        result = dispatch({"intent": "scaffold_decompose", "project_id": "p1",
                           "root_node_id": 1, "description": "hoverboard robot",
                           "actor": "user"}, extra={})
        body, _ = result if isinstance(result, tuple) else (result, 200)
        # Read the task description back
        con = sqlite3.connect(path)
        row = con.execute("SELECT description FROM tasks WHERE id=?", (body["task_id"],)).fetchone()
        con.close()
        assert "schematic" in row[0].lower()
        assert "scaffold_propose_schematic" in row[0]
    finally:
        os.unlink(path)
