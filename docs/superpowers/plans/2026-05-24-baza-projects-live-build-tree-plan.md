# Baza Projects — Live Build Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a live, agent-driven scaffold UI on Baza dev projects where a project description auto-decomposes into a visual tree of typed nodes (research/decision/hardware/firmware/software/integration/test/deploy/result) with a BOM, Inventory/Equipment modals, SSE-driven live updates, yellow→green progress bar, and ⭐ on full deploy.

**Architecture:** New SQLite tables in `dashboard/baza_projects.db`. Engine module (`core/scaffold_engine.py`) does graph CRUD + dependency + progress math + event emission. Flask blueprint (`dashboard/scaffold.py`) exposes REST + SSE. Continuous worker (`core/scaffold_runner.py`) runs under a systemd timer and dispatches unblocked nodes to agents via existing intent_dispatcher. New scaffold sub-tab in `project_detail.html` renders a D3 tidy-tree with side panel, BOM table, progress bar, and Inventory/Equipment modals. 4 new shared skills let agents emit nodes, complete nodes, add BOM rows, and block-awaiting-part.

**Tech Stack:** Python 3 / Flask / SQLite (WAL) / D3 v7 / EventSource SSE / systemd timer. Reuses existing `intent_dispatcher`, `task_runner`, `web_search` skill, body-level modal pattern, BaseAgent persona overrides.

**Spec:** `docs/superpowers/specs/2026-05-24-baza-projects-live-build-tree-design.md`

**Reference scenario:** Rubbish Taxi (hoverboard reflash + autonomous trashcan transport).

---

## File Structure

### New files
- `core/scaffold_engine.py` — graph CRUD, deps, progress math, event bus (~500 lines)
- `core/scaffold_runner.py` — systemd-driven worker (~200 lines)
- `dashboard/scaffold.py` — Flask blueprint: scaffold + BOM + inventory + equipment routes + SSE (~800 lines)
- `skills/shared/scaffold_emit_nodes.py`
- `skills/shared/scaffold_complete_node.py`
- `skills/shared/scaffold_add_bom.py`
- `skills/shared/scaffold_block_awaiting_part.py`
- `dashboard/static/vendor/d3.v7.min.js` (vendored, ~280 KB)
- `tests/test_baza_scaffold_schema.py`
- `tests/test_baza_scaffold_engine.py`
- `tests/test_baza_scaffold_api.py`
- `tests/test_baza_scaffold_bom.py`
- `tests/test_baza_scaffold_runner.py`
- `tests/test_baza_scaffold_skills.py`
- `/etc/systemd/system/baza-scaffold-runner.service` (installed via task 14)
- `/etc/systemd/system/baza-scaffold-runner.timer`

### Modified files
- `dashboard/app.py` — register blueprint, call schema migration on startup
- `dashboard/templates/project_detail.html` — new 🌳 Scaffold sub-tab + tree + side panel + BOM + modals + JS + SSE

---

## Task 1: Schema migration + scaffold_paused column

**Files:**
- Modify: `dashboard/app.py` (top-level init section, near existing `_ensure_*_tables` calls)
- Create: `tests/test_baza_scaffold_schema.py`

- [ ] **Step 1: Write the failing schema test**

```python
# tests/test_baza_scaffold_schema.py
import sqlite3
import tempfile
import os
from pathlib import Path


def _migrate(db_path):
    """Helper that runs the migration against a fresh DB."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dashboard.app import _ensure_scaffold_tables
    con = sqlite3.connect(db_path)
    _ensure_scaffold_tables(con)
    con.commit()
    return con


def test_scaffold_tables_created():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        con = _migrate(path)
        cur = con.cursor()
        names = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "project_scaffold_nodes" in names
        assert "project_scaffold_edges" in names
        assert "project_scaffold_events" in names
        assert "project_bom" in names
        assert "baza_inventory" in names
        assert "baza_equipment" in names
        con.close()
    finally:
        os.unlink(path)


def test_scaffold_migration_idempotent():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        con1 = _migrate(path); con1.close()
        # Second run must not raise
        con2 = _migrate(path); con2.close()
    finally:
        os.unlink(path)


def test_scaffold_paused_column_added_to_projects():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT)")
        con.commit()
        con.close()
        con = _migrate(path)
        cols = {r[1] for r in con.execute("PRAGMA table_info(projects)")}
        assert "scaffold_paused" in cols
        con.close()
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run test (expect FAIL — function not defined)**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && python -m pytest tests/test_baza_scaffold_schema.py -v`
Expected: ImportError or AttributeError for `_ensure_scaffold_tables`.

- [ ] **Step 3: Implement `_ensure_scaffold_tables` in `dashboard/app.py`**

Locate the section near other `_ensure_*_tables(con)` calls (search for `_ensure_social_v22_tables` or similar). Append a new function above the call site:

```python
def _ensure_scaffold_tables(con):
    """Idempotent migration for the live build-tree scaffold subsystem."""
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS project_scaffold_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            parent_id INTEGER,
            node_type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            agent_assigned TEXT,
            payload_json TEXT,
            weight INTEGER NOT NULL DEFAULT 1,
            depth INTEGER NOT NULL DEFAULT 0,
            x REAL,
            y REAL,
            auto_decided INTEGER NOT NULL DEFAULT 0,
            chosen_option TEXT,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scaffold_nodes_pid ON project_scaffold_nodes(project_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scaffold_nodes_status ON project_scaffold_nodes(project_id, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scaffold_nodes_parent ON project_scaffold_nodes(parent_id)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS project_scaffold_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            from_node INTEGER NOT NULL,
            to_node INTEGER NOT NULL,
            edge_type TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scaffold_edges_pid ON project_scaffold_edges(project_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scaffold_edges_to ON project_scaffold_edges(to_node)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS project_scaffold_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            node_id INTEGER,
            event_type TEXT NOT NULL,
            actor TEXT,
            payload TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scaffold_events_pid ON project_scaffold_events(project_id, id)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS project_bom (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            node_id INTEGER,
            name TEXT NOT NULL,
            part_number TEXT,
            vendor TEXT,
            url TEXT,
            qty INTEGER NOT NULL DEFAULT 1,
            unit_price REAL,
            status TEXT NOT NULL DEFAULT 'researched',
            in_hand INTEGER NOT NULL DEFAULT 0,
            in_hand_at TEXT,
            notes TEXT,
            inventory_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bom_pid ON project_bom(project_id)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS baza_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            name TEXT NOT NULL,
            part_number TEXT,
            quantity INTEGER NOT NULL DEFAULT 1,
            location TEXT,
            condition TEXT DEFAULT 'good',
            unit_price REAL,
            vendor TEXT,
            url TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS baza_equipment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT,
            location TEXT,
            status TEXT NOT NULL DEFAULT 'available',
            in_use_by TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)

    # ALTER projects to add scaffold_paused
    try:
        cur.execute("ALTER TABLE projects ADD COLUMN scaffold_paused INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # already exists or table missing (test fixtures)

    con.commit()
```

Then call it from the dashboard startup section where the other `_ensure_*` calls live:

```python
# in dashboard/app.py near other migration calls
_ensure_scaffold_tables(_db())
```

- [ ] **Step 4: Run test (expect PASS)**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && python -m pytest tests/test_baza_scaffold_schema.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add dashboard/app.py tests/test_baza_scaffold_schema.py
git commit -m "scaffold T1: schema migration (5 tables + scaffold_paused col)"
```

---

## Task 2: Engine — `core/scaffold_engine.py`

**Files:**
- Create: `core/scaffold_engine.py`
- Create: `tests/test_baza_scaffold_engine.py`

- [ ] **Step 1: Write failing tests covering CRUD + deps + progress + override cascade + event bus**

```python
# tests/test_baza_scaffold_engine.py
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
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    from dashboard.app import _ensure_scaffold_tables
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
    _ensure_scaffold_tables(con)
    con.execute("INSERT INTO projects(id, name) VALUES('p1', 'Test')")
    con.commit()
    yield path
    con.close()
    os.unlink(path)


def test_create_root_node(db):
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    nid = eng.create_node("p1", node_type="root", title="Rubbish Taxi",
                          description="hoverboard trash bot", weight=1)
    nodes = eng.get_nodes("p1")
    assert len(nodes) == 1
    assert nodes[0]["title"] == "Rubbish Taxi"
    assert nodes[0]["status"] == "pending"
    assert nodes[0]["depth"] == 0


def test_create_child_node_sets_depth(db):
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    root = eng.create_node("p1", node_type="root", title="r", weight=1)
    child = eng.create_node("p1", node_type="research", title="search motor controllers",
                            parent_id=root, weight=1)
    nodes = {n["id"]: n for n in eng.get_nodes("p1")}
    assert nodes[child]["depth"] == 1


def test_dep_satisfaction_via_parent(db):
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    root = eng.create_node("p1", node_type="root", title="r", weight=1)
    child = eng.create_node("p1", node_type="firmware", title="c", parent_id=root)
    # Root is pending — child is NOT runnable (parent must be in_progress or done)
    assert eng.is_runnable(child) is False
    eng.update_node(root, status="in_progress")
    assert eng.is_runnable(child) is True


def test_dep_satisfaction_via_edge(db):
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    a = eng.create_node("p1", node_type="research", title="a")
    b = eng.create_node("p1", node_type="research", title="b")
    eng.add_edge("p1", from_node=a, to_node=b, edge_type="depends_on")
    eng.update_node(a, status="in_progress")  # parent OK (None)
    assert eng.is_runnable(b) is False  # a not done
    eng.update_node(a, status="done")
    assert eng.is_runnable(b) is True


def test_progress_weighted(db):
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    eng.create_node("p1", node_type="root", title="r", weight=0)
    a = eng.create_node("p1", node_type="research", title="a", weight=1)
    b = eng.create_node("p1", node_type="firmware", title="b", weight=5)
    eng.update_node(a, status="done")
    pct = eng.progress_pct("p1")
    # done weight=1 / total weight=6 = 16.66...
    assert 16 <= pct <= 17
    eng.update_node(b, status="done")
    assert eng.progress_pct("p1") == 100


def test_has_star_only_when_result_done(db):
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    eng.create_node("p1", node_type="root", title="r")
    res = eng.create_node("p1", node_type="result", title="ship", weight=0)
    assert eng.has_star("p1") is False
    eng.update_node(res, status="done")
    # progress must also be 100
    assert eng.has_star("p1") is True


def test_event_log_append(db):
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    n = eng.create_node("p1", node_type="research", title="t")
    eng.emit_event("p1", node_id=n, event_type="started", actor="rex", payload={"foo": 1})
    events = eng.get_events("p1")
    assert any(e["event_type"] == "started" and e["actor"] == "rex" for e in events)


def test_override_cascade_requeues_dependents(db):
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    decision = eng.create_node("p1", node_type="decision", title="sensor",
                                payload={"options": ["lidar", "ultrasonic"]})
    eng.decide(decision, chosen_option="lidar", reason="auto")
    downstream = eng.create_node("p1", node_type="firmware", title="lidar driver")
    eng.add_edge("p1", from_node=decision, to_node=downstream, edge_type="depends_on")
    eng.update_node(downstream, status="done")
    # Override the decision
    eng.override_decision(decision, chosen_option="ultrasonic", reason="user pref")
    # Downstream node should now be pending again
    nodes = {n["id"]: n for n in eng.get_nodes("p1")}
    assert nodes[downstream]["status"] == "pending"
    assert nodes[decision]["status"] == "overridden"


def test_event_bus_subscribers_receive(db):
    from core.scaffold_engine import ScaffoldEngine, event_bus
    eng = ScaffoldEngine(db)
    received = []
    def handler(evt):
        received.append(evt)
    event_bus.subscribe("p1", handler)
    n = eng.create_node("p1", node_type="research", title="t")
    eng.emit_event("p1", node_id=n, event_type="started", actor="rex")
    assert len(received) >= 1
    event_bus.unsubscribe("p1", handler)


def test_assign_default_agent_by_type(db):
    from core.scaffold_engine import ScaffoldEngine, default_agent_for
    assert default_agent_for("research") == "rex_smasher"
    assert default_agent_for("hardware_component") == "rex_smasher"
    assert default_agent_for("firmware") == "phil_hass"
    assert default_agent_for("software_module") == "phil_hass"
    assert default_agent_for("integration") == "claw_batto"
    assert default_agent_for("deploy") == "claw_batto"
    assert default_agent_for("manual_step") is None
