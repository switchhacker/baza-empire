"""Tests for dashboard/crons_panel.py — Task 10 of the cron-improvements plan.

GET /crons/health (read-only dashboard page) + GET /api/crons/status (JSON).
Mounted at /crons/health rather than the bare /crons named in the task brief
because /crons + templates/crons.html are already an unrelated, pre-existing
manual crontab editor ("Cron Hub") — see crons_panel.py's module docstring
and task-10-report.md.
"""
import importlib
import os
import subprocess
import sys

import pytest
from flask import Flask

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.join(REPO_ROOT, "dashboard") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))

SAMPLE_LIST_TIMERS = (
    "NEXT                            LEFT LAST                              PASSED UNIT                           ACTIVATES\n"
    "Thu 2026-07-02 18:42:56 EDT       4s Thu 2026-07-02 18:42:26 EDT      25s ago baza-scaffold-runner.timer     baza-scaffold-runner.service\n"
    "Thu 2026-07-02 22:08:35 EDT 3h 25min Thu 2026-07-02 16:08:35 EDT 2h 34min ago baza-task-runner.timer         baza-task-runner.service\n"
    "\n"
    "2 timers listed.\n"
)

AGENTS_YAML_FIXTURE = """
agents:
  claw_batto:
    scheduled_tasks:
      - name: infra_health
        schedule: "0 */4 * * *"
        enabled: true
        script: agents/claw_batto/crons/infra_health.py
        log: logs/claw_infra.log
      - name: flaky_cron
        schedule: "*/5 * * * *"
        enabled: false
        script: agents/claw_batto/crons/flaky.py
        log: logs/flaky.log
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh crons_panel + core.cron_health_db bound to a tmp DB and a tmp
    agents.yaml fixture. Returns (test_client, crons_panel module, cron_health_db module).
    """
    db_path = str(tmp_path / "cron_health.db")
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", db_path)

    if "core.cron_health_db" in sys.modules:
        del sys.modules["core.cron_health_db"]
    import core.cron_health_db as cron_health_db
    cron_health_db.init()

    if "crons_panel" in sys.modules:
        del sys.modules["crons_panel"]
    import crons_panel
    importlib.reload(crons_panel)  # bind to the fresh core.cron_health_db instance

    agents_yaml = tmp_path / "agents.yaml"
    agents_yaml.write_text(AGENTS_YAML_FIXTURE)
    monkeypatch.setattr(crons_panel, "_agents_yaml_path", lambda: str(agents_yaml))

    # Default: deterministic systemd output, no dependency on the host's real timers.
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=SAMPLE_LIST_TIMERS, stderr="")

    monkeypatch.setattr(crons_panel.subprocess, "run", fake_run)

    app = Flask(
        "t",
        template_folder=os.path.join(REPO_ROOT, "dashboard", "templates"),
        static_folder=os.path.join(REPO_ROOT, "dashboard", "static"),
    )
    crons_panel.register(app)
    return app.test_client(), crons_panel, cron_health_db


# ── Page ─────────────────────────────────────────────────────────────────────

def test_crons_page_200_lists_declared(env):
    client, crons_panel, db = env
    db.record_run_start("infra_health")
    r = client.get("/crons/health")
    assert r.status_code == 200
    body = r.data.decode()
    assert "infra_health" in body
    assert "claw_batto" in body
    assert "flaky_cron" in body  # disabled crons still listed


def test_crons_page_renders_when_systemd_unavailable(env, monkeypatch):
    client, crons_panel, db = env
    monkeypatch.setattr(
        crons_panel.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no systemctl"))
    )
    r = client.get("/crons/health")
    assert r.status_code == 200
    assert "unavailable" in r.data.decode().lower()


# ── JSON API ─────────────────────────────────────────────────────────────────

def test_api_status_json_shape(env):
    client, crons_panel, db = env
    run_id = db.record_run_start("infra_health")
    db.record_run_end(run_id, "ok")

    r = client.get("/api/crons/status")
    assert r.status_code == 200
    data = r.get_json()
    assert set(["declared", "systemd_timers", "recent_runs", "generated_at"]) <= set(data.keys())

    names = [d["name"] for d in data["declared"]]
    assert "infra_health" in names and "flaky_cron" in names

    infra = next(d for d in data["declared"] if d["name"] == "infra_health")
    assert infra["agent"] == "claw_batto"
    assert infra["enabled"] is True
    assert infra["last_run"]["status"] == "ok"
    assert infra["last_run"]["status_icon"] == "✅"
    assert infra["next_fire"]  # croniter computed something

    flaky = next(d for d in data["declared"] if d["name"] == "flaky_cron")
    assert flaky["enabled"] is False
    assert flaky["last_run"] is None

    assert data["systemd_timers"]["available"] is True
    units = [t["unit"] for t in data["systemd_timers"]["timers"]]
    assert "baza-task-runner.timer" in units
    task_runner = next(t for t in data["systemd_timers"]["timers"] if t["unit"] == "baza-task-runner.timer")
    assert task_runner["activates"] == "baza-task-runner.service"
    assert task_runner["left"] == "3h 25min"

    assert len(data["recent_runs"]) == 1
    assert data["recent_runs"][0]["cron_name"] == "infra_health"


def test_api_status_systemd_unavailable_degrades(env, monkeypatch):
    client, crons_panel, db = env
    monkeypatch.setattr(
        crons_panel.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no systemctl"))
    )
    r = client.get("/api/crons/status")
    assert r.status_code == 200
    data = r.get_json()
    assert data["systemd_timers"]["available"] is False
    assert data["systemd_timers"]["timers"] == []
    assert data["systemd_timers"]["error"]


def test_recent_runs_limit_50(env):
    client, crons_panel, db = env
    for _ in range(55):
        db.record_run_start("infra_health")
    r = client.get("/api/crons/status")
    data = r.get_json()
    assert len(data["recent_runs"]) == 50


# ── XSS ──────────────────────────────────────────────────────────────────────

def test_error_tail_escaped(env):
    client, crons_panel, db = env
    run_id = db.record_run_start("infra_health")
    db.record_run_end(run_id, "error", error="<script>alert(1)</script>")

    r = client.get("/crons/health")
    assert r.status_code == 200
    body = r.data.decode()
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_error_tail_truncated_to_200(env):
    client, crons_panel, db = env
    run_id = db.record_run_start("infra_health")
    long_error = "x" * 500
    db.record_run_end(run_id, "error", error=long_error)

    r = client.get("/api/crons/status")
    data = r.get_json()
    infra = next(d for d in data["declared"] if d["name"] == "infra_health")
    assert len(infra["last_run"]["error_tail"]) == 200
