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
    assert eng.is_runnable(child) is False
    eng.update_node(root, status="in_progress")
    assert eng.is_runnable(child) is True


def test_dep_satisfaction_via_edge(db):
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    a = eng.create_node("p1", node_type="research", title="a")
    b = eng.create_node("p1", node_type="research", title="b")
    eng.add_edge("p1", from_node=a, to_node=b, edge_type="depends_on")
    eng.update_node(a, status="in_progress")
    assert eng.is_runnable(b) is False
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
    eng.override_decision(decision, chosen_option="ultrasonic", reason="user pref")
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
