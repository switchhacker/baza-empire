"""Tests for core/task_events.py — the visibility pipeline write helper."""
import json
import os
import sys
import tempfile
import importlib

import pytest


@pytest.fixture()
def task_events(monkeypatch):
    """Fresh module instance pointing at a temporary DB."""
    tmpdir = tempfile.mkdtemp(prefix="task_events_")
    db = os.path.join(tmpdir, "events.db")
    monkeypatch.setenv("BAZA_TASK_EVENTS_DB", db)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    if "core.task_events" in sys.modules:
        del sys.modules["core.task_events"]
    mod = importlib.import_module("core.task_events")
    mod.init_schema()
    return mod


def test_init_schema_idempotent(task_events):
    task_events.init_schema()
    task_events.init_schema()  # second call should not raise
    rid = task_events.emit("task_started", task_id="t1", payload={"title": "ok"})
    assert isinstance(rid, int) and rid > 0


def test_emit_and_list(task_events):
    a = task_events.emit("task_started", task_id="t1", agent_id="claw_batto",
                         payload={"title": "Set up SSL"})
    b = task_events.emit("skill_invoked", task_id="t1", agent_id="claw_batto",
                         payload={"name": "shell", "args": {"cmd": "ls"}}, parent_event_id=a)
    c = task_events.emit("skill_result", task_id="t1", agent_id="claw_batto",
                         payload={"name": "shell", "ok": True, "output_snippet": "hi"},
                         parent_event_id=b)
    task_events.emit("task_completed", task_id="t1", agent_id="claw_batto",
                     payload={"notes_snippet": "done"})

    listed = task_events.list_events(task_id="t1")
    assert len(listed) == 4
    # Reverse-chronological
    assert listed[0]["kind"] == "task_completed"
    assert listed[-1]["kind"] == "task_started"


def test_chain_for_task_nests_children(task_events):
    a = task_events.emit("task_started", task_id="t2", payload={"title": "x"})
    b = task_events.emit("skill_invoked", task_id="t2", parent_event_id=a,
                         payload={"name": "x"})
    task_events.emit("skill_result", task_id="t2", parent_event_id=b,
                     payload={"name": "x", "ok": True})

    chain = task_events.chain_for_task("t2")
    # Two roots: task_started and skill_invoked? No — skill_invoked has parent a.
    # Root: task_started. skill_invoked nested under it. skill_result under skill_invoked.
    assert len(chain) == 1
    root = chain[0]
    assert root["kind"] == "task_started"
    assert len(root["children"]) == 1
    assert root["children"][0]["kind"] == "skill_invoked"
    assert root["children"][0]["children"][0]["kind"] == "skill_result"


def test_payload_truncation(task_events):
    big = "x" * 5000
    rid = task_events.emit("task_progress", task_id="tbig",
                           payload={"notes_snippet": big})
    assert rid is not None
    fetched = task_events.list_events(task_id="tbig")
    val = fetched[0]["payload"]["notes_snippet"]
    assert len(val) <= task_events.PAYLOAD_FIELD_MAX + 32
    assert val.endswith("…[truncated]")


def test_emit_failure_never_raises(task_events, monkeypatch):
    """If the DB path is unwritable, emit returns None instead of crashing."""
    monkeypatch.setattr(task_events, "DB_PATH", "/nonexistent/dir/that/does/not/exist.db")
    rid = task_events.emit("task_started", task_id="tx", payload={"title": "fails"})
    assert rid is None  # graceful


def test_unknown_kind_still_writes(task_events):
    """Schema is a hint; arbitrary kinds are accepted to allow evolution."""
    rid = task_events.emit("custom_kind_xyz", task_id="tu", payload={"foo": "bar"})
    assert rid is not None


def test_list_filters(task_events):
    task_events.emit("task_started", task_id="t10", agent_id="simon_bately", payload={})
    task_events.emit("task_started", task_id="t11", agent_id="claw_batto", payload={})
    task_events.emit("artifact_saved", task_id="t10", agent_id="simon_bately",
                     payload={"path": "/x"})

    by_agent = task_events.list_events(agent_id="claw_batto")
    assert {e["task_id"] for e in by_agent} == {"t11"}

    by_kind = task_events.list_events(kinds=["artifact_saved"])
    assert all(e["kind"] == "artifact_saved" for e in by_kind)
    assert len(by_kind) == 1


def test_recent_task_summaries(task_events):
    task_events.emit("task_started", task_id="ta", agent_id="rex_valor",
                     project_id="pa", payload={})
    task_events.emit("task_completed", task_id="ta", agent_id="rex_valor",
                     project_id="pa", payload={})
    task_events.emit("task_started", task_id="tb", agent_id="phil_hass",
                     project_id="pb", payload={})
    task_events.emit("task_blocked", task_id="tb", agent_id="phil_hass",
                     project_id="pb", payload={})

    sums = task_events.recent_task_summaries()
    assert len(sums) == 2
    by_task = {s["task_id"]: s for s in sums}
    assert by_task["ta"]["has_completed"] == 1
    assert by_task["tb"]["has_blocked"] == 1
    assert by_task["ta"]["event_count"] == 2


def test_prune_older_than(task_events):
    task_events.emit("task_started", task_id="old", payload={})
    # Manually backdate the row to 100 days ago
    import sqlite3
    conn = sqlite3.connect(task_events.DB_PATH)
    conn.execute("UPDATE task_events SET ts = datetime('now','-100 days') WHERE task_id='old'")
    conn.commit()
    conn.close()
    task_events.emit("task_started", task_id="new", payload={})

    deleted = task_events.prune_older_than(days=90)
    assert deleted == 1
    remaining = task_events.list_events()
    assert {e["task_id"] for e in remaining} == {"new"}
