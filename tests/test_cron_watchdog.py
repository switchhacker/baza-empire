"""Tests for scripts/cron_watchdog.py — Task 9 of the cron-improvements plan.

Missed-schedule detection (2 missed fires + 15min grace, via croniter),
error-streak detection (last 3 runs all non-"ok"), and the drift-check /
alert-dispatch wiring in main(). All DB/subprocess/Telegram calls are
mocked — no real cron_health.db writes, no real crontab shelling out.
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _load_module():
    """cron_watchdog.py has no hyphen, but scripts/ isn't a package (no
    __init__.py) and isn't guaranteed to be on sys.path in test runs, so
    load it explicitly by file path — same trick used for sync-agent-crons.py
    (which *must* use this approach since it has a hyphen)."""
    path = os.path.join(REPO_ROOT, "scripts", "cron_watchdog.py")
    spec = importlib.util.spec_from_file_location("cron_watchdog", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cw():
    return _load_module()


# ── expected_prev_fire ──────────────────────────────────────────────────────

def test_expected_prev_fire_every4h(cw):
    now = datetime(2026, 7, 2, 18, 37)
    got = cw.expected_prev_fire("0 */4 * * *", now)
    assert got == datetime(2026, 7, 2, 16, 0)


def test_expected_prev_fire_daily(cw):
    now = datetime(2026, 7, 2, 18, 37)
    got = cw.expected_prev_fire("0 8 * * *", now)
    assert got == datetime(2026, 7, 2, 8, 0)


# ── find_problems: missed schedules ─────────────────────────────────────────

def test_missed_two_schedules_flagged(cw):
    now = datetime(2026, 7, 2, 18, 37)
    declared = [{"agent": "claw_batto", "name": "infra_health", "schedule": "0 */4 * * *"}]
    # Last run 10h ago -> only run predates both the 16:00 and 12:00 fires.
    last = (now - timedelta(hours=10)).isoformat(timespec="seconds")
    runs = {"infra_health": [{"started_at": last, "status": "ok"}]}

    problems = cw.find_problems(declared, runs, now)

    missed = [p for p in problems if p["type"] == "missed"]
    assert len(missed) == 1
    assert missed[0]["name"] == "infra_health"
    assert missed[0]["agent"] == "claw_batto"


def test_never_run_flagged_as_missed(cw):
    now = datetime(2026, 7, 2, 18, 37)
    declared = [{"agent": "duke_harmon", "name": "project_tracker", "schedule": "0 */4 * * *"}]
    runs = {}  # no history at all -> treat as "never run"

    problems = cw.find_problems(declared, runs, now)

    assert any(p["type"] == "missed" and p["name"] == "project_tracker" for p in problems)


def test_recent_run_ok_not_flagged(cw):
    now = datetime(2026, 7, 2, 18, 37)
    declared = [{"agent": "claw_batto", "name": "infra_health", "schedule": "0 */4 * * *"}]
    # Ran 30 minutes ago -> well within the most recent fire + grace.
    recent = (now - timedelta(minutes=30)).isoformat(timespec="seconds")
    runs = {"infra_health": [{"started_at": recent, "status": "ok"}]}

    problems = cw.find_problems(declared, runs, now)

    assert problems == []


def test_within_grace_window_not_flagged(cw):
    # schedule "0 8 * * *": prev1=2026-07-02 08:00, prev2=2026-07-01 08:00,
    # deadline=2026-07-01 07:45. A run at 07:50 on 07-01 is *after* deadline
    # (inside the 15-min grace) so must NOT be flagged as missed.
    now = datetime(2026, 7, 2, 18, 37)
    declared = [{"agent": "phil_hass", "name": "financial_review", "schedule": "0 8 * * *"}]
    last = datetime(2026, 7, 1, 7, 50).isoformat(timespec="seconds")
    runs = {"financial_review": [{"started_at": last, "status": "ok"}]}

    problems = cw.find_problems(declared, runs, now)

    assert problems == []


# ── find_problems: error streaks ────────────────────────────────────────────

def test_error_streak_flagged(cw):
    now = datetime(2026, 7, 2, 18, 37)
    declared = [{"agent": "phil_hass", "name": "financial_review", "schedule": "0 8 * * *"}]
    recent = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
    runs = {
        "financial_review": [
            {"started_at": recent, "status": "error"},
            {"started_at": recent, "status": "error"},
            {"started_at": recent, "status": "timeout"},
        ]
    }

    problems = cw.find_problems(declared, runs, now)

    errors = [p for p in problems if p["type"] == "errors"]
    assert len(errors) == 1
    assert errors[0]["name"] == "financial_review"
    # Recent successful-looking run timing means "missed" must not also fire.
    assert not any(p["type"] == "missed" for p in problems)


def test_error_streak_needs_three(cw):
    now = datetime(2026, 7, 2, 18, 37)
    declared = [{"agent": "phil_hass", "name": "financial_review", "schedule": "0 8 * * *"}]
    recent = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
    runs = {
        "financial_review": [
            {"started_at": recent, "status": "error"},
            {"started_at": recent, "status": "error"},
        ]
    }

    problems = cw.find_problems(declared, runs, now)

    assert not any(p["type"] == "errors" for p in problems)


def test_error_streak_broken_by_one_ok(cw):
    now = datetime(2026, 7, 2, 18, 37)
    declared = [{"agent": "phil_hass", "name": "financial_review", "schedule": "0 8 * * *"}]
    recent = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
    runs = {
        "financial_review": [
            {"started_at": recent, "status": "error"},
            {"started_at": recent, "status": "ok"},
            {"started_at": recent, "status": "error"},
        ]
    }

    problems = cw.find_problems(declared, runs, now)

    assert not any(p["type"] == "errors" for p in problems)


# ── load_declared_crons ─────────────────────────────────────────────────────

def test_load_declared_crons_from_fixture(cw, tmp_path):
    fixture = tmp_path / "agents.fixture.yaml"
    fixture.write_text(
        "agents:\n"
        "  fixture_agent:\n"
        "    scheduled_tasks:\n"
        "      - name: on_task\n"
        "        schedule: '*/30 * * * *'\n"
        "        script: agents/fixture_agent/crons/on_task.py\n"
        "        enabled: true\n"
        "      - name: off_task\n"
        "        schedule: '* * * * *'\n"
        "        script: agents/fixture_agent/crons/off_task.py\n"
        "        enabled: false\n"
    )
    declared = cw.load_declared_crons(str(fixture))
    names = {d["name"] for d in declared}
    assert "on_task" in names
    assert "off_task" not in names


def test_load_declared_crons_missing_file_returns_empty(cw, tmp_path):
    missing = tmp_path / "nope.yaml"
    assert cw.load_declared_crons(str(missing)) == []


def test_load_declared_crons_skips_sh_scripts(cw, tmp_path):
    """A .sh-scripted cron (e.g. rotate_logs before its .py wrapper) can't
    call cron_helpers.cron_run() to heartbeat -- it must never enter the
    missed-schedule check at all, since there's no run history it could
    ever satisfy."""
    fixture = tmp_path / "agents.fixture.yaml"
    fixture.write_text(
        "agents:\n"
        "  claw_batto:\n"
        "    scheduled_tasks:\n"
        "      - name: rotate_logs\n"
        "        schedule: '0 3 * * 0'\n"
        "        script: scripts/rotate_logs.sh\n"
        "        enabled: true\n"
        "      - name: infra_health\n"
        "        schedule: '0 */4 * * *'\n"
        "        script: agents/claw_batto/crons/infra_health.py\n"
        "        enabled: true\n"
    )
    declared = cw.load_declared_crons(str(fixture))
    names = {d["name"] for d in declared}
    assert "rotate_logs" not in names
    assert "infra_health" in names


def test_load_declared_crons_missing_script_field_still_included(cw, tmp_path):
    """No `script` field at all (older-style fixture entries) must not be
    mistaken for a .sh script -- only an actual .sh suffix skips a task."""
    fixture = tmp_path / "agents.fixture.yaml"
    fixture.write_text(
        "agents:\n"
        "  fixture_agent:\n"
        "    scheduled_tasks:\n"
        "      - name: on_task\n"
        "        schedule: '*/30 * * * *'\n"
        "        enabled: true\n"
    )
    declared = cw.load_declared_crons(str(fixture))
    assert {d["name"] for d in declared} == {"on_task"}


# ── main(): alert dispatch + drift check wiring ─────────────────────────────

def test_main_dispatches_alerts_and_drift(cw, monkeypatch):
    sent = []

    def fake_send_alert(cron_name, message, alert_key, renotify_hours=None, **kwargs):
        sent.append((cron_name, alert_key, renotify_hours))
        return True

    monkeypatch.setattr(cw, "_resolve_send_alert", lambda: fake_send_alert)
    monkeypatch.setattr(cw, "load_declared_crons",
                         lambda *a, **k: [{"agent": "claw_batto", "name": "infra_health", "schedule": "0 */4 * * *"}])

    class _FakeDB:
        @staticmethod
        def init():
            pass

        @staticmethod
        def recent_runs(cron_name=None, limit=200):
            return []  # never run -> missed

    monkeypatch.setattr(cw, "db", _FakeDB)
    monkeypatch.setattr(cw, "check_drift", lambda: (True, "drift output"))

    cw.main()

    keys = {k for (_, k, _) in sent}
    assert "cronwd:infra_health:missed" in keys
    assert "cronwd:drift" in keys
    renotify_by_key = {k: r for (_, k, r) in sent}
    assert renotify_by_key["cronwd:infra_health:missed"] == 6
    assert renotify_by_key["cronwd:drift"] == 24


def test_main_no_alerts_when_healthy(cw, monkeypatch):
    sent = []
    monkeypatch.setattr(cw, "_resolve_send_alert", lambda: (lambda *a, **k: sent.append(a) or True))
    monkeypatch.setattr(cw, "load_declared_crons",
                         lambda *a, **k: [{"agent": "claw_batto", "name": "infra_health", "schedule": "0 */4 * * *"}])

    now = datetime.now()
    recent_iso = (now - timedelta(minutes=5)).isoformat(timespec="seconds")

    class _FakeDB:
        @staticmethod
        def init():
            pass

        @staticmethod
        def recent_runs(cron_name=None, limit=200):
            return [{"started_at": recent_iso, "status": "ok"}]

    monkeypatch.setattr(cw, "db", _FakeDB)
    monkeypatch.setattr(cw, "check_drift", lambda: (False, ""))

    cw.main()

    assert sent == []


# ── check_drift ──────────────────────────────────────────────────────────────

def test_check_drift_nonzero_rc_is_drifted(cw, monkeypatch):
    class _Proc:
        returncode = 1
        stdout = "some drift\n"
        stderr = ""

    monkeypatch.setattr(cw.subprocess, "run", lambda *a, **k: _Proc())
    drifted, output = cw.check_drift()
    assert drifted is True
    assert "some drift" in output


def test_check_drift_zero_rc_is_clean(cw, monkeypatch):
    class _Proc:
        returncode = 0
        stdout = "in sync\n"
        stderr = ""

    monkeypatch.setattr(cw.subprocess, "run", lambda *a, **k: _Proc())
    drifted, output = cw.check_drift()
    assert drifted is False


# ── check_drift: target-aware (crontab vs systemd cutover) ─────────────────

def test_check_drift_defaults_to_crontab_target(cw, monkeypatch, tmp_path):
    """No baza-cron-*.timer units present under the systemd user dir -> the
    crontab target is still live, so `--check` runs with no --target flag
    (sync-agent-crons.py's own default)."""
    monkeypatch.setenv("BAZA_SYSTEMD_USER_DIR", str(tmp_path))  # empty dir

    captured = {}

    class _Proc:
        returncode = 0
        stdout = "in sync\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(cw.subprocess, "run", fake_run)
    cw.check_drift()

    assert "--target" not in captured["cmd"]


def test_check_drift_switches_to_systemd_target_when_timers_present(cw, monkeypatch, tmp_path):
    """Any baza-cron-*.timer unit under the systemd user dir means the
    systemd cutover has happened -- check_drift() must run `--check --target
    systemd` instead, or this goes permanently red once the managed crontab
    lines are gone."""
    (tmp_path / "baza-cron-infra-health.timer").write_text("[Timer]\n")
    monkeypatch.setenv("BAZA_SYSTEMD_USER_DIR", str(tmp_path))

    captured = {}

    class _Proc:
        returncode = 0
        stdout = "in sync\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(cw.subprocess, "run", fake_run)
    cw.check_drift()

    assert "--target" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--target") + 1] == "systemd"


def test_check_drift_ignores_non_timer_files(cw, monkeypatch, tmp_path):
    """A baza-cron-*.service (no .timer) or an unrelated file must not count
    as cutover evidence -- only an actual .timer unit does."""
    (tmp_path / "baza-cron-infra-health.service").write_text("[Service]\n")
    (tmp_path / "some-other-file.timer").write_text("[Timer]\n")
    monkeypatch.setenv("BAZA_SYSTEMD_USER_DIR", str(tmp_path))

    captured = {}

    class _Proc:
        returncode = 0
        stdout = "in sync\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(cw.subprocess, "run", fake_run)
    cw.check_drift()

    assert "--target" not in captured["cmd"]


def test_check_drift_missing_systemd_dir_defaults_to_crontab(cw, monkeypatch, tmp_path):
    """The systemd user dir not existing at all (fresh box, pre-cutover)
    must not blow up -- just treat it as no cutover."""
    nonexistent = tmp_path / "does" / "not" / "exist"
    monkeypatch.setenv("BAZA_SYSTEMD_USER_DIR", str(nonexistent))

    captured = {}

    class _Proc:
        returncode = 0
        stdout = "in sync\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(cw.subprocess, "run", fake_run)
    cw.check_drift()

    assert "--target" not in captured["cmd"]
