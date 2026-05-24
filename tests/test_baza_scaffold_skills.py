import json
import os
import subprocess
import sqlite3
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / "venv" / "bin" / "python")


def _setup_db():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    sys.path.insert(0, str(ROOT))
    from dashboard.app import _ensure_scaffold_tables
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
    _ensure_scaffold_tables(con)
    con.execute("INSERT INTO projects(id, name) VALUES('p1', 'T')")
    con.commit(); con.close()
    return path


def _run_skill(script_rel, args):
    path = _setup_db()
    env = {**os.environ,
           "BAZA_PROJECTS_DB": path,
           "SKILL_ARGS": json.dumps({**args, "_db_path": path})}
    result = subprocess.run(
        [PY, str(ROOT / script_rel)],
        env=env, capture_output=True, text=True, timeout=10
    )
    return result, path


def test_skill_emit_nodes():
    args = {"project_id": "p1", "parent_id": None,
            "nodes": [{"node_type": "research", "title": "look it up"},
                      {"node_type": "firmware", "title": "esp32 fw", "weight": 5}]}
    res, db = _run_skill("skills/shared/scaffold_emit_nodes.py", args)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip())
    assert len(out["created_ids"]) == 2
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM project_scaffold_nodes").fetchone()[0]
    assert n == 2
    os.unlink(db)


def test_skill_complete_node():
    db = _setup_db()
    con = sqlite3.connect(db)
    cur = con.execute("""INSERT INTO project_scaffold_nodes
                         (project_id, node_type, title, status)
                         VALUES ('p1','research','x','in_progress')""")
    nid = cur.lastrowid
    con.commit(); con.close()
    env = {**os.environ, "BAZA_PROJECTS_DB": db,
           "SKILL_ARGS": json.dumps({"_db_path": db, "node_id": nid,
                                     "result": "found it"})}
    res = subprocess.run([PY, str(ROOT / "skills/shared/scaffold_complete_node.py")],
                         env=env, capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, res.stderr
    con = sqlite3.connect(db)
    status = con.execute("SELECT status FROM project_scaffold_nodes WHERE id=?",
                         (nid,)).fetchone()[0]
    assert status == "done"
    os.unlink(db)


def test_skill_add_bom():
    args = {"project_id": "p1", "node_id": None,
            "name": "ESP32", "qty": 2, "vendor": "Adafruit",
            "url": "https://x", "unit_price": 8.5}
    res, db = _run_skill("skills/shared/scaffold_add_bom.py", args)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip())
    assert out["bom_id"] > 0
    os.unlink(db)


def test_skill_block_awaiting_part():
    db = _setup_db()
    con = sqlite3.connect(db)
    cur = con.execute("""INSERT INTO project_scaffold_nodes
                         (project_id, node_type, title, status)
                         VALUES ('p1','hardware_component','x','in_progress')""")
    nid = cur.lastrowid
    cur = con.execute("""INSERT INTO project_bom
                         (project_id, node_id, name) VALUES ('p1', ?, 'x')""", (nid,))
    bid = cur.lastrowid
    con.commit(); con.close()
    env = {**os.environ, "BAZA_PROJECTS_DB": db,
           "SKILL_ARGS": json.dumps({"_db_path": db, "node_id": nid, "bom_id": bid})}
    res = subprocess.run([PY, str(ROOT / "skills/shared/scaffold_block_awaiting_part.py")],
                         env=env, capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, res.stderr
    con = sqlite3.connect(db)
    status = con.execute("SELECT status FROM project_scaffold_nodes WHERE id=?",
                         (nid,)).fetchone()[0]
    assert status == "awaiting_part"
    os.unlink(db)
