import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / "venv" / "bin" / "python")


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    sys.path.insert(0, str(ROOT))
    from dashboard.app import _ensure_scaffold_tables
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
    _ensure_scaffold_tables(con)
    con.execute("INSERT INTO projects(id, name) VALUES('p1', 'T')")
    con.commit(); con.close()
    yield path
    os.unlink(path)


def test_schematic_node_type_in_engine():
    from core.scaffold_engine import NODE_TYPES, DEFAULT_WEIGHTS, default_agent_for
    assert "schematic" in NODE_TYPES
    assert "schematic" in DEFAULT_WEIGHTS
    assert default_agent_for("schematic") == "rex_smasher"


def test_propose_schematic_skill_populates_payload(db):
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    nid = eng.create_node("p1", node_type="schematic", title="Wiring")
    # Add BOM rows that match known components
    con = sqlite3.connect(db)
    for name in ["ESP32 DevKit", "HC-SR04 Ultrasonic", "SSD1306 OLED 128x64"]:
        con.execute("INSERT INTO project_bom (project_id, name) VALUES (?, ?)", ("p1", name))
    con.commit(); con.close()

    env = {**os.environ, "BAZA_PROJECTS_DB": db,
           "SKILL_ARGS": json.dumps({"_db_path": db, "project_id": "p1",
                                     "node_id": nid, "description": "obstacle bot"})}
    res = subprocess.run(
        [PY, str(ROOT / "skills/shared/scaffold_propose_schematic.py")],
        env=env, capture_output=True, text=True, timeout=10
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip())
    assert out["component_count"] >= 3
    # Verify payload persisted
    node = eng.get_node(nid)
    payload = json.loads(node["payload_json"])
    assert "schematic" in payload
    assert len(payload["schematic"]["components"]) >= 3
    assert all("component_id" in c for c in payload["schematic"]["components"])
