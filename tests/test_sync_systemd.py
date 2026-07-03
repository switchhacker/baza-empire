"""Tests for scripts/sync-agent-crons.py's `--target systemd` mode — Task 11:
cron_to_oncalendar() conversion, render_units() unit-file rendering, the
plan/apply diff engine, and the injectable-runner crontab migration.

Runs against a FIXTURE agents.yaml (never the real config/agents.yaml) by
monkeypatching the module's AGENTS_YAML constant before calling
load_declared_tasks(), same convention as tests/test_sync_timeouts.py. All
systemd/crontab side effects go through an injected fake runner — no test
here ever shells out to the real systemctl or crontab, and --apply is never
exercised against the real filesystem (SYSTEMD_USER_DIR is always a tmp_path).
"""
import argparse
import importlib.util
import os
import sys
import textwrap

import pytest
import yaml as _yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC_SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "sync-agent-crons.py")


def _load_sync_module():
    # Hyphenated filename -> can't `import`, must load by path.
    spec = importlib.util.spec_from_file_location("sync_agent_crons_under_test_systemd", SYNC_SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def sync_mod():
    return _load_sync_module()


FIXTURE_YAML = textwrap.dedent(
    """\
    agents:
      fixture_agent:
        scheduled_tasks:
          - name: infra_health
            schedule: "0 */6 * * *"
            script: agents/fixture_agent/crons/infra_health.py
            log: logs/fixture_infra_health.log
            enabled: true
          - name: weekly_digest
            schedule: "0 7 * * 1"
            script: agents/fixture_agent/crons/weekly_digest.py
            log: logs/fixture_weekly_digest.log
            timeout_min: 15
            enabled: true
          - name: rotate
            schedule: "10 4 * * *"
            script: scripts/rotate_logs.sh
            log: logs/fixture_rotate.log
            enabled: true
          - name: disabled_task
            schedule: "* * * * *"
            script: agents/fixture_agent/crons/off.py
            enabled: false
    """
)


@pytest.fixture()
def fixture_yaml_path(tmp_path):
    p = tmp_path / "agents.fixture.yaml"
    p.write_text(FIXTURE_YAML)
    return str(p)


def _declared(sync_mod, fixture_yaml_path, monkeypatch):
    monkeypatch.setattr(sync_mod, "AGENTS_YAML", fixture_yaml_path)
    return sync_mod.load_declared_tasks()


# ── cron_to_oncalendar() ────────────────────────────────────────────────

CONVERSIONS = [
    ("0 */6 * * *", "*-*-* 00/6:00:00"),
    ("45 */6 * * *", "*-*-* 00/6:45:00"),
    ("0 9 * * *", "*-*-* 09:00:00"),
    ("0 7 * * 1", "Mon *-*-* 07:00:00"),
    ("0 6 * * 3", "Wed *-*-* 06:00:00"),
    ("0 5-19/2 * * *", "*-*-* 05..19/2:00:00"),
    ("15 6 * * 1-5", "Mon..Fri *-*-* 06:15:00"),
    ("*/30 * * * *", "*-*-* *:00/30:00"),
]


@pytest.mark.parametrize("expr,expected", CONVERSIONS)
def test_oncalendar_conversions(sync_mod, expr, expected):
    assert sync_mod.cron_to_oncalendar(expr) == expected


UNSUPPORTED = [
    "0 9 1 * *",       # day-of-month restriction
    "0 9 * 6 *",       # month restriction
    "0 9 * * 1,3,5",   # comma list on day-of-week
    "*/15 * * * mon",  # non-numeric day-of-week
    "0 9 * *",         # wrong field count
    "@reboot",         # non-standard cron alias
]


@pytest.mark.parametrize("expr", UNSUPPORTED)
def test_oncalendar_unsupported_raises(sync_mod, expr):
    with pytest.raises(ValueError):
        sync_mod.cron_to_oncalendar(expr)


def test_real_agents_yaml_schedules_convert(sync_mod):
    """Every schedule declared (enabled) in the real config/agents.yaml must
    survive cron_to_oncalendar() without raising -- read-only, never mutated."""
    with open(sync_mod.AGENTS_YAML) as f:
        data = _yaml.safe_load(f) or {}
    agents = data.get("agents", data)
    schedules = set()
    for agent_id, cfg in agents.items():
        if not isinstance(cfg, dict):
            continue
        for t in cfg.get("scheduled_tasks", []) or []:
            if not t.get("enabled", True):
                continue
            schedules.add(t["schedule"])
    assert schedules, "expected at least one declared schedule in the real agents.yaml"
    for s in schedules:
        sync_mod.cron_to_oncalendar(s)  # must not raise


# ── render_units() ───────────────────────────────────────────────────────

def test_render_units_fields(sync_mod):
    task = {
        "name": "fixture_agent_infra_health",
        "schedule": "0 */6 * * *",
        "script": "agents/fixture_agent/crons/infra_health.py",
        "log": "logs/fixture_infra_health.log",
        "timeout_min": 45,
        "agent_id": "fixture_agent",
        "task_name": "infra_health",
    }
    service_text, timer_text = sync_mod.render_units(task)

    abs_log = os.path.join(sync_mod.REPO_ROOT, "logs/fixture_infra_health.log")
    secrets_env = os.path.join(sync_mod.REPO_ROOT, "configs", "secrets.env")

    assert "Type=oneshot" in service_text
    assert f"WorkingDirectory={sync_mod.REPO_ROOT}" in service_text
    assert sync_mod.PYTHON_BIN in service_text
    assert f"StandardOutput=append:{abs_log}" in service_text
    assert "StandardError=inherit" in service_text
    assert "RuntimeMaxSec=2700" in service_text  # 45 * 60
    assert f"EnvironmentFile={secrets_env}" in service_text
    assert "OnFailure=baza-cron-alert@%n.service" in service_text

    assert "OnCalendar=*-*-* 00/6:00:00" in timer_text
    assert "Persistent=true" in timer_text
    assert "WantedBy=timers.target" in timer_text


def test_sh_script_execstart_bash(sync_mod):
    task = {
        "name": "fixture_agent_rotate",
        "schedule": "10 4 * * *",
        "script": "scripts/rotate_logs.sh",
        "log": "logs/fixture_rotate.log",
        "timeout_min": 30,
        "agent_id": "fixture_agent",
        "task_name": "rotate",
    }
    service_text, _ = sync_mod.render_units(task)
    abs_script = os.path.join(sync_mod.REPO_ROOT, "scripts/rotate_logs.sh")
    assert f"ExecStart=bash {abs_script}" in service_text
    assert sync_mod.PYTHON_BIN not in service_text


def test_build_systemd_task_matches_build_cron_line_naming(sync_mod, fixture_yaml_path, monkeypatch):
    declared = _declared(sync_mod, fixture_yaml_path, monkeypatch)
    agent_id, task = next((a, t) for a, t in declared if t["name"] == "infra_health")
    name, schedule, _cmd = sync_mod.build_cron_line(agent_id, task)
    t = sync_mod.build_systemd_task(agent_id, task)
    assert t["name"] == name
    assert t["schedule"] == schedule
    assert t["timeout_min"] == 30  # default


# ── plan_systemd() dry-run diff (no writes) ─────────────────────────────

def test_dry_run_no_writes(monkeypatch, tmp_path, fixture_yaml_path):
    """Brief-mandated scenario: BAZA_SYSTEMD_USER_DIR points at a tmp dir;
    dry-run --target systemd must not create it or write any files."""
    systemd_dir = tmp_path / "systemd-user"
    monkeypatch.setenv("BAZA_SYSTEMD_USER_DIR", str(systemd_dir))
    mod = _load_sync_module()  # reload so SYSTEMD_USER_DIR picks up the env var
    monkeypatch.setattr(mod, "AGENTS_YAML", fixture_yaml_path)
    declared = mod.load_declared_tasks()

    assert mod.SYSTEMD_USER_DIR == str(systemd_dir)

    args = argparse.Namespace(apply=False, check=False, target="systemd")
    rc = mod.main_systemd(declared, args)

    assert rc == 0
    assert not systemd_dir.exists()


def test_plan_add_for_new_declared_tasks(sync_mod, fixture_yaml_path, tmp_path, monkeypatch):
    declared = _declared(sync_mod, fixture_yaml_path, monkeypatch)
    systemd_dir = tmp_path / "systemd-user"
    entries, plan, alert_status, errors = sync_mod.plan_systemd(declared, systemd_dir=str(systemd_dir))
    assert not errors
    assert len(entries) == 3  # disabled_task excluded
    assert all(status == "+" for status in plan.values())
    assert not systemd_dir.exists()  # planning never creates the dir


def test_plan_in_sync_when_units_match(sync_mod, fixture_yaml_path, tmp_path, monkeypatch):
    declared = _declared(sync_mod, fixture_yaml_path, monkeypatch)
    systemd_dir = tmp_path / "systemd-user"
    systemd_dir.mkdir()
    entries, _plan, _alert, _errors = sync_mod.plan_systemd(declared, systemd_dir=str(systemd_dir))
    for name, e in entries.items():
        svc, timer = sync_mod.systemd_unit_paths(name, str(systemd_dir))
        with open(svc, "w") as f:
            f.write(e["service_text"])
        with open(timer, "w") as f:
            f.write(e["timer_text"])

    _entries2, plan2, _alert2, _errors2 = sync_mod.plan_systemd(declared, systemd_dir=str(systemd_dir))
    assert plan2 and all(status == "=" for status in plan2.values())


def test_plan_update_when_content_differs(sync_mod, fixture_yaml_path, tmp_path, monkeypatch):
    declared = _declared(sync_mod, fixture_yaml_path, monkeypatch)
    systemd_dir = tmp_path / "systemd-user"
    systemd_dir.mkdir()
    entries, _plan, _alert, _errors = sync_mod.plan_systemd(declared, systemd_dir=str(systemd_dir))
    name0 = next(iter(entries))
    svc, timer = sync_mod.systemd_unit_paths(name0, str(systemd_dir))
    with open(svc, "w") as f:
        f.write("stale content\n")
    with open(timer, "w") as f:
        f.write(entries[name0]["timer_text"])

    _entries2, plan2, _alert2, _errors2 = sync_mod.plan_systemd(declared, systemd_dir=str(systemd_dir))
    assert plan2[name0] == "~"


def test_plan_remove_for_orphaned_unit(sync_mod, fixture_yaml_path, tmp_path, monkeypatch):
    declared = _declared(sync_mod, fixture_yaml_path, monkeypatch)
    systemd_dir = tmp_path / "systemd-user"
    systemd_dir.mkdir()
    (systemd_dir / "baza-cron-stale_agent_old_task.service").write_text("stale")
    (systemd_dir / "baza-cron-stale_agent_old_task.timer").write_text("stale")

    _entries, plan, _alert, _errors = sync_mod.plan_systemd(declared, systemd_dir=str(systemd_dir))
    assert plan["stale_agent_old_task"] == "-"


def test_check_flag_fails_on_drift(sync_mod, fixture_yaml_path, monkeypatch):
    declared = _declared(sync_mod, fixture_yaml_path, monkeypatch)
    args = argparse.Namespace(apply=False, check=True, target="systemd")
    rc = sync_mod.main_systemd(declared, args)
    assert rc == 1


def test_check_flag_passes_when_in_sync(sync_mod, fixture_yaml_path, tmp_path, monkeypatch):
    declared = _declared(sync_mod, fixture_yaml_path, monkeypatch)
    systemd_dir = tmp_path / "systemd-user"
    systemd_dir.mkdir()
    entries, _plan, _alert, _errors = sync_mod.plan_systemd(declared, systemd_dir=str(systemd_dir))
    for name, e in entries.items():
        svc, timer = sync_mod.systemd_unit_paths(name, str(systemd_dir))
        with open(svc, "w") as f:
            f.write(e["service_text"])
        with open(timer, "w") as f:
            f.write(e["timer_text"])
    # also satisfy the alert-template half of the plan
    os.makedirs(systemd_dir, exist_ok=True)
    with open(os.path.join(str(systemd_dir), sync_mod.ALERT_TEMPLATE_NAME), "w") as f:
        f.write(sync_mod._read_file(sync_mod.ALERT_TEMPLATE_SRC))

    monkeypatch.setattr(sync_mod, "SYSTEMD_USER_DIR", str(systemd_dir))
    args = argparse.Namespace(apply=False, check=True, target="systemd")
    rc = sync_mod.main_systemd(declared, args)
    assert rc == 0


# ── apply_systemd() — injectable runner, never touches real systemd/crontab ──

class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_fake_runner(crontab_out=""):
    calls = []

    def runner(cmd, input=None):
        calls.append((cmd, input))
        if cmd[:2] == ["crontab", "-l"]:
            return FakeCompleted(0, crontab_out, "")
        if cmd[:2] == ["crontab", "-"]:
            return FakeCompleted(0, "", "")
        return FakeCompleted(0, "", "")

    return runner, calls


def test_apply_writes_units_daemon_reload_enable(sync_mod, fixture_yaml_path, tmp_path, monkeypatch):
    declared = _declared(sync_mod, fixture_yaml_path, monkeypatch)
    systemd_dir = tmp_path / "systemd-user"

    runner, calls = make_fake_runner(crontab_out="")
    result = sync_mod.apply_systemd(
        declared, systemd_dir=str(systemd_dir), runner=runner, today="2026-07-02"
    )

    assert set(result["written"]) == {
        "fixture_agent_infra_health",
        "fixture_agent_weekly_digest",
        "fixture_agent_rotate",
    }
    for name in result["written"]:
        svc, timer = sync_mod.systemd_unit_paths(name, str(systemd_dir))
        assert os.path.isfile(svc)
        assert os.path.isfile(timer)

    assert os.path.isfile(os.path.join(str(systemd_dir), sync_mod.ALERT_TEMPLATE_NAME))

    cmds = [c for c, _ in calls]
    assert ["systemctl", "--user", "daemon-reload"] in cmds
    for name in result["written"]:
        assert ["systemctl", "--user", "enable", "--now", f"baza-cron-{name}.timer"] in cmds

    # never any real subprocess call -- everything went through the fake
    assert all(c[0] in ("systemctl", "crontab") for c, _ in calls)


def test_apply_removes_orphaned_units(sync_mod, fixture_yaml_path, tmp_path, monkeypatch):
    declared = _declared(sync_mod, fixture_yaml_path, monkeypatch)
    systemd_dir = tmp_path / "systemd-user"
    systemd_dir.mkdir()
    stray_svc = systemd_dir / "baza-cron-stale_agent_old_task.service"
    stray_timer = systemd_dir / "baza-cron-stale_agent_old_task.timer"
    stray_svc.write_text("stale")
    stray_timer.write_text("stale")

    runner, calls = make_fake_runner()
    result = sync_mod.apply_systemd(
        declared, systemd_dir=str(systemd_dir), runner=runner, today="2026-07-02"
    )

    assert result["removed"] == ["stale_agent_old_task"]
    assert not stray_svc.exists()
    assert not stray_timer.exists()
    cmds = [c for c, _ in calls]
    assert ["systemctl", "--user", "disable", "--now", "baza-cron-stale_agent_old_task.timer"] in cmds


def test_apply_migrates_crontab_managed_lines(sync_mod, fixture_yaml_path, tmp_path, monkeypatch):
    declared = _declared(sync_mod, fixture_yaml_path, monkeypatch)
    systemd_dir = tmp_path / "systemd-user"

    agent_id0, task0 = declared[0]
    name0, schedule0, cmd0 = sync_mod.build_cron_line(agent_id0, task0)
    fake_crontab = (
        "# manual entry - leave me alone\n"
        "0 0 * * * /usr/bin/manual-thing\n"
        "\n"
        f"{sync_mod.MARKER_PREFIX}{name0}\n"
        f"{schedule0} {cmd0}\n"
        "\n"
    )
    runner, calls = make_fake_runner(crontab_out=fake_crontab)

    result = sync_mod.apply_systemd(
        declared, systemd_dir=str(systemd_dir), runner=runner, today="2026-07-02"
    )

    assert result["crontab"]["removed"] == [name0]
    assert result["crontab"]["date"] == "2026-07-02"

    write_calls = [inp for cmd, inp in calls if cmd == ["crontab", "-"]]
    assert len(write_calls) == 1
    new_crontab = write_calls[0]
    assert "manual-thing" in new_crontab  # unmanaged line preserved
    assert sync_mod.MARKER_PREFIX + name0 not in new_crontab  # migrated block gone
    assert "# baza-empire-managed migrated-to-systemd 2026-07-02" in new_crontab


def test_remove_crontab_managed_lines_noop_when_absent(sync_mod):
    runner, calls = make_fake_runner(crontab_out="# manual entry\n0 0 * * * /bin/true\n")
    result = sync_mod.remove_crontab_managed_lines(["not_present"], runner=runner)
    assert result == {"removed": [], "date": None}
    # never issues a write when nothing changed
    assert not any(cmd == ["crontab", "-"] for cmd, _ in calls)


# ── alert template + cron_failure_alert.py wiring ───────────────────────

def test_alert_template_source_exists_and_matches_expected_shape(sync_mod):
    assert os.path.isfile(sync_mod.ALERT_TEMPLATE_SRC)
    text = sync_mod._read_file(sync_mod.ALERT_TEMPLATE_SRC)
    assert "ExecStart=" in text
    assert "cron_failure_alert.py %i" in text
    assert sync_mod.PYTHON_BIN in text


def test_cron_failure_alert_sends_alert(monkeypatch):
    import importlib.util as _ilu

    script_path = os.path.join(REPO_ROOT, "scripts", "cron_failure_alert.py")
    spec = _ilu.spec_from_file_location("cron_failure_alert_under_test", script_path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)

    calls = {}

    def fake_send_alert(cron_name, message, alert_key, renotify_hours=None, **kw):
        calls["cron_name"] = cron_name
        calls["message"] = message
        calls["alert_key"] = alert_key
        calls["renotify_hours"] = renotify_hours
        return True

    monkeypatch.setattr(mod, "send_alert", fake_send_alert)

    rc = mod.main(["baza-cron-claw_batto_infra_health.service"])

    assert rc == 0
    assert calls["cron_name"] == "systemd"
    assert calls["alert_key"] == "unitfail:baza-cron-claw_batto_infra_health.service"
    assert calls["renotify_hours"] == 6
    assert "baza-cron-claw_batto_infra_health.service" in calls["message"]
    assert "journalctl --user -u baza-cron-claw_batto_infra_health.service -n 30" in calls["message"]


def test_cron_failure_alert_requires_unit_arg():
    import importlib.util as _ilu

    script_path = os.path.join(REPO_ROOT, "scripts", "cron_failure_alert.py")
    spec = _ilu.spec_from_file_location("cron_failure_alert_under_test2", script_path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main([]) == 2


# ── CLI smoke test against the real repo (dry-run only, no --apply) ─────

def test_cli_dry_run_systemd_against_real_config(tmp_path):
    """End-to-end smoke: real config/agents.yaml, tmp SYSTEMD_USER_DIR, no
    --apply. Must exit 0 and must not create the tmp systemd dir."""
    import subprocess

    systemd_dir = tmp_path / "systemd-user"
    env = dict(os.environ)
    env["BAZA_SYSTEMD_USER_DIR"] = str(systemd_dir)
    python_bin = os.path.join(REPO_ROOT, "venv", "bin", "python")
    proc = subprocess.run(
        [python_bin, SYNC_SCRIPT_PATH, "--target", "systemd"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not systemd_dir.exists()
