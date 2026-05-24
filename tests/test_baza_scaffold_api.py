import json
import sqlite3
import tempfile
import os
import pytest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    monkeypatch.setenv("BAZA_PROJECTS_DB", path)
    # Reload app with new DB
    import importlib
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


def test_start_scaffold_creates_root(client):
    r = client.post("/api/baza/projects/p1/scaffold/start",
                    json={"description": "build a thing"})
    assert r.status_code == 202
    body = r.get_json()
    assert "root_node_id" in body


def test_get_empty_scaffold(client):
    r = client.get("/api/baza/projects/p1/scaffold")
    assert r.status_code == 200
    body = r.get_json()
    assert body["nodes"] == []
    assert body["edges"] == []
    assert body["progress_pct"] == 0
    assert body["has_star"] is False


def test_create_manual_node(client):
    r = client.post("/api/baza/projects/p1/scaffold/node",
                    json={"node_type": "research", "title": "look it up"})
    assert r.status_code == 201
    nid = r.get_json()["id"]
    r2 = client.get("/api/baza/projects/p1/scaffold")
    assert len(r2.get_json()["nodes"]) == 1


def test_patch_node(client):
    nid = client.post("/api/baza/projects/p1/scaffold/node",
                      json={"node_type": "research", "title": "old"}).get_json()["id"]
    r = client.patch(f"/api/baza/projects/p1/scaffold/node/{nid}",
                     json={"title": "new"})
    assert r.status_code == 200


def test_delete_node(client):
    nid = client.post("/api/baza/projects/p1/scaffold/node",
                      json={"node_type": "research", "title": "x"}).get_json()["id"]
    r = client.delete(f"/api/baza/projects/p1/scaffold/node/{nid}")
    assert r.status_code == 200
    assert client.get("/api/baza/projects/p1/scaffold").get_json()["nodes"] == []


def test_pause_resume(client):
    r = client.post("/api/baza/projects/p1/scaffold/pause")
    assert r.status_code == 200
    r = client.post("/api/baza/projects/p1/scaffold/resume")
    assert r.status_code == 200


def test_override_decision(client):
    nid = client.post("/api/baza/projects/p1/scaffold/node",
                      json={"node_type": "decision", "title": "sensor",
                            "payload": {"options": ["a", "b"]}}).get_json()["id"]
    client.patch(f"/api/baza/projects/p1/scaffold/node/{nid}",
                 json={"status": "done"})
    r = client.post(f"/api/baza/projects/p1/scaffold/node/{nid}/override",
                    json={"chosen_option": "b", "reason": "user pick"})
    assert r.status_code == 200


def test_node_not_found_404(client):
    r = client.patch("/api/baza/projects/p1/scaffold/node/9999",
                     json={"title": "x"})
    assert r.status_code == 404


def test_sse_hello_event(client):
    """Subscribe to SSE and verify hello frame is delivered."""
    rv = client.get("/api/baza/projects/p1/scaffold/stream",
                    headers={"Accept": "text/event-stream"},
                    buffered=False)
    # Read just the first chunk
    chunks = []
    for chunk in rv.response:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        if len("".join(chunks)) > 0:
            break
    rv.close()
    out = "".join(chunks)
    assert "hello" in out


def test_sse_event_after_node_create(client):
    """Create a node via API; verify the event bus published the right shape."""
    from core.scaffold_engine import event_bus
    received = []
    event_bus.subscribe("p1", lambda e: received.append(e))
    client.post("/api/baza/projects/p1/scaffold/node",
                json={"node_type": "research", "title": "x"})
    types = [e["event_type"] for e in received]
    assert "created" in types