```

- [ ] **Step 2: Run tests (expect FAIL — module not found)**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && python -m pytest tests/test_baza_scaffold_engine.py -v`

- [ ] **Step 3: Implement `core/scaffold_engine.py`**

```python
# core/scaffold_engine.py
"""Scaffold graph engine — CRUD, dependency checks, progress math, event bus."""
import json
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path

NODE_TYPES = {
    "root", "research", "decision", "hardware_component",
    "firmware", "software_module", "integration",
    "test", "deploy", "result", "manual_step",
}

DEFAULT_WEIGHTS = {
    "root": 0,
    "research": 1,
    "decision": 1,
    "hardware_component": 3,
    "firmware": 5,
    "software_module": 4,
    "integration": 4,
    "test": 2,
    "deploy": 2,
    "manual_step": 2,
    "result": 0,
}

_AGENT_BY_TYPE = {
    "research": "rex_smasher",
    "hardware_component": "rex_smasher",
    "decision": "claw_batto",
    "firmware": "phil_hass",
    "software_module": "phil_hass",
    "integration": "claw_batto",
    "test": "phil_hass",
    "deploy": "claw_batto",
    "manual_step": None,
    "root": None,
    "result": "claw_batto",
}


def default_agent_for(node_type):
    return _AGENT_BY_TYPE.get(node_type)


class _EventBus:
    """In-process pub/sub keyed by project_id. Used by SSE writers."""
    def __init__(self):
        self._lock = threading.Lock()
        self._subs = defaultdict(list)  # project_id -> [callable]

    def subscribe(self, project_id, callback):
        with self._lock:
            self._subs[project_id].append(callback)

    def unsubscribe(self, project_id, callback):
        with self._lock:
            try:
                self._subs[project_id].remove(callback)
            except ValueError:
                pass

    def publish(self, project_id, event):
        with self._lock:
            subs = list(self._subs.get(project_id, []))
        for cb in subs:
            try:
                cb(event)
            except Exception:
                pass


event_bus = _EventBus()


class ScaffoldEngine:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def _con(self):
        con = sqlite3.connect(self.db_path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout = 5000")
        return con

    # ---------------- Node CRUD ----------------

    def create_node(self, project_id, node_type, title, description="",
                    parent_id=None, weight=None, agent=None, payload=None,
                    status="pending"):
        if node_type not in NODE_TYPES:
            raise ValueError(f"unknown node_type: {node_type}")
        if weight is None:
            weight = DEFAULT_WEIGHTS.get(node_type, 1)
        depth = 0
        if parent_id:
            with self._con() as con:
                row = con.execute(
                    "SELECT depth FROM project_scaffold_nodes WHERE id=?",
                    (parent_id,)
                ).fetchone()
                if row:
                    depth = row["depth"] + 1
        payload_str = json.dumps(payload, default=str) if payload else None
        with self._con() as con:
            cur = con.execute("""
                INSERT INTO project_scaffold_nodes
                  (project_id, parent_id, node_type, title, description,
                   status, agent_assigned, payload_json, weight, depth)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (project_id, parent_id, node_type, title, description,
                  status, agent, payload_str, weight, depth))
            nid = cur.lastrowid
            con.commit()
        self.emit_event(project_id, node_id=nid, event_type="created",
                        actor=agent or "system",
                        payload={"node_type": node_type, "title": title,
                                 "parent_id": parent_id})
        return nid

    def get_node(self, node_id):
        with self._con() as con:
            row = con.execute(
                "SELECT * FROM project_scaffold_nodes WHERE id=?",
                (node_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_nodes(self, project_id):
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM project_scaffold_nodes WHERE project_id=? ORDER BY depth, id",
                (project_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def update_node(self, node_id, **fields):
        allowed = {"title", "description", "status", "weight", "payload_json",
                   "agent_assigned", "chosen_option", "auto_decided",
                   "started_at", "completed_at", "x", "y"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return
        sets.append("updated_at=CURRENT_TIMESTAMP")
        vals.append(node_id)
        with self._con() as con:
            con.execute(
                f"UPDATE project_scaffold_nodes SET {', '.join(sets)} WHERE id=?",
                vals
            )
            con.commit()
        node = self.get_node(node_id)
        if node and "status" in fields:
            self.emit_event(node["project_id"], node_id=node_id,
                            event_type="status_changed",
                            actor="system",
                            payload={"new_status": fields["status"]})

    def delete_node(self, node_id):
        """Cascades to descendants."""
        node = self.get_node(node_id)
        if not node:
            return
        pid = node["project_id"]
        # collect descendants BFS
        to_delete = [node_id]
        queue = [node_id]
        while queue:
            parent = queue.pop()
            with self._con() as con:
                rows = con.execute(
                    "SELECT id FROM project_scaffold_nodes WHERE parent_id=?",
                    (parent,)
                ).fetchall()
            for r in rows:
                to_delete.append(r["id"])
                queue.append(r["id"])
        with self._con() as con:
            con.executemany(
                "DELETE FROM project_scaffold_nodes WHERE id=?",
                [(i,) for i in to_delete]
            )
            con.executemany(
                "DELETE FROM project_scaffold_edges WHERE from_node=? OR to_node=?",
                [(i, i) for i in to_delete]
            )
            con.commit()
        self.emit_event(pid, node_id=node_id, event_type="deleted",
                        actor="system", payload={"cascade_count": len(to_delete)})

    # ---------------- Edges ----------------

    def add_edge(self, project_id, from_node, to_node, edge_type):
        with self._con() as con:
            con.execute("""
                INSERT INTO project_scaffold_edges
                  (project_id, from_node, to_node, edge_type)
                VALUES (?, ?, ?, ?)
            """, (project_id, from_node, to_node, edge_type))
            con.commit()

    def get_edges(self, project_id):
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM project_scaffold_edges WHERE project_id=?",
                (project_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------------- Dependency check ----------------

    def is_runnable(self, node_id):
        node = self.get_node(node_id)
        if not node or node["status"] != "pending":
            return False
        # Parent must be in_progress or done
        if node["parent_id"]:
            parent = self.get_node(node["parent_id"])
            if parent and parent["status"] not in ("in_progress", "done"):
                return False
        # All depends_on edges must point at done nodes
        with self._con() as con:
            unfinished = con.execute("""
                SELECT 1 FROM project_scaffold_edges e
                JOIN project_scaffold_nodes n ON n.id = e.from_node
                WHERE e.to_node=? AND e.edge_type='depends_on' AND n.status != 'done'
                LIMIT 1
            """, (node_id,)).fetchone()
            if unfinished:
                return False
        return True

    def get_runnable_nodes(self, project_id, limit=20):
        candidates = [n for n in self.get_nodes(project_id) if n["status"] == "pending"]
        out = []
        for n in candidates:
            if self.is_runnable(n["id"]):
                out.append(n)
                if len(out) >= limit:
                    break
        return out

    # ---------------- Progress ----------------

    def progress_pct(self, project_id):
        nodes = self.get_nodes(project_id)
        total = sum(n["weight"] for n in nodes)
        if total == 0:
            return 0
        done = sum(n["weight"] for n in nodes if n["status"] == "done")
        return int(round(100 * done / total))

    def has_star(self, project_id):
        nodes = self.get_nodes(project_id)
        result_nodes = [n for n in nodes if n["node_type"] == "result"]
        if not result_nodes:
            return False
        if not all(n["status"] == "done" for n in result_nodes):
            return False
        return self.progress_pct(project_id) == 100

    # ---------------- Decisions ----------------

    def decide(self, node_id, chosen_option, reason=""):
        self.update_node(node_id,
                         status="done",
                         chosen_option=chosen_option,
                         auto_decided=1,
                         completed_at=datetime.utcnow().isoformat())
        node = self.get_node(node_id)
        self.emit_event(node["project_id"], node_id=node_id, event_type="decided",
                        actor=node["agent_assigned"] or "system",
                        payload={"chosen": chosen_option, "reason": reason})

    def override_decision(self, node_id, chosen_option, reason=""):
        node = self.get_node(node_id)
        if not node:
            return
        # Find dependents and reset them to pending if they were further along
        with self._con() as con:
            deps = con.execute("""
                SELECT to_node FROM project_scaffold_edges
                WHERE from_node=? AND edge_type='depends_on'
            """, (node_id,)).fetchall()
        for d in deps:
            ddata = self.get_node(d["to_node"])
            if ddata and ddata["status"] in ("in_progress", "done", "failed"):
                self.update_node(d["to_node"], status="pending",
                                 started_at=None, completed_at=None)
        self.update_node(node_id,
                         status="overridden",
                         chosen_option=chosen_option,
                         auto_decided=0)
        self.emit_event(node["project_id"], node_id=node_id, event_type="overridden",
                        actor="user",
                        payload={"chosen": chosen_option, "reason": reason})

    # ---------------- Events ----------------

    def emit_event(self, project_id, node_id=None, event_type="note",
                   actor="system", payload=None):
        payload_str = json.dumps(payload, default=str) if payload else None
        with self._con() as con:
            cur = con.execute("""
                INSERT INTO project_scaffold_events
                  (project_id, node_id, event_type, actor, payload)
                VALUES (?, ?, ?, ?, ?)
            """, (project_id, node_id, event_type, actor, payload_str))
            event_id = cur.lastrowid
            con.commit()
        event = {
            "id": event_id,
            "project_id": project_id,
            "node_id": node_id,
            "event_type": event_type,
            "actor": actor,
            "payload": payload or {},
            "created_at": datetime.utcnow().isoformat(),
        }
        event_bus.publish(project_id, event)
        return event_id

    def get_events(self, project_id, since_id=0, limit=200):
        with self._con() as con:
            rows = con.execute("""
                SELECT * FROM project_scaffold_events
                WHERE project_id=? AND id>?
                ORDER BY id ASC LIMIT ?
            """, (project_id, since_id, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("payload"):
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    pass
            out.append(d)
        return out
```

- [ ] **Step 4: Run tests (expect PASS)**

Run: `python -m pytest tests/test_baza_scaffold_engine.py -v`
Expected: 10 PASSED.

- [ ] **Step 5: Commit**

```bash
git add core/scaffold_engine.py tests/test_baza_scaffold_engine.py
git commit -m "scaffold T2: engine (graph CRUD, deps, progress, override, event bus)"
```

---

## Task 3: API blueprint — scaffold graph routes

**Files:**
- Create: `dashboard/scaffold.py`
- Modify: `dashboard/app.py` (register blueprint near other `app.register_blueprint(...)` calls)
- Create: `tests/test_baza_scaffold_api.py`

- [ ] **Step 1: Write failing API tests for graph routes**

```python
# tests/test_baza_scaffold_api.py
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
def client(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    monkeypatch.setenv("BAZA_PROJECTS_DB", path)
    # Reload app with new DB
    import importlib
    if "dashboard.app" in sys.modules:
        del sys.modules["dashboard.app"]
    from dashboard.app import app, _ensure_scaffold_tables, _db
    con = _db()
    con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
    _ensure_scaffold_tables(con)
    con.execute("INSERT OR REPLACE INTO projects(id, name) VALUES('p1', 'Test')")
    con.commit()
    con.close()
    app.config["TESTING"] = True
    yield app.test_client()
    os.unlink(path)


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
    # First mark it done with a choice (simulating agent decision)
    client.patch(f"/api/baza/projects/p1/scaffold/node/{nid}",
                 json={"status": "done"})
    r = client.post(f"/api/baza/projects/p1/scaffold/node/{nid}/override",
                    json={"chosen_option": "b", "reason": "user pick"})
    assert r.status_code == 200


def test_node_not_found_404(client):
    r = client.patch("/api/baza/projects/p1/scaffold/node/9999",
                     json={"title": "x"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests (expect FAIL — blueprint not registered)**

Run: `python -m pytest tests/test_baza_scaffold_api.py -v`

- [ ] **Step 3: Implement `dashboard/scaffold.py` graph routes (BOM/inventory in later tasks)**

```python
# dashboard/scaffold.py
"""Flask blueprint for the live build-tree scaffold subsystem."""
import json
import os
import time
import queue
from flask import Blueprint, jsonify, request, Response, current_app
from core.scaffold_engine import ScaffoldEngine, event_bus, NODE_TYPES

scaffold_bp = Blueprint("scaffold", __name__)


def _engine():
    db = os.environ.get("BAZA_PROJECTS_DB",
                        os.path.join(os.path.dirname(__file__), "baza_projects.db"))
    return ScaffoldEngine(db)


