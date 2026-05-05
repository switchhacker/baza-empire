"""Tests for core/preview_supervisor.py — long-running preview lifecycle."""
import importlib
import os
import sys
import tempfile
import time

import pytest


@pytest.fixture()
def supervisor(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="preview_sup_")
    proj_root = os.path.join(tmp, "projects")
    db_path = os.path.join(tmp, "test.db")
    monkeypatch.setenv("BAZA_PROJECTS_ROOT", proj_root)
    monkeypatch.setenv("BAZA_TASK_EVENTS_DB", db_path)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    for mod_name in ("core.preview_supervisor", "core.baza_projects", "core.task_events"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY, name TEXT, description TEXT,
            status TEXT DEFAULT 'active', launch_date TEXT, owner TEXT,
            created_at TEXT
        );
        """
    )
    conn.close()
    bp = importlib.import_module("core.baza_projects")
    bp.ensure_schema()
    sup = importlib.import_module("core.preview_supervisor")
    return sup, bp


def _make_project(bp, name="Preview Test", type_="library", run_cmd=None):
    p = bp.create_project(name=name, type_=type_)
    if run_cmd is not None:
        bp.update_manifest(p["id"], {"commands": {"build":"", "test":"", "run": run_cmd, "preview": run_cmd, "deploy":""}})
    return p


def test_status_when_not_running(supervisor):
    sup, bp = supervisor
    p = _make_project(bp)
    s = sup.status(p["id"])
    assert s["running"] is False


def test_start_requires_command(supervisor):
    sup, bp = supervisor
    p = _make_project(bp, run_cmd="")  # blank
    res = sup.start(p["id"], slot="preview")
    assert res["started"] is False
    assert "no command" in res["error"]


def test_start_invalid_slot(supervisor):
    sup, bp = supervisor
    p = _make_project(bp, run_cmd="echo hi")
    res = sup.start(p["id"], slot="deploy")
    assert res["started"] is False
    assert "slot" in res["error"]


def test_start_and_stop_cycle(supervisor):
    sup, bp = supervisor
    p = _make_project(bp, run_cmd='python3 -c "import time; print(\\"ready\\"); time.sleep(60)"')
    res = sup.start(p["id"], slot="run")
    assert res["started"] is True
    pid = res["pid"]
    port = res["port"]
    assert isinstance(pid, int) and pid > 0
    assert 9000 <= port < 9100
    # Status should show running
    s = sup.status(p["id"])
    assert s["running"] is True
    assert s["pid"] == pid
    # Starting again should refuse
    res2 = sup.start(p["id"], slot="run")
    assert res2["started"] is False
    assert "already running" in res2["error"]
    # Logs should at least exist
    logs = sup.tail_logs(p["id"])
    assert "preview log" in logs
    # Stop
    stopped = sup.stop(p["id"])
    assert stopped["stopped"] is True
    # Give the kernel a moment to reap
    time.sleep(0.3)
    assert sup.status(p["id"])["running"] is False


def test_stop_when_not_running(supervisor):
    sup, bp = supervisor
    p = _make_project(bp, run_cmd="echo hi")
    res = sup.stop(p["id"])
    assert res["stopped"] is False
    assert "not running" in res["error"]


def test_stale_pidfile_cleaned(supervisor):
    sup, bp = supervisor
    p = _make_project(bp, run_cmd="echo hi")
    # Write a fake pidfile pointing at a long-dead pid
    fake = {"pid": 1, "pgid": 1, "port": 9999, "slot": "run", "command": "x", "started_at": "1970-01-01T00:00:00Z"}
    pid_path = os.path.join(p["path"], ".preview.json")
    import json as _json
    # Use pid 99999999 which is virtually never alive
    fake["pid"] = 99999999
    fake["pgid"] = 99999999
    with open(pid_path, "w") as f:
        _json.dump(fake, f)
    s = sup.status(p["id"])
    assert s["running"] is False
    assert s.get("stale_cleaned") is True