def _project_exists(pid):
    eng = _engine()
    import sqlite3
    con = sqlite3.connect(eng.db_path)
    try:
        row = con.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone()
        return row is not None
    finally:
        con.close()


# ---------------- Scaffold graph ----------------

@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/start", methods=["POST"])
def scaffold_start(pid):
    if not _project_exists(pid):
        return jsonify({"error": "project not found"}), 404
    body = request.get_json(silent=True) or {}
    description = (body.get("description") or "").strip()
    regenerate = bool(body.get("regenerate"))
    eng = _engine()
    if regenerate:
        for n in eng.get_nodes(pid):
            eng.delete_node(n["id"])
    root_id = eng.create_node(pid, node_type="root",
                              title=description[:80] or "New build",
                              description=description,
                              status="in_progress",
                              payload={"description": description})
    # Dispatch to intent_dispatcher for orchestration (best-effort)
    task_id = None
    try:
        from core.intent_dispatcher import dispatch
        env = {"intent": "scaffold_decompose",
               "project_id": pid,
               "root_node_id": root_id,
               "description": description,
               "actor": "user"}
        res = dispatch(env, extra={})
        if isinstance(res, dict):
            task_id = res.get("task_id")
    except Exception as e:
        # Dispatch failure shouldn't crash the start call
        eng.emit_event(pid, node_id=root_id, event_type="note",
                       actor="system",
                       payload={"warning": f"dispatch unavailable: {e}"})
    return jsonify({"root_node_id": root_id, "task_id": task_id}), 202


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold", methods=["GET"])
def scaffold_get(pid):
    eng = _engine()
    nodes = eng.get_nodes(pid)
    # parse payload_json into dict for client
    for n in nodes:
        if n.get("payload_json"):
            try:
                n["payload"] = json.loads(n["payload_json"])
            except Exception:
                n["payload"] = {}
        else:
            n["payload"] = {}
    return jsonify({
        "nodes": nodes,
        "edges": eng.get_edges(pid),
        "progress_pct": eng.progress_pct(pid),
        "has_star": eng.has_star(pid),
    })


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node", methods=["POST"])
def scaffold_node_create(pid):
    body = request.get_json(silent=True) or {}
    nt = body.get("node_type")
    title = (body.get("title") or "").strip()
    if not nt or nt not in NODE_TYPES:
        return jsonify({"error": "invalid node_type"}), 400
    if not title:
        return jsonify({"error": "title required"}), 400
    eng = _engine()
    nid = eng.create_node(
        pid,
        node_type=nt,
        title=title,
        description=body.get("description", ""),
        parent_id=body.get("parent_id"),
        weight=body.get("weight"),
        agent=body.get("agent"),
        payload=body.get("payload"),
    )
    return jsonify({"id": nid}), 201


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node/<int:nid>", methods=["PATCH"])
def scaffold_node_patch(pid, nid):
    eng = _engine()
    n = eng.get_node(nid)
    if not n or n["project_id"] != pid:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    fields = {}
    for k in ("title", "description", "status", "weight", "agent_assigned"):
        if k in body:
            fields[k] = body[k]
    if "payload" in body:
        fields["payload_json"] = json.dumps(body["payload"], default=str)
    eng.update_node(nid, **fields)
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node/<int:nid>", methods=["DELETE"])
def scaffold_node_delete(pid, nid):
    eng = _engine()
    n = eng.get_node(nid)
    if not n or n["project_id"] != pid:
        return jsonify({"error": "not found"}), 404
    eng.delete_node(nid)
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node/<int:nid>/run", methods=["POST"])
def scaffold_node_run(pid, nid):
    eng = _engine()
    n = eng.get_node(nid)
    if not n or n["project_id"] != pid:
        return jsonify({"error": "not found"}), 404
    eng.update_node(nid, status="pending", started_at=None, completed_at=None)
    eng.emit_event(pid, node_id=nid, event_type="rerun_requested", actor="user")
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node/<int:nid>/override", methods=["POST"])
def scaffold_node_override(pid, nid):
    eng = _engine()
    n = eng.get_node(nid)
    if not n or n["project_id"] != pid:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    chosen = (body.get("chosen_option") or "").strip()
    if not chosen:
        return jsonify({"error": "chosen_option required"}), 400
    eng.override_decision(nid, chosen_option=chosen, reason=body.get("reason", ""))
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node/<int:nid>/note", methods=["POST"])
def scaffold_node_note(pid, nid):
    eng = _engine()
    n = eng.get_node(nid)
    if not n or n["project_id"] != pid:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    note = (body.get("note") or "").strip()
    if not note:
        return jsonify({"error": "note required"}), 400
    eng.emit_event(pid, node_id=nid, event_type="note", actor="user",
                   payload={"note": note})
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/pause", methods=["POST"])
def scaffold_pause(pid):
    import sqlite3
    eng = _engine()
    con = sqlite3.connect(eng.db_path)
    try:
        con.execute("UPDATE projects SET scaffold_paused=1 WHERE id=?", (pid,))
        con.commit()
    finally:
        con.close()
    eng.emit_event(pid, event_type="project_paused", actor="user")
    return jsonify({"ok": True, "paused": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/resume", methods=["POST"])
def scaffold_resume(pid):
    import sqlite3
    eng = _engine()
    con = sqlite3.connect(eng.db_path)
    try:
        con.execute("UPDATE projects SET scaffold_paused=0 WHERE id=?", (pid,))
        con.commit()
    finally:
        con.close()
    eng.emit_event(pid, event_type="project_resumed", actor="user")
    return jsonify({"ok": True, "paused": False})


# ---------------- SSE (Task 4 fills this in) ----------------

@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/stream", methods=["GET"])
def scaffold_stream(pid):
    def gen():
        q = queue.Queue()
        def handler(evt):
            try:
                q.put_nowait(evt)
            except Exception:
                pass
        event_bus.subscribe(pid, handler)
        try:
            # Initial hello so the client knows it connected
            yield f"event: hello\ndata: {{\"project_id\": \"{pid}\"}}\n\n"
            while True:
                try:
                    evt = q.get(timeout=15)
                    yield f"event: {evt['event_type']}\ndata: {json.dumps(evt, default=str)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            event_bus.unsubscribe(pid, handler)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})
```

Register in `dashboard/app.py` near other blueprint registrations:

```python
from dashboard.scaffold import scaffold_bp
app.register_blueprint(scaffold_bp)
```

Also make `_db()` and `_ensure_scaffold_tables` honor `BAZA_PROJECTS_DB` if not already done — verify by reading the existing `_db()` function. If it always points to the hardcoded path, add:

```python
DB_PATH = os.environ.get("BAZA_PROJECTS_DB", os.path.join(os.path.dirname(__file__), "baza_projects.db"))
```

and replace the hardcoded path in `_db()` with `DB_PATH`. (If this would touch unrelated code, leave `_db()` alone and just point the scaffold engine at the env var directly — both work.)

- [ ] **Step 4: Run tests (expect PASS)**

Run: `python -m pytest tests/test_baza_scaffold_api.py -v`
Expected: 8 PASSED.

- [ ] **Step 5: Commit**

```bash
git add dashboard/scaffold.py dashboard/app.py tests/test_baza_scaffold_api.py
git commit -m "scaffold T3: graph API blueprint (start/get/CRUD/run/override/note/pause)"
```

---

## Task 4: SSE delivery verification

**Files:**
- Modify: `tests/test_baza_scaffold_api.py` (add SSE tests)

- [ ] **Step 1: Write SSE delivery test**

Append to `tests/test_baza_scaffold_api.py`:

```python
def test_sse_hello_event(client):
    """Subscribe to SSE and verify hello frame is delivered."""
    # Use the Werkzeug test client streaming behaviour
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
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_baza_scaffold_api.py::test_sse_hello_event tests/test_baza_scaffold_api.py::test_sse_event_after_node_create -v`
Expected: 2 PASSED.

If `test_sse_hello_event` hangs, the generator isn't yielding fast enough on connect — verify the generator yields the hello line BEFORE entering the queue loop (it does in T3 impl).

- [ ] **Step 3: Commit**

```bash
git add tests/test_baza_scaffold_api.py
git commit -m "scaffold T4: SSE delivery tests (hello + event publish)"
```

---

## Task 5: BOM CRUD + checkbox auto-unblock

**Files:**
- Modify: `dashboard/scaffold.py` (add BOM routes)
- Create: `tests/test_baza_scaffold_bom.py`

- [ ] **Step 1: Write BOM tests**

```python
# tests/test_baza_scaffold_bom.py
import os
import sys
import tempfile
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    monkeypatch.setenv("BAZA_PROJECTS_DB", path)
    import importlib
    if "dashboard.app" in sys.modules:
        del sys.modules["dashboard.app"]
    from dashboard.app import app, _ensure_scaffold_tables, _db
    con = _db()
    con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
    _ensure_scaffold_tables(con)
    con.execute("INSERT OR REPLACE INTO projects(id, name) VALUES('p1', 'Test')")
    con.commit()
    con.close()
    app.config["TESTING"] = True
    yield app.test_client()
    os.unlink(path)


def test_bom_crud(client):
    r = client.post("/api/baza/projects/p1/bom",
                    json={"name": "ESP32", "qty": 2, "unit_price": 8.5,
                          "vendor": "Adafruit", "url": "https://adafruit.com/x"})
    assert r.status_code == 201
    bid = r.get_json()["id"]
    r2 = client.get("/api/baza/projects/p1/bom")
    assert len(r2.get_json()["items"]) == 1

    r3 = client.patch(f"/api/baza/projects/p1/bom/{bid}",
                      json={"status": "ordered"})
    assert r3.status_code == 200

    r4 = client.delete(f"/api/baza/projects/p1/bom/{bid}")
    assert r4.status_code == 200


def test_bom_toggle_hand_unblocks_node(client):
    # Create a hardware node + awaiting_part status
    nid = client.post("/api/baza/projects/p1/scaffold/node",
                      json={"node_type": "hardware_component",
                            "title": "ESP32"}).get_json()["id"]
    client.patch(f"/api/baza/projects/p1/scaffold/node/{nid}",
                 json={"status": "awaiting_part"})
    bid = client.post("/api/baza/projects/p1/bom",
                      json={"name": "ESP32", "node_id": nid}).get_json()["id"]
    # Toggle in_hand
    r = client.post(f"/api/baza/projects/p1/bom/{bid}/toggle-hand")
    assert r.status_code == 200
    # Node should now be pending again
    r2 = client.get("/api/baza/projects/p1/scaffold")
    n = [n for n in r2.get_json()["nodes"] if n["id"] == nid][0]
    assert n["status"] == "pending"


def test_bom_promote_inventory(client):
    bid = client.post("/api/baza/projects/p1/bom",
                      json={"name": "10kΩ resistor",
                            "qty": 100, "unit_price": 0.02}).get_json()["id"]
    r = client.post(f"/api/baza/projects/p1/bom/{bid}/promote-inventory")
    assert r.status_code == 200
    assert r.get_json()["inventory_id"] > 0
    inv = client.get("/api/baza/inventory").get_json()["items"]
    assert any(i["name"] == "10kΩ resistor" for i in inv)


def test_bom_not_found(client):
    r = client.patch("/api/baza/projects/p1/bom/9999",
                     json={"status": "ordered"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run (expect FAIL — routes don't exist)**

Run: `python -m pytest tests/test_baza_scaffold_bom.py -v`

- [ ] **Step 3: Implement BOM routes — append to `dashboard/scaffold.py`**

```python
# ---------------- BOM ----------------
import sqlite3

BOM_WRITABLE = {"name", "part_number", "vendor", "url", "qty", "unit_price",
                "status", "notes", "node_id"}


@scaffold_bp.route("/api/baza/projects/<pid>/bom", methods=["GET"])
def bom_list(pid):
    eng = _engine()
    con = sqlite3.connect(eng.db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM project_bom WHERE project_id=? ORDER BY in_hand ASC, id DESC",
            (pid,)
        ).fetchall()
    finally:
        con.close()
    return jsonify({"items": [dict(r) for r in rows]})


@scaffold_bp.route("/api/baza/projects/<pid>/bom", methods=["POST"])
def bom_create(pid):
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    eng = _engine()
    con = sqlite3.connect(eng.db_path)
    try:
        cur = con.execute("""
            INSERT INTO project_bom
              (project_id, node_id, name, part_number, vendor, url, qty,
               unit_price, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pid, body.get("node_id"), name, body.get("part_number"),
              body.get("vendor"), body.get("url"), int(body.get("qty") or 1),
              body.get("unit_price"), body.get("status", "researched"),
              body.get("notes")))
        bid = cur.lastrowid
        con.commit()
    finally:
        con.close()
    eng.emit_event(pid, node_id=body.get("node_id"), event_type="bom_added",
                   actor="user", payload={"bom_id": bid, "name": name})
    return jsonify({"id": bid}), 201


@scaffold_bp.route("/api/baza/projects/<pid>/bom/<int:bid>", methods=["PATCH"])
def bom_patch(pid, bid):
    body = request.get_json(silent=True) or {}
    sets, vals = [], []
    for k, v in body.items():
        if k in BOM_WRITABLE:
            sets.append(f"{k}=?"); vals.append(v)
    if not sets:
        return jsonify({"error": "nothing to update"}), 400
    sets.append("updated_at=CURRENT_TIMESTAMP"); vals.extend([bid, pid])
    eng = _engine()
    con = sqlite3.connect(eng.db_path)
    try:
        cur = con.execute(
            f"UPDATE project_bom SET {', '.join(sets)} WHERE id=? AND project_id=?",
            vals
        )
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/bom/<int:bid>", methods=["DELETE"])
def bom_delete(pid, bid):
    eng = _engine()
    con = sqlite3.connect(eng.db_path)
    try:
        cur = con.execute("DELETE FROM project_bom WHERE id=? AND project_id=?",
                          (bid, pid))
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/bom/<int:bid>/toggle-hand", methods=["POST"])
def bom_toggle_hand(pid, bid):
    eng = _engine()
    con = sqlite3.connect(eng.db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM project_bom WHERE id=? AND project_id=?",
                          (bid, pid)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        new_val = 0 if row["in_hand"] else 1
        ts = "CURRENT_TIMESTAMP" if new_val else "NULL"
        con.execute(
            f"UPDATE project_bom SET in_hand=?, in_hand_at={ts}, "
            f"status=CASE WHEN ?=1 THEN 'received' ELSE status END "
            f"WHERE id=?",
            (new_val, new_val, bid)
        )
        con.commit()
        # If this BOM is linked to an awaiting_part node, unblock it
        if new_val and row["node_id"]:
            node = eng.get_node(row["node_id"])
            if node and node["status"] == "awaiting_part":
                eng.update_node(row["node_id"], status="pending")
    finally:
        con.close()
    eng.emit_event(pid, node_id=row["node_id"], event_type="bom_in_hand",
                   actor="user",
                   payload={"bom_id": bid, "in_hand": bool(new_val)})
    return jsonify({"ok": True, "in_hand": bool(new_val)})


@scaffold_bp.route("/api/baza/projects/<pid>/bom/<int:bid>/promote-inventory", methods=["POST"])
def bom_promote_inventory(pid, bid):
    eng = _engine()
    con = sqlite3.connect(eng.db_path)
    con.row_factory = sqlite3.Row
    try:
        b = con.execute("SELECT * FROM project_bom WHERE id=? AND project_id=?",
                        (bid, pid)).fetchone()
        if not b:
            return jsonify({"error": "not found"}), 404
        cur = con.execute("""
            INSERT INTO baza_inventory
              (category, name, part_number, quantity, unit_price, vendor, url, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("auto-promoted", b["name"], b["part_number"], b["qty"],
              b["unit_price"], b["vendor"], b["url"], b["notes"]))
        inv_id = cur.lastrowid
        con.execute("UPDATE project_bom SET inventory_id=? WHERE id=?", (inv_id, bid))
        con.commit()
    finally:
        con.close()
    eng.emit_event(pid, node_id=b["node_id"], event_type="promoted_to_inventory",
                   actor="user", payload={"bom_id": bid, "inventory_id": inv_id})
    return jsonify({"ok": True, "inventory_id": inv_id})
```

- [ ] **Step 4: Run tests (expect PASS)**

Run: `python -m pytest tests/test_baza_scaffold_bom.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add dashboard/scaffold.py tests/test_baza_scaffold_bom.py
git commit -m "scaffold T5: BOM CRUD + checkbox auto-unblock + promote-to-inventory"
```

---

## Task 6: Global Inventory + Equipment routes

**Files:**
- Modify: `dashboard/scaffold.py`
- Modify: `tests/test_baza_scaffold_bom.py` (append)

- [ ] **Step 1: Write inventory/equipment tests**

Append to `tests/test_baza_scaffold_bom.py`:

```python
def test_inventory_crud(client):
    r = client.post("/api/baza/inventory",
                    json={"name": "Arduino Uno", "category": "MCU",
                          "quantity": 3, "location": "garage bin 1"})
    assert r.status_code == 201
    iid = r.get_json()["id"]
    items = client.get("/api/baza/inventory").get_json()["items"]
    assert any(i["id"] == iid for i in items)

    r2 = client.patch(f"/api/baza/inventory/{iid}", json={"quantity": 4})
    assert r2.status_code == 200

    r3 = client.delete(f"/api/baza/inventory/{iid}")
    assert r3.status_code == 200


def test_equipment_crud(client):
    r = client.post("/api/baza/equipment",
                    json={"name": "Hakko FX-888D", "type": "soldering"})
    assert r.status_code == 201
    eid = r.get_json()["id"]
    items = client.get("/api/baza/equipment").get_json()["items"]
    assert any(i["id"] == eid for i in items)

    client.patch(f"/api/baza/equipment/{eid}", json={"status": "in_use"})
    items = client.get("/api/baza/equipment").get_json()["items"]
    assert next(i for i in items if i["id"] == eid)["status"] == "in_use"

    client.delete(f"/api/baza/equipment/{eid}")


def test_inventory_not_found(client):
    r = client.patch("/api/baza/inventory/9999", json={"quantity": 1})
    assert r.status_code == 404
```

- [ ] **Step 2: Run (expect FAIL)**

Run: `python -m pytest tests/test_baza_scaffold_bom.py::test_inventory_crud tests/test_baza_scaffold_bom.py::test_equipment_crud -v`

- [ ] **Step 3: Implement inventory + equipment routes — append to `dashboard/scaffold.py`**

```python
# ---------------- Global Inventory + Equipment ----------------

INV_WRITABLE = {"category", "name", "part_number", "quantity", "location",
                "condition", "unit_price", "vendor", "url", "notes"}
EQUIP_WRITABLE = {"name", "type", "location", "status", "in_use_by", "notes"}


def _crud_helpers(table, writable_set):
    """Returns (list_fn, create_fn, patch_fn, delete_fn) for a global table."""
    def _list():
        eng = _engine()
        con = sqlite3.connect(eng.db_path); con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                f"SELECT * FROM {table} ORDER BY id DESC"
            ).fetchall()
        finally:
            con.close()
        return jsonify({"items": [dict(r) for r in rows]})

    def _create():
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        cols = [k for k in body if k in writable_set]
        cols_sql = ", ".join(cols)
        ph = ", ".join("?" for _ in cols)
        vals = [body[k] for k in cols]
        eng = _engine()
        con = sqlite3.connect(eng.db_path)
        try:
            cur = con.execute(
                f"INSERT INTO {table} ({cols_sql}) VALUES ({ph})", vals
            )
            new_id = cur.lastrowid
            con.commit()
        finally:
            con.close()
        return jsonify({"id": new_id}), 201

    def _patch(item_id):
        body = request.get_json(silent=True) or {}
        sets, vals = [], []
        for k, v in body.items():
            if k in writable_set:
                sets.append(f"{k}=?"); vals.append(v)
        if not sets:
            return jsonify({"error": "nothing to update"}), 400
        sets.append("updated_at=CURRENT_TIMESTAMP"); vals.append(item_id)
        eng = _engine()
        con = sqlite3.connect(eng.db_path)
        try:
            cur = con.execute(
                f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", vals
            )
            if cur.rowcount == 0:
                return jsonify({"error": "not found"}), 404
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    def _delete(item_id):
        eng = _engine()
        con = sqlite3.connect(eng.db_path)
        try:
            cur = con.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
            if cur.rowcount == 0:
                return jsonify({"error": "not found"}), 404
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    return _list, _create, _patch, _delete


_inv_list, _inv_create, _inv_patch, _inv_delete = _crud_helpers("baza_inventory", INV_WRITABLE)
_eq_list, _eq_create, _eq_patch, _eq_delete = _crud_helpers("baza_equipment", EQUIP_WRITABLE)

scaffold_bp.add_url_rule("/api/baza/inventory", "inv_list", _inv_list, methods=["GET"])
scaffold_bp.add_url_rule("/api/baza/inventory", "inv_create", _inv_create, methods=["POST"])
scaffold_bp.add_url_rule("/api/baza/inventory/<int:item_id>", "inv_patch", _inv_patch, methods=["PATCH"])
scaffold_bp.add_url_rule("/api/baza/inventory/<int:item_id>", "inv_delete", _inv_delete, methods=["DELETE"])

scaffold_bp.add_url_rule("/api/baza/equipment", "eq_list", _eq_list, methods=["GET"])
scaffold_bp.add_url_rule("/api/baza/equipment", "eq_create", _eq_create, methods=["POST"])
scaffold_bp.add_url_rule("/api/baza/equipment/<int:item_id>", "eq_patch", _eq_patch, methods=["PATCH"])
scaffold_bp.add_url_rule("/api/baza/equipment/<int:item_id>", "eq_delete", _eq_delete, methods=["DELETE"])


# ---------------- Supplies needed (Phase 3 stub) ----------------

@scaffold_bp.route("/api/baza/supplies/needed", methods=["GET"])
def supplies_needed():
    eng = _engine()
    con = sqlite3.connect(eng.db_path); con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT name, part_number, vendor, url, SUM(qty) as total_qty,
                   MIN(unit_price) as best_price, COUNT(*) as project_count
            FROM project_bom
            WHERE in_hand = 0 AND status NOT IN ('cancelled', 'received')
            GROUP BY name, part_number
            ORDER BY total_qty DESC
        """).fetchall()
    finally:
        con.close()
    return jsonify({"items": [dict(r) for r in rows]})
```

- [ ] **Step 4: Run tests (expect PASS)**

Run: `python -m pytest tests/test_baza_scaffold_bom.py -v`
Expected: 7 PASSED total.

- [ ] **Step 5: Commit**

```bash
git add dashboard/scaffold.py tests/test_baza_scaffold_bom.py
git commit -m "scaffold T6: global Inventory + Equipment CRUD + supplies-needed stub"
```

---

## Task 7: Four new shared skills

**Files:**
- Create: `skills/shared/scaffold_emit_nodes.py`
- Create: `skills/shared/scaffold_complete_node.py`
- Create: `skills/shared/scaffold_add_bom.py`
- Create: `skills/shared/scaffold_block_awaiting_part.py`
- Create: `tests/test_baza_scaffold_skills.py`

- [ ] **Step 1: Write skill tests (sub-process invocation)**

```python
# tests/test_baza_scaffold_skills.py
import json
import os
import subprocess
import sqlite3
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
        [sys.executable, str(ROOT / script_rel)],
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
    # Verify in DB
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
    res = subprocess.run([sys.executable, str(ROOT / "skills/shared/scaffold_complete_node.py")],
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
    res = subprocess.run([sys.executable, str(ROOT / "skills/shared/scaffold_block_awaiting_part.py")],
                         env=env, capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, res.stderr
    con = sqlite3.connect(db)
    status = con.execute("SELECT status FROM project_scaffold_nodes WHERE id=?",
                         (nid,)).fetchone()[0]
    assert status == "awaiting_part"
    os.unlink(db)
```

- [ ] **Step 2: Run (expect FAIL — skills don't exist)**

Run: `python -m pytest tests/test_baza_scaffold_skills.py -v`

- [ ] **Step 3: Implement the 4 skills**

```python
# skills/shared/scaffold_emit_nodes.py
"""Skill: agents add child nodes under a parent during scaffold decomposition."""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main():
    args = json.loads(os.environ.get("SKILL_ARGS") or "{}")
    db_path = args.get("_db_path") or os.environ.get("BAZA_PROJECTS_DB")
    if not db_path:
        print(json.dumps({"error": "no db path"})); sys.exit(1)
    project_id = args["project_id"]
    parent_id = args.get("parent_id")
    nodes = args.get("nodes") or []

    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db_path)
    created = []
    for n in nodes:
        nid = eng.create_node(
            project_id,
            node_type=n["node_type"],
            title=n.get("title", ""),
            description=n.get("description", ""),
            parent_id=n.get("parent_id", parent_id),
            weight=n.get("weight"),
            agent=n.get("agent"),
            payload=n.get("payload"),
        )
        created.append(nid)
        # depends_on edges
        for dep in (n.get("depends_on") or []):
            eng.add_edge(project_id, from_node=dep, to_node=nid, edge_type="depends_on")
    print(json.dumps({"created_ids": created}))


if __name__ == "__main__":
    main()
```

```python
# skills/shared/scaffold_complete_node.py
"""Skill: agent reports a node complete (with optional result + artifacts)."""
import json
import os
import sys
from pathlib import Path
from datetime import datetime

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main():
    args = json.loads(os.environ.get("SKILL_ARGS") or "{}")
    db_path = args.get("_db_path") or os.environ.get("BAZA_PROJECTS_DB")
    if not db_path:
        print(json.dumps({"error": "no db path"})); sys.exit(1)
    node_id = args["node_id"]
    result = args.get("result", "done")
    artifacts = args.get("artifacts") or []
    decision = args.get("decision")
    reason = args.get("reason", "")

    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db_path)
    node = eng.get_node(node_id)
    if not node:
        print(json.dumps({"error": "node not found"})); sys.exit(1)

    # Merge into payload
    payload = {}
    if node.get("payload_json"):
        try:
            payload = json.loads(node["payload_json"])
        except Exception:
            payload = {}
    payload["result"] = result
    if artifacts:
        payload["artifacts"] = artifacts
    if reason:
        payload["reason"] = reason

    if result == "blocked":
        new_status = "blocked"
    elif decision is not None:
        eng.decide(node_id, chosen_option=decision, reason=reason)
        print(json.dumps({"ok": True, "decided": decision}))
        return
    else:
        new_status = "done"

    eng.update_node(node_id,
                    status=new_status,
                    payload_json=json.dumps(payload, default=str),
                    completed_at=datetime.utcnow().isoformat())
    eng.emit_event(node["project_id"], node_id=node_id, event_type="completed",
                   actor=node.get("agent_assigned") or "system",
                   payload={"result": result})
    print(json.dumps({"ok": True, "status": new_status}))


if __name__ == "__main__":
    main()
```

```python
# skills/shared/scaffold_add_bom.py
"""Skill: agent adds a part to the project BOM."""
import json
import os
import sys
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main():
    args = json.loads(os.environ.get("SKILL_ARGS") or "{}")
    db_path = args.get("_db_path") or os.environ.get("BAZA_PROJECTS_DB")
    if not db_path:
        print(json.dumps({"error": "no db path"})); sys.exit(1)
    project_id = args["project_id"]
    name = (args.get("name") or "").strip()
    if not name:
        print(json.dumps({"error": "name required"})); sys.exit(1)

    con = sqlite3.connect(db_path)
    try:
        cur = con.execute("""
            INSERT INTO project_bom
              (project_id, node_id, name, part_number, vendor, url, qty,
               unit_price, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (project_id, args.get("node_id"), name,
              args.get("part_number"), args.get("vendor"), args.get("url"),
              int(args.get("qty") or 1), args.get("unit_price"),
              args.get("status", "researched"), args.get("notes")))
        bid = cur.lastrowid
        con.commit()
    finally:
        con.close()

    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db_path)
    eng.emit_event(project_id, node_id=args.get("node_id"),
                   event_type="bom_added",
                   actor=args.get("actor", "system"),
                   payload={"bom_id": bid, "name": name})
    print(json.dumps({"bom_id": bid}))


if __name__ == "__main__":
    main()
```

```python
# skills/shared/scaffold_block_awaiting_part.py
"""Skill: mark a node blocked because a needed part hasn't arrived."""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main():
    args = json.loads(os.environ.get("SKILL_ARGS") or "{}")
    db_path = args.get("_db_path") or os.environ.get("BAZA_PROJECTS_DB")
    if not db_path:
        print(json.dumps({"error": "no db path"})); sys.exit(1)
    node_id = args["node_id"]
    bom_id = args.get("bom_id")

    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db_path)
    node = eng.get_node(node_id)
    if not node:
        print(json.dumps({"error": "node not found"})); sys.exit(1)

    eng.update_node(node_id, status="awaiting_part")
    eng.emit_event(node["project_id"], node_id=node_id,
                   event_type="awaiting_part", actor="system",
                   payload={"bom_id": bom_id})
    print(json.dumps({"ok": True}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests (expect PASS)**

Run: `python -m pytest tests/test_baza_scaffold_skills.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add skills/shared/scaffold_emit_nodes.py skills/shared/scaffold_complete_node.py skills/shared/scaffold_add_bom.py skills/shared/scaffold_block_awaiting_part.py tests/test_baza_scaffold_skills.py
git commit -m "scaffold T7: 4 shared skills (emit_nodes, complete_node, add_bom, block_awaiting_part)"
```

---

## Task 8: Scaffold runner (worker)

**Files:**
- Create: `core/scaffold_runner.py`
- Create: `tests/test_baza_scaffold_runner.py`

- [ ] **Step 1: Write runner tests**

```python
# tests/test_baza_scaffold_runner.py
import json
import os
import sys
import sqlite3
import tempfile
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    from dashboard.app import _ensure_scaffold_tables
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
    con.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, project_id TEXT, title TEXT, description TEXT, assigned_to TEXT, status TEXT DEFAULT 'pending', priority INTEGER DEFAULT 5, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    _ensure_scaffold_tables(con)
    con.execute("INSERT INTO projects(id, name) VALUES('p1', 'T')")
    con.commit(); con.close()
    yield path
    os.unlink(path)


def test_runner_picks_runnable_nodes(db):
    from core.scaffold_runner import tick_project
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    nid = eng.create_node("p1", node_type="research", title="x",
                          status="pending")
    started = tick_project("p1", db_path=db)
    assert nid in started
    n = eng.get_node(nid)
    assert n["status"] == "in_progress"


def test_runner_assigns_agent_by_type(db):
    from core.scaffold_runner import tick_project
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    nid = eng.create_node("p1", node_type="firmware", title="fw",
                          status="pending")
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
    nid = eng.create_node("p1", node_type="manual_step", title="solder",
                          status="pending")
    started = tick_project("p1", db_path=db)
    assert nid not in started
    n = eng.get_node(nid)
    assert n["status"] == "pending"  # untouched


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
```

- [ ] **Step 2: Run (expect FAIL — module not found)**

Run: `python -m pytest tests/test_baza_scaffold_runner.py -v`

- [ ] **Step 3: Implement `core/scaffold_runner.py`**

```python
# core/scaffold_runner.py
"""Continuous worker — finds runnable scaffold nodes and dispatches them.

Run as a systemd timer (every 30s). Pseudocode:
  for each active+unpaused project:
    for each runnable node (limit 20):
      assign agent, mark in_progress, insert task row
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _db_path():
    return os.environ.get("BAZA_PROJECTS_DB",
                          str(REPO / "dashboard" / "baza_projects.db"))


def get_active_unpaused_projects(db_path):
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("""
            SELECT DISTINCT p.id FROM projects p
            JOIN project_scaffold_nodes n ON n.project_id = p.id
            WHERE COALESCE(p.scaffold_paused, 0) = 0
              AND n.status IN ('pending', 'in_progress')
        """).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def _task_description_for(node, project_id):
    payload = node.get("payload_json") or "{}"
    parent_id = node.get("parent_id")
    return f"""You are working on Baza scaffold node {node['id']} in project {project_id}.

Node type: {node['node_type']}
Title: {node['title']}
Description: {node.get('description', '')}
Payload: {payload}
Parent: {parent_id}

When finished, you MUST end your response with:
##SKILL:scaffold_complete_node{{"node_id": {node['id']}, "result": "..."}}##

For research nodes: call ##SKILL:web_search{{...}}##, summarize 3-5 sources, pick one.
For decision nodes: list alternatives, pick the best, call scaffold_complete_node with `decision` set.
For hardware_component nodes: call ##SKILL:scaffold_add_bom{{...}}## with the chosen part, then complete.
For firmware / software_module nodes: write code into artifacts/scaffold/{node['id']}/ via the file tool, list paths in artifacts.

If blocked, end with ##SKILL:scaffold_complete_node{{"node_id": {node['id']}, "result": "blocked", "reason": "..."}}##
"""


def tick_project(project_id, db_path=None):
    """Process one project tick. Returns list of node IDs started."""
    db_path = db_path or _db_path()
    from core.scaffold_engine import ScaffoldEngine, default_agent_for

    # Honor pause flag
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT scaffold_paused FROM projects WHERE id=?",
                          (project_id,)).fetchone()
        if row and row[0]:
            return []
    finally:
        con.close()

    eng = ScaffoldEngine(db_path)
    started = []
    runnable = eng.get_runnable_nodes(project_id, limit=20)
    for n in runnable:
        if n["node_type"] == "manual_step":
            continue  # waits for user
        agent = n.get("agent_assigned") or default_agent_for(n["node_type"])
        if not agent:
            continue
        # Assign + mark started
        eng.update_node(n["id"],
                        status="in_progress",
                        agent_assigned=agent,
                        started_at=datetime.utcnow().isoformat())
        eng.emit_event(project_id, node_id=n["id"], event_type="started",
                       actor=agent)
        # Insert task row
        try:
            con = sqlite3.connect(db_path)
            con.execute("""
                INSERT INTO tasks
                  (project_id, title, description, assigned_to, status, priority)
                VALUES (?, ?, ?, ?, 'pending', 5)
            """, (project_id,
                  f"[scaffold #{n['id']}] {n['title']}",
                  _task_description_for(n, project_id),
                  agent))
            con.commit()
        finally:
            con.close()
        started.append(n["id"])
    return started


def tick_all(db_path=None):
    db_path = db_path or _db_path()
    total = []
    for pid in get_active_unpaused_projects(db_path):
        try:
            total.extend(tick_project(pid, db_path=db_path))
        except Exception as e:
            print(f"[scaffold-runner] tick failed for {pid}: {e}", file=sys.stderr)
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="single tick then exit (systemd default)")
    parser.add_argument("--project", help="only run this project")
    args = parser.parse_args()
    if args.project:
        result = tick_project(args.project)
    else:
        result = tick_all()
    print(json.dumps({"started": result}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests (expect PASS)**

Run: `python -m pytest tests/test_baza_scaffold_runner.py -v`
Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add core/scaffold_runner.py tests/test_baza_scaffold_runner.py
git commit -m "scaffold T8: continuous runner (dispatches unblocked nodes to agents)"
```

---

## Task 9: D3 vendor + scaffold tab shell

**Files:**
- Download: `dashboard/static/vendor/d3.v7.min.js`
- Modify: `dashboard/templates/project_detail.html` (add 🌳 Scaffold sub-tab)

- [ ] **Step 1: Vendor D3 v7**

```bash
mkdir -p /home/switchhacker/baza-empire/agent-framework-v3/dashboard/static/vendor
curl -L -o /home/switchhacker/baza-empire/agent-framework-v3/dashboard/static/vendor/d3.v7.min.js https://d3js.org/d3.v7.min.js
ls -l /home/switchhacker/baza-empire/agent-framework-v3/dashboard/static/vendor/d3.v7.min.js
```
Expected: file ~280 KB.

- [ ] **Step 2: Locate sub-tab nav in `project_detail.html`**

Open `dashboard/templates/project_detail.html` and find the sub-tab nav (search for `Overview` or the tab list — around line 100 per the exploration). The nav looks like:

```html
<div class="subtabs">
  <button data-tab="overview">Overview</button>
  <button data-tab="brainstorm">Brainstorm</button>
  ...
</div>
```

Add **🌳 Scaffold** as the first tab and add a content div:

```html
<button data-tab="scaffold">🌳 Scaffold</button>
<!-- ...other tabs unchanged... -->

<div id="tab-scaffold" class="tab-content" style="display:none;">
  <!-- Header strip -->
  <div class="scaffold-header" style="display:flex;align-items:center;gap:12px;padding:10px;border-bottom:1px solid #2a2a2a;">
    <button id="scaffold-pause-toggle" class="btn-sm" title="Pause/resume autonomous runner">⏸ Pause</button>
    <button id="scaffold-start-btn" class="btn-sm" style="background:#22c55e;color:#fff;">▶ Start scaffold</button>
    <div style="flex:1;">
      <div class="scaffold-progress-bar" style="position:relative;height:18px;background:#222;border-radius:9px;overflow:hidden;">
        <div id="scaffold-progress-fill" style="position:absolute;left:0;top:0;bottom:0;width:0%;background:linear-gradient(90deg,#FFD700,#22c55e);transition:width 0.4s;"></div>
        <span id="scaffold-progress-label" style="position:absolute;right:10px;top:0;line-height:18px;font-size:12px;font-weight:700;color:#fff;text-shadow:0 0 3px #000;">0%</span>
      </div>
    </div>
    <button id="scaffold-open-inventory" class="btn-sm">🧰 Inventory</button>
    <button id="scaffold-open-equipment" class="btn-sm">🔧 Equipment</button>
    <button id="scaffold-open-supplies" class="btn-sm">🛒 Supplies</button>
  </div>

  <!-- Tree canvas -->
  <div id="scaffold-tree-wrap" style="position:relative;height:520px;background:#0e0e0e;overflow:hidden;border-bottom:1px solid #2a2a2a;">
    <svg id="scaffold-tree-svg" style="width:100%;height:100%;"></svg>
    <div id="scaffold-empty-state" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#777;text-align:center;">
      <div>
        <div style="font-size:48px;">🌱</div>
        <div style="margin-top:8px;">No scaffold yet. Click <strong>▶ Start scaffold</strong> to grow your build tree.</div>
      </div>
    </div>
  </div>

  <!-- Side panel (slides in from right) -->
  <div id="scaffold-side-panel" style="position:fixed;top:0;right:-440px;width:420px;height:100vh;background:#161616;border-left:1px solid #333;z-index:9000;transition:right 0.25s;overflow-y:auto;box-shadow:-4px 0 16px rgba(0,0,0,0.5);">
    <div id="scaffold-side-panel-body" style="padding:14px;">
      <button onclick="ScaffoldUI.closeSidePanel()" style="float:right;font-size:18px;background:none;border:none;color:#999;cursor:pointer;">✕</button>
      <div id="scaffold-node-detail"></div>
    </div>
  </div>

  <!-- BOM table -->
  <div style="padding:12px;">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
      <h3 style="margin:0;">📋 Bill of Materials</h3>
      <button id="scaffold-bom-add" class="btn-sm">+ Add part</button>
    </div>
    <table id="scaffold-bom-table" style="width:100%;border-collapse:collapse;">
      <thead style="background:#1a1a1a;">
        <tr>
          <th style="padding:6px;text-align:left;">✓</th>
          <th style="padding:6px;text-align:left;">Name</th>
          <th style="padding:6px;text-align:left;">Qty</th>
          <th style="padding:6px;text-align:left;">Unit $</th>
          <th style="padding:6px;text-align:left;">Vendor</th>
          <th style="padding:6px;text-align:left;">Status</th>
          <th style="padding:6px;text-align:left;">Node</th>
          <th style="padding:6px;text-align:left;"></th>
        </tr>
      </thead>
      <tbody id="scaffold-bom-tbody"></tbody>
    </table>
    <div id="scaffold-bom-empty" style="padding:20px;color:#777;text-align:center;">No parts yet.</div>
  </div>
</div>
```

Load D3 in the page head (search for existing `<script src="`):

```html
<script src="/static/vendor/d3.v7.min.js"></script>
```

- [ ] **Step 3: Restart dashboard, verify tab renders**

```bash
sudo systemctl restart baza-dashboard.service
sleep 2
curl -s http://localhost:8888/projects/SOME_PROJECT_ID | grep -c 'tab-scaffold'
```
Expected: 1 (or more) — the new tab div is in the page.

- [ ] **Step 4: Commit**

```bash
git add dashboard/static/vendor/d3.v7.min.js dashboard/templates/project_detail.html
git commit -m "scaffold T9: vendor D3 v7 + 🌳 Scaffold sub-tab shell (header, tree wrap, BOM table)"
```

---

## Task 10: D3 tree renderer + side panel + SSE client

**Files:**
- Modify: `dashboard/templates/project_detail.html` (append JS module before closing body)

- [ ] **Step 1: Add JS module — `ScaffoldUI` IIFE**

Add inside a `<script>` block near the bottom of the page (after existing project_detail JS):

```html
<script>
(function(){
  const PROJECT_ID = window.PROJECT_ID || (window.location.pathname.split('/').pop());
  let _graph = { nodes: [], edges: [], progress_pct: 0, has_star: false };
  let _eventSource = null;
  let _reconnectDelay = 1000;

  const ICONS = {
    root: '🌱', research: '🔬', decision: '❓',
    hardware_component: '🔩', firmware: '⚡', software_module: '💻',
    integration: '🔗', test: '✅', deploy: '🚀',
    result: '🍎', manual_step: '✋'
  };
  const STATUS_COLOR = {
    pending: '#9ca3af', in_progress: '#3b82f6',
    done: '#22c55e', blocked: '#ef4444',
    awaiting_part: '#f59e0b', failed: '#dc2626',
    overridden: '#a855f7'
  };

  async function _api(path, opts={}) {
    opts.headers = Object.assign({'Content-Type': 'application/json'}, opts.headers || {});
    const r = await fetch(`/api/baza/projects/${PROJECT_ID}${path}`, opts);
    if (!r.ok && opts.method !== 'GET') console.error('api', path, r.status);
    return r.json().catch(() => ({}));
  }

  async function loadGraph() {
    _graph = await _api('/scaffold');
    drawTree();
    drawBOM();
    drawProgress();
  }

  function drawProgress() {
    const fill = document.getElementById('scaffold-progress-fill');
    const lbl = document.getElementById('scaffold-progress-label');
    if (!fill) return;
    fill.style.width = (_graph.progress_pct || 0) + '%';
    lbl.textContent = _graph.has_star ? '⭐' : (_graph.progress_pct + '%');
  }

  function drawTree() {
    const svg = d3.select('#scaffold-tree-svg');
    svg.selectAll('*').remove();
    const empty = document.getElementById('scaffold-empty-state');
    if (!_graph.nodes.length) { empty.style.display = 'flex'; return; }
    empty.style.display = 'none';

    const W = svg.node().clientWidth || 800;
    const H = svg.node().clientHeight || 520;

    // Build hierarchy from parent_id
    const stratify = d3.stratify()
      .id(d => d.id)
      .parentId(d => d.parent_id);
    let root;
    try {
      root = stratify(_graph.nodes);
    } catch (e) {
      // multi-root: synthesize
      const wrapped = [{id: '_root', parent_id: null, title: 'root', node_type: 'root', status: 'done'}]
        .concat(_graph.nodes.map(n => n.parent_id == null ? {...n, parent_id: '_root'} : n));
      root = stratify(wrapped);
    }
    const tree = d3.tree().size([W - 80, H - 80]);
    tree(root);

    const g = svg.append('g').attr('transform', 'translate(40, 40)');

    // Edges
    g.selectAll('.link').data(root.links()).enter()
      .append('path').attr('class', 'link')
      .attr('fill', 'none').attr('stroke', '#444').attr('stroke-width', 1.5)
      .attr('d', d3.linkVertical().x(d => d.x).y(d => d.y));

    // Nodes
    const node = g.selectAll('.node').data(root.descendants()).enter()
      .append('g').attr('class', 'node')
      .attr('transform', d => `translate(${d.x}, ${d.y})`)
      .style('cursor', 'pointer')
      .on('click', (evt, d) => { if (d.data.id !== '_root') openSidePanel(d.data.id); });

    node.append('rect')
      .attr('x', -75).attr('y', -22).attr('width', 150).attr('height', 44)
      .attr('rx', 6).attr('ry', 6)
      .attr('fill', d => STATUS_COLOR[d.data.status] || '#666')
      .attr('opacity', 0.85)
      .attr('stroke', '#000').attr('stroke-width', 1);

    node.append('text').attr('text-anchor', 'middle').attr('y', -4)
      .attr('fill', '#fff').attr('font-size', '14px')
      .text(d => (ICONS[d.data.node_type] || '•') + ' ' + (d.data.title || '').slice(0, 16));

    node.append('text').attr('text-anchor', 'middle').attr('y', 14)
      .attr('fill', '#ddd').attr('font-size', '10px')
      .text(d => d.data.status);

    // Zoom
    svg.call(d3.zoom().scaleExtent([0.2, 3])
      .on('zoom', evt => g.attr('transform', evt.transform)));
  }

  async function openSidePanel(nodeId) {
    const n = _graph.nodes.find(x => x.id === nodeId);
    if (!n) return;
    const panel = document.getElementById('scaffold-side-panel');
    const body = document.getElementById('scaffold-node-detail');
    body.innerHTML = `
      <h3 style="margin-top:0;">${ICONS[n.node_type]||'•'} ${escapeHtml(n.title)}</h3>
      <div style="display:flex;gap:8px;margin-bottom:10px;">
        <span class="pill" style="background:${STATUS_COLOR[n.status]||'#666'};padding:2px 8px;border-radius:10px;font-size:11px;color:#fff;">${n.status}</span>
        <span class="pill" style="background:#333;padding:2px 8px;border-radius:10px;font-size:11px;">${n.node_type}</span>
        ${n.agent_assigned ? `<span class="pill" style="background:#1a1a1a;padding:2px 8px;border-radius:10px;font-size:11px;">👤 ${n.agent_assigned}</span>` : ''}
      </div>
      <p style="color:#bbb;font-size:13px;">${escapeHtml(n.description || '')}</p>
      ${n.payload && n.payload.options ? `<div style="margin-top:10px;">
         <strong>Options:</strong><ul>${n.payload.options.map(o => `<li>${escapeHtml(o)}${o===n.chosen_option?' ✓':''}</li>`).join('')}</ul>
         <button onclick="ScaffoldUI.overrideDecision(${n.id})" class="btn-sm">Override</button>
      </div>` : ''}
      <div style="margin-top:14px;border-top:1px solid #333;padding-top:10px;">
        <button onclick="ScaffoldUI.runNode(${n.id})" class="btn-sm">↻ Re-run</button>
        <button onclick="ScaffoldUI.addNote(${n.id})" class="btn-sm">📝 Note</button>
        ${n.node_type === 'manual_step' ? `<button onclick="ScaffoldUI.markDone(${n.id})" class="btn-sm" style="background:#22c55e;color:#fff;">✅ Mark done</button>` : ''}
        <button onclick="ScaffoldUI.deleteNode(${n.id})" class="btn-sm" style="background:#7a1d1d;color:#fff;">Delete</button>
      </div>
      <details style="margin-top:14px;">
        <summary style="cursor:pointer;color:#888;font-size:12px;">Raw payload</summary>
        <pre style="background:#0a0a0a;padding:8px;font-size:11px;overflow-x:auto;">${escapeHtml(JSON.stringify(n.payload || {}, null, 2))}</pre>
      </details>`;
    panel.style.right = '0';
  }

  function closeSidePanel() {
    document.getElementById('scaffold-side-panel').style.right = '-440px';
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ---- BOM ----
  async function drawBOM() {
    const r = await _api('/bom');
    const tbody = document.getElementById('scaffold-bom-tbody');
    const empty = document.getElementById('scaffold-bom-empty');
    tbody.innerHTML = '';
    if (!r.items || !r.items.length) { empty.style.display = 'block'; return; }
    empty.style.display = 'none';
    r.items.forEach(b => {
      const tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid #222';
      tr.innerHTML = `
        <td style="padding:6px;"><input type="checkbox" ${b.in_hand?'checked':''} onchange="ScaffoldUI.toggleHand(${b.id})"></td>
        <td style="padding:6px;">${escapeHtml(b.name)}${b.part_number?` <span style="color:#888;font-size:11px;">(${escapeHtml(b.part_number)})</span>`:''}</td>
        <td style="padding:6px;">${b.qty}</td>
        <td style="padding:6px;">${b.unit_price?'$'+b.unit_price:''}</td>
        <td style="padding:6px;">${b.vendor?(b.url?`<a href="${escapeHtml(b.url)}" target="_blank">${escapeHtml(b.vendor)}</a>`:escapeHtml(b.vendor)):''}</td>
        <td style="padding:6px;"><span class="pill" style="background:#333;padding:2px 8px;border-radius:10px;font-size:11px;">${b.status}</span></td>
        <td style="padding:6px;">${b.node_id?`<a href="javascript:void(0)" onclick="ScaffoldUI.openSidePanel(${b.node_id})">#${b.node_id}</a>`:''}</td>
        <td style="padding:6px;"><button class="btn-sm" onclick="ScaffoldUI.promoteBom(${b.id})">→ Inv</button> <button class="btn-sm" style="background:#7a1d1d;color:#fff;" onclick="ScaffoldUI.deleteBom(${b.id})">✕</button></td>
      `;
      tbody.appendChild(tr);
    });
  }

  // ---- Actions ----
  async function startScaffold() {
    const desc = prompt("Project description (what should the agent build?):");
    if (!desc) return;
    await _api('/scaffold/start', { method: 'POST', body: JSON.stringify({description: desc}) });
    loadGraph();
  }
  async function runNode(id) {
    await _api(`/scaffold/node/${id}/run`, { method: 'POST', body: '{}' });
    loadGraph();
  }
  async function addNote(id) {
    const note = prompt("Note:");
    if (!note) return;
    await _api(`/scaffold/node/${id}/note`, { method: 'POST', body: JSON.stringify({note}) });
  }
  async function markDone(id) {
    await _api(`/scaffold/node/${id}`, { method: 'PATCH', body: JSON.stringify({status: 'done'}) });
    loadGraph();
  }
  async function deleteNode(id) {
    if (!confirm("Delete this node and its descendants?")) return;
    await _api(`/scaffold/node/${id}`, { method: 'DELETE' });
    closeSidePanel();
    loadGraph();
  }
  async function overrideDecision(id) {
    const choice = prompt("Override with which option?");
    if (!choice) return;
    await _api(`/scaffold/node/${id}/override`, { method: 'POST', body: JSON.stringify({chosen_option: choice}) });
    loadGraph();
  }
  async function toggleHand(bid) {
    await _api(`/bom/${bid}/toggle-hand`, { method: 'POST', body: '{}' });
    loadGraph();
  }
  async function promoteBom(bid) {
    await _api(`/bom/${bid}/promote-inventory`, { method: 'POST', body: '{}' });
    drawBOM();
  }
  async function deleteBom(bid) {
    if (!confirm("Delete this BOM row?")) return;
    await _api(`/bom/${bid}`, { method: 'DELETE' });
    drawBOM();
  }
  async function addBom() {
    const name = prompt("Part name:"); if (!name) return;
    const qty = parseInt(prompt("Qty:", "1")) || 1;
    await _api('/bom', { method: 'POST', body: JSON.stringify({name, qty}) });
    drawBOM();
  }
  async function togglePause() {
    const r = await fetch(`/api/baza/projects/${PROJECT_ID}/scaffold`);
    // We don't have a flag in graph payload — just call pause and let the UI optimistically flip
    const btn = document.getElementById('scaffold-pause-toggle');
    const currentlyPaused = btn.textContent.includes('▶');
    const url = currentlyPaused ? '/scaffold/resume' : '/scaffold/pause';
    await _api(url, { method: 'POST', body: '{}' });
    btn.textContent = currentlyPaused ? '⏸ Pause' : '▶ Resume';
  }

  // ---- SSE ----
  function connectStream() {
    if (_eventSource) _eventSource.close();
    _eventSource = new EventSource(`/api/baza/projects/${PROJECT_ID}/scaffold/stream`);
    _eventSource.addEventListener('hello', () => { _reconnectDelay = 1000; });
    ['created','started','status_changed','completed','decided','overridden',
     'bom_added','bom_in_hand','promoted_to_inventory','note','deleted',
     'project_paused','project_resumed','awaiting_part','rerun_requested',
     'failed'].forEach(t => {
      _eventSource.addEventListener(t, () => loadGraph());
    });
    _eventSource.onerror = () => {
      _eventSource.close();
      _eventSource = null;
      setTimeout(connectStream, _reconnectDelay);
      _reconnectDelay = Math.min(_reconnectDelay * 2, 30000);
    };
  }

  function init() {
    document.getElementById('scaffold-start-btn').addEventListener('click', startScaffold);
    document.getElementById('scaffold-pause-toggle').addEventListener('click', togglePause);
    document.getElementById('scaffold-bom-add').addEventListener('click', addBom);
    document.getElementById('scaffold-open-inventory').addEventListener('click', () => InventoryModal.open());
    document.getElementById('scaffold-open-equipment').addEventListener('click', () => EquipmentModal.open());
    document.getElementById('scaffold-open-supplies').addEventListener('click', () => alert('Phase 3'));
    loadGraph();
    connectStream();
  }

  window.ScaffoldUI = {
    init, loadGraph, openSidePanel, closeSidePanel,
    runNode, addNote, markDone, deleteNode, overrideDecision,
    toggleHand, promoteBom, deleteBom, addBom
  };

  // Trigger on tab show — wire to existing tab-switching code
  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.querySelector('[data-tab="scaffold"]');
    if (btn) btn.addEventListener('click', () => {
      // give the existing tab framework a tick to display:block the panel
      setTimeout(init, 50);
    });
  });
})();
</script>
```

- [ ] **Step 2: Restart dashboard + manual smoke**

```bash
sudo systemctl restart baza-dashboard.service
```

In a browser, open a Baza project detail page, click the 🌳 Scaffold tab. Click "▶ Start scaffold", type "test description", verify:
- A node appears (the root)
- The progress bar updates
- Side panel opens on click
- No JS console errors

- [ ] **Step 3: Commit**

```bash
git add dashboard/templates/project_detail.html
git commit -m "scaffold T10: D3 tree renderer + side panel + BOM UI + SSE client"
```

---

## Task 11: Inventory + Equipment body-level modals

**Files:**
- Modify: `dashboard/templates/project_detail.html` (add modals + JS modules)

- [ ] **Step 1: Add modals at body level (per ahb123 modal feedback)**

At the very end of `<body>` (before `</body>`), add:

```html
<!-- Inventory modal -->
<div id="inv-modal" class="modal-bg" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:10000;align-items:center;justify-content:center;">
  <div class="modal-card" style="background:#161616;width:min(900px,92vw);max-height:85vh;overflow-y:auto;border-radius:8px;border:1px solid #333;">
    <div style="padding:14px;border-bottom:1px solid #333;display:flex;align-items:center;gap:10px;">
      <h3 style="margin:0;flex:1;">🧰 Baza Inventory</h3>
      <input id="inv-search" placeholder="Search…" style="background:#222;color:#fff;border:1px solid #333;padding:4px 8px;border-radius:4px;">
      <button id="inv-add" class="btn-sm">+ Add</button>
      <button onclick="InventoryModal.close()" style="background:none;border:none;color:#999;font-size:18px;cursor:pointer;">✕</button>
    </div>
    <div style="padding:12px;">
      <table style="width:100%;border-collapse:collapse;">
        <thead style="background:#1a1a1a;"><tr>
          <th style="padding:6px;text-align:left;">Name</th>
          <th style="padding:6px;text-align:left;">Category</th>
          <th style="padding:6px;text-align:left;">Qty</th>
          <th style="padding:6px;text-align:left;">Location</th>
          <th style="padding:6px;text-align:left;"></th>
        </tr></thead>
        <tbody id="inv-tbody"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- Equipment modal -->
<div id="equip-modal" class="modal-bg" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:10000;align-items:center;justify-content:center;">
  <div class="modal-card" style="background:#161616;width:min(900px,92vw);max-height:85vh;overflow-y:auto;border-radius:8px;border:1px solid #333;">
    <div style="padding:14px;border-bottom:1px solid #333;display:flex;align-items:center;gap:10px;">
      <h3 style="margin:0;flex:1;">🔧 Baza Equipment</h3>
      <select id="equip-filter-status" style="background:#222;color:#fff;border:1px solid #333;padding:4px 8px;border-radius:4px;">
        <option value="">All statuses</option>
        <option value="available">Available</option>
        <option value="in_use">In use</option>
        <option value="broken">Broken</option>
      </select>
      <button id="equip-add" class="btn-sm">+ Add</button>
      <button onclick="EquipmentModal.close()" style="background:none;border:none;color:#999;font-size:18px;cursor:pointer;">✕</button>
    </div>
    <div style="padding:12px;">
      <table style="width:100%;border-collapse:collapse;">
        <thead style="background:#1a1a1a;"><tr>
          <th style="padding:6px;text-align:left;">Name</th>
          <th style="padding:6px;text-align:left;">Type</th>
          <th style="padding:6px;text-align:left;">Location</th>
          <th style="padding:6px;text-align:left;">Status</th>
          <th style="padding:6px;text-align:left;"></th>
        </tr></thead>
        <tbody id="equip-tbody"></tbody>
      </table>
    </div>
  </div>
</div>

<script>
(function(){
  function escHtml(s){return String(s).replace(/[&<>"']/g,c=>({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

  async function _list(url) { return (await fetch(url).then(r=>r.json())).items || []; }

  // ---- Inventory ----
  async function invRefresh() {
    const items = await _list('/api/baza/inventory');
    const q = (document.getElementById('inv-search').value || '').toLowerCase();
    const tbody = document.getElementById('inv-tbody');
    tbody.innerHTML = '';
    items.filter(i => !q || (i.name||'').toLowerCase().includes(q) || (i.category||'').toLowerCase().includes(q)).forEach(i => {
      const tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid #222';
      tr.innerHTML = `
        <td style="padding:6px;">${escHtml(i.name)}</td>
        <td style="padding:6px;">${escHtml(i.category||'')}</td>
        <td style="padding:6px;"><input type="number" value="${i.quantity}" style="width:60px;background:#222;color:#fff;border:1px solid #333;" onchange="InventoryModal.patch(${i.id}, {quantity: parseInt(this.value)||1})"></td>
        <td style="padding:6px;">${escHtml(i.location||'')}</td>
        <td style="padding:6px;"><button class="btn-sm" style="background:#7a1d1d;color:#fff;" onclick="InventoryModal.del(${i.id})">✕</button></td>`;
      tbody.appendChild(tr);
    });
  }
  async function invAdd() {
    const name = prompt("Item name:"); if (!name) return;
    const category = prompt("Category (optional):") || '';
    const quantity = parseInt(prompt("Quantity:", "1"))||1;
    const location = prompt("Location (optional):") || '';
    await fetch('/api/baza/inventory', {method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name, category, quantity, location})});
    invRefresh();
  }
  async function invPatch(id, fields) {
    await fetch(`/api/baza/inventory/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(fields)});
  }
  async function invDel(id) {
    if (!confirm("Delete this item?")) return;
    await fetch(`/api/baza/inventory/${id}`,{method:'DELETE'});
    invRefresh();
  }
  window.InventoryModal = {
    open: () => { document.getElementById('inv-modal').style.display = 'flex'; invRefresh(); },
    close: () => { document.getElementById('inv-modal').style.display = 'none'; },
    patch: invPatch, del: invDel
  };
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('inv-add').addEventListener('click', invAdd);
    document.getElementById('inv-search').addEventListener('input', invRefresh);
  });

  // ---- Equipment ----
  async function equipRefresh() {
    const items = await _list('/api/baza/equipment');
    const status = document.getElementById('equip-filter-status').value;
    const tbody = document.getElementById('equip-tbody');
    tbody.innerHTML = '';
    items.filter(i => !status || i.status === status).forEach(i => {
      const tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid #222';
      tr.innerHTML = `
        <td style="padding:6px;">${escHtml(i.name)}</td>
        <td style="padding:6px;">${escHtml(i.type||'')}</td>
        <td style="padding:6px;">${escHtml(i.location||'')}</td>
        <td style="padding:6px;"><select onchange="EquipmentModal.patch(${i.id},{status:this.value})" style="background:#222;color:#fff;border:1px solid #333;">
          ${['available','in_use','broken','loaned'].map(s => `<option ${i.status===s?'selected':''} value="${s}">${s}</option>`).join('')}
        </select></td>
        <td style="padding:6px;"><button class="btn-sm" style="background:#7a1d1d;color:#fff;" onclick="EquipmentModal.del(${i.id})">✕</button></td>`;
      tbody.appendChild(tr);
    });
  }
  async function equipAdd() {
    const name = prompt("Equipment name:"); if (!name) return;
    const type = prompt("Type (e.g., 3d_printer, soldering, multimeter):") || '';
    const location = prompt("Location:") || '';
    await fetch('/api/baza/equipment',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name,type,location})});
    equipRefresh();
  }
  async function equipPatch(id, fields) {
    await fetch(`/api/baza/equipment/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(fields)});
    equipRefresh();
  }
  async function equipDel(id) {
    if (!confirm("Delete this item?")) return;
    await fetch(`/api/baza/equipment/${id}`,{method:'DELETE'});
    equipRefresh();
  }
  window.EquipmentModal = {
    open: () => { document.getElementById('equip-modal').style.display = 'flex'; equipRefresh(); },
    close: () => { document.getElementById('equip-modal').style.display = 'none'; },
    patch: equipPatch, del: equipDel
  };
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('equip-add').addEventListener('click', equipAdd);
    document.getElementById('equip-filter-status').addEventListener('change', equipRefresh);
  });
})();
</script>
```

- [ ] **Step 2: Restart + manual smoke**

```bash
sudo systemctl restart baza-dashboard.service
```

Open a project, click 🧰 Inventory in scaffold header, add an item, verify it appears. Repeat for 🔧 Equipment.

- [ ] **Step 3: Commit**

```bash
git add dashboard/templates/project_detail.html
git commit -m "scaffold T11: Inventory + Equipment body-level modals with CRUD"
```

---

## Task 12: Systemd service + timer for scaffold runner

**Files:**
- Create: `/etc/systemd/system/baza-scaffold-runner.service`
- Create: `/etc/systemd/system/baza-scaffold-runner.timer`

- [ ] **Step 1: Write service unit**

```bash
sudo tee /etc/systemd/system/baza-scaffold-runner.service > /dev/null <<'EOF'
[Unit]
Description=Baza scaffold runner — dispatches unblocked scaffold nodes
After=network.target

[Service]
Type=oneshot
User=switchhacker
Group=switchhacker
WorkingDirectory=/home/switchhacker/baza-empire/agent-framework-v3
Environment="PATH=/home/switchhacker/baza-empire/agent-framework-v3/venv/bin:/usr/bin:/bin"
ExecStart=/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/python /home/switchhacker/baza-empire/agent-framework-v3/core/scaffold_runner.py --once
StandardOutput=append:/var/log/baza/scaffold-runner.log
StandardError=append:/var/log/baza/scaffold-runner.log
EOF
```

- [ ] **Step 2: Write timer unit**

```bash
sudo tee /etc/systemd/system/baza-scaffold-runner.timer > /dev/null <<'EOF'
[Unit]
Description=Run baza scaffold runner every 30s
Requires=baza-scaffold-runner.service

[Timer]
OnBootSec=60s
OnUnitActiveSec=30s
AccuracySec=5s

[Install]
WantedBy=timers.target
EOF
```

- [ ] **Step 3: Ensure log dir, install, enable, start**

```bash
sudo mkdir -p /var/log/baza
sudo chown switchhacker:switchhacker /var/log/baza
sudo systemctl daemon-reload
sudo systemctl enable --now baza-scaffold-runner.timer
sudo systemctl list-timers | grep scaffold
```
Expected: timer is scheduled.

Trigger one manual run to verify:
```bash
sudo systemctl start baza-scaffold-runner.service
sleep 2
tail -20 /var/log/baza/scaffold-runner.log
```
Expected: a line like `{"started": []}` (no active scaffolds yet, that's fine).

- [ ] **Step 4: Commit the units into the repo for repeatability**

```bash
mkdir -p systemd
sudo cp /etc/systemd/system/baza-scaffold-runner.service systemd/
sudo cp /etc/systemd/system/baza-scaffold-runner.timer systemd/
sudo chown switchhacker:switchhacker systemd/baza-scaffold-runner.*
git add systemd/baza-scaffold-runner.service systemd/baza-scaffold-runner.timer
git commit -m "scaffold T12: systemd service + 30s timer for continuous scaffold runner"
```

---

## Task 13: Intent dispatcher wiring — `scaffold_decompose` intent

**Files:**
- Modify: `core/intent_dispatcher.py` (add new intent handler)

- [ ] **Step 1: Find existing intent handlers in `core/intent_dispatcher.py`**

Locate the `dispatch()` function and the existing intent-handler functions (`_handle_develop_or_iterate`, `_handle_test`, etc.). Add a new handler.

- [ ] **Step 2: Add `_handle_scaffold_decompose`**

In `core/intent_dispatcher.py`, register a new intent and handler:

```python
def _handle_scaffold_decompose(envelope, extra):
    """Insert a task that asks Claw to decompose the root scaffold node."""
    import sqlite3
    project_id = envelope.get("project_id")
    root_id = envelope.get("root_node_id")
    description = envelope.get("description", "")
    if not (project_id and root_id):
        return {"error": "missing project_id or root_node_id"}, 400

    desc = f"""You are decomposing a Baza scaffold project.

Project: {project_id}
Root node: {root_id}
Description: {description}

Step 1: Call ##SKILL:web_search{{"query": "...", "n": 5}}## to understand the topic.
Step 2: Plan the build tree. Identify hardware vs software branches.
Step 3: Call ##SKILL:scaffold_emit_nodes{{"project_id": "{project_id}", "parent_id": {root_id}, "nodes": [...]}}## with 4-8 child nodes covering research, decisions, hardware_components, firmware, software_module, integration, test, deploy, result.
Step 4: For hardware projects, include at least one decision node (e.g., choose MCU).
Step 5: Call ##SKILL:scaffold_complete_node{{"node_id": {root_id}, "result": "decomposed"}}## when finished.

The continuous scaffold runner will pick up the new pending nodes and dispatch them to Rex / Phil / yourself for execution.
"""
    # Insert into tasks table for Claw to pick up
    from pathlib import Path
    db = os.environ.get("BAZA_PROJECTS_DB",
                        str(Path(__file__).resolve().parents[1] / "dashboard" / "baza_projects.db"))
    con = sqlite3.connect(db)
    try:
        cur = con.execute("""
            INSERT INTO tasks
              (project_id, title, description, assigned_to, status, priority)
            VALUES (?, ?, ?, 'claw_batto', 'pending', 9)
        """, (project_id, f"[scaffold decompose] {description[:50]}", desc))
        task_id = cur.lastrowid
        con.commit()
    finally:
        con.close()
    return {"ok": True, "task_id": task_id, "agent": "claw_batto",
            "project_id": project_id}, 200
```

Wire it into the dispatch table. Find the existing dispatch routing (a dict or if/elif chain) and add `"scaffold_decompose": _handle_scaffold_decompose`.

- [ ] **Step 3: Manual smoke**

Restart the dashboard and the task runner, then POST to start a scaffold:

```bash
sudo systemctl restart baza-dashboard.service
# Pick any existing baza project ID (or create a quick test one)
TEST_PID=$(curl -s http://localhost:8888/api/baza/projects | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['projects'][0]['id'] if r.get('projects') else '')")
echo "Using project: $TEST_PID"
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"description": "tiny test build — single LED blink program"}' \
  http://localhost:8888/api/baza/projects/$TEST_PID/scaffold/start
# Should return {"root_node_id": ..., "task_id": ...}
sqlite3 dashboard/baza_projects.db "SELECT id, assigned_to, status FROM tasks ORDER BY id DESC LIMIT 3;"
```
Expected: a task for `claw_batto` is created with our decompose description.

- [ ] **Step 4: Commit**

```bash
git add core/intent_dispatcher.py
git commit -m "scaffold T13: intent dispatcher wires scaffold_decompose → claw_batto task"
```

---

## Task 14: End-to-end live smoke

**Files:** (verification only — no code)

- [ ] **Step 1: Run the full test suite for the subsystem**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
python -m pytest tests/test_baza_scaffold_*.py -v
```
Expected: ≥35 tests passing, 0 failing.

- [ ] **Step 2: Run live smoke end-to-end**

```bash
sudo systemctl restart baza-dashboard.service baza-scaffold-runner.timer
sleep 2

# Create a test project via API
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"name": "scaffold-smoke", "type": "other", "description": "smoke test"}' \
  http://localhost:8888/api/baza/projects | python3 -m json.tool

# Use the returned ID
PID=scaffold-smoke

# Kick off scaffold
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"description": "build a simple LED blink demo on Arduino Uno"}' \
  http://localhost:8888/api/baza/projects/$PID/scaffold/start | python3 -m json.tool

# Wait 60 seconds for runner + agents
sleep 60

# Verify nodes
curl -s http://localhost:8888/api/baza/projects/$PID/scaffold | python3 -m json.tool | head -50

# Verify event log
sqlite3 dashboard/baza_projects.db "SELECT id, event_type, actor, created_at FROM project_scaffold_events WHERE project_id='$PID' ORDER BY id DESC LIMIT 10;"
```

Acceptance:
- `/scaffold` returns at least the root node + (ideally) 1-2 child nodes that the agent emitted
- `project_scaffold_events` shows at least `created`, `started` events
- Tasks table shows tasks created for claw_batto (and possibly rex_smasher / phil_hass if agents have ticked)
- No errors in `/var/log/baza/scaffold-runner.log` or `journalctl -u baza-dashboard.service -n 50`

If agents haven't executed (e.g., no Telegram tokens / no LLM running), the test still passes as long as nodes + tasks are correctly created and events stream.

- [ ] **Step 3: Visual smoke**

In a browser, open `/projects/scaffold-smoke`, click **🌳 Scaffold**, observe:
- Tree shows the root + any child nodes
- Click a node → side panel opens
- Progress bar shows a non-zero percentage if anything has been marked done
- Click **🧰 Inventory** → modal opens, you can add an item
- Click **🔧 Equipment** → modal opens, you can add an item

- [ ] **Step 4: Final commit + session log entry**

```bash
# Append to session log
date_str=$(date '+%Y-%m-%d %H:%M')
cat >> /home/switchhacker/Desktop/baza-session-log.md <<EOF

### ${date_str} | Baza Projects "Live Build Tree" SHIPPED (Phases 1+2)
14 tasks landed. 5 new SQLite tables, ~20 REST routes + SSE stream,
scaffold runner systemd timer, 4 new shared skills, D3 tidy-tree UI in
🌳 Scaffold sub-tab on project_detail.html, body-level Inventory +
Equipment modals. Auto-decompose via intent_dispatcher → claw_batto.
Continuous runner dispatches unblocked nodes to rex/phil/claw.
Yellow→green progress bar, ⭐ on full deploy. Live SSE updates.

Test count: \$(python -m pytest tests/test_baza_scaffold_*.py --collect-only -q 2>/dev/null | tail -1)

Phase 3 deferred: <model-viewer> 3D hardware preview, cross-project
supplies roll-up actually computing, polished decision override UI,
⭐ celebration animation, per-node .glb upload.
EOF

# Empty milestone commit
git commit --allow-empty -m "scaffold: Phases 1+2 complete — live build tree shipped"
```

---

## Self-Review

### Spec coverage check
- ✅ Schema (5 tables + scaffold_paused) → T1
- ✅ Engine (CRUD, deps, progress, override, event bus) → T2
- ✅ Scaffold graph API + SSE → T3, T4
- ✅ BOM + checkbox unblock + promote-inventory → T5
- ✅ Inventory + Equipment + supplies stub → T6
- ✅ 4 new skills → T7
- ✅ Scaffold runner → T8
- ✅ D3 tree + side panel + SSE client → T9, T10
- ✅ Inventory + Equipment body-level modals → T11
- ✅ Systemd timer → T12
- ✅ Intent dispatcher wiring → T13
- ✅ Live E2E smoke → T14

### Placeholder scan
- No "TBD", "TODO", "implement later" placeholders
- All routes have complete handler code
- All tests have full test bodies
- All systemd unit files have full content

### Type / name consistency
- `ScaffoldEngine` referenced same name in T2-T13
- `event_bus` singleton consistent between T2 (definition) and T3 (SSE consumer)
- Node type strings consistent: `root, research, decision, hardware_component, firmware, software_module, integration, test, deploy, result, manual_step` across all tasks
- Status strings consistent: `pending, in_progress, done, blocked, awaiting_part, failed, overridden`
- Event type strings consistent across emitters + SSE listener list in T10
- DB path env var consistent: `BAZA_PROJECTS_DB` everywhere
- Skill arg key `_db_path` used consistently in T7 tests + skill main()

### Scope check
14 tasks. Same shape as Social Studio v2.2 (12 tasks). Bundles Phase 1 (schema, UI shell, manual CRUD, BOM, Inventory, Equipment) + Phase 2 (agent flow, runner, SSE, intent wiring). Phase 3 explicitly deferred.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-24-baza-projects-live-build-tree-plan.md`.**

Per user direction ("lets get this done"), executing **Subagent-Driven** automatically with `superpowers:subagent-driven-development`.
