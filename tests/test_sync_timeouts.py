"""Tests for scripts/sync-agent-crons.py's crontab line builder — Task 9:
`timeout` wrapping + `.sh` -> `bash` support.

Runs against a FIXTURE agents.yaml (never the real config/agents.yaml) by
monkeypatching the module's AGENTS_YAML constant before calling
load_declared_tasks(); load_declared_tasks()/build_cron_line() themselves are
untouched aside from build_cron_line()'s new timeout/interpreter logic.
"""
import importlib.util
import os
import sys
import textwrap

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC_SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "sync-agent-crons.py")


def _load_sync_module():
    # Hyphenated filename -> can't `import`, must load by path.
    spec = importlib.util.spec_from_file_location("sync_agent_crons_under_test", SYNC_SCRIPT_PATH)
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
          - name: python_task
            schedule: "*/30 * * * *"
            script: agents/fixture_agent/crons/python_task.py
            log: logs/fixture_python_task.log
            enabled: true
          - name: custom_timeout_task
            schedule: "0 3 * * *"
            script: agents/fixture_agent/crons/slow_task.py
            log: logs/fixture_slow_task.log
            timeout_min: 90
            enabled: true
          - name: shell_task
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


def _declared_by_name(sync_mod, fixture_yaml_path, monkeypatch):
    monkeypatch.setattr(sync_mod, "AGENTS_YAML", fixture_yaml_path)
    declared = sync_mod.load_declared_tasks()
    return {task["name"]: (agent_id, task) for agent_id, task in declared}


def test_sync_line_contains_timeout(sync_mod, fixture_yaml_path, monkeypatch):
    by_name = _declared_by_name(sync_mod, fixture_yaml_path, monkeypatch)

    agent_id, task = by_name["python_task"]
    name, schedule, cmd = sync_mod.build_cron_line(agent_id, task)
    assert "timeout 30m" in cmd  # default timeout_min
    assert sync_mod.PYTHON_BIN in cmd
    # timeout must wrap the interpreter invocation, after `cd ... &&`.
    assert cmd.index("cd ") < cmd.index("timeout 30m") < cmd.index(sync_mod.PYTHON_BIN)

    agent_id2, task2 = by_name["custom_timeout_task"]
    _, _, cmd2 = sync_mod.build_cron_line(agent_id2, task2)
    assert "timeout 90m" in cmd2
    assert "timeout 30m" not in cmd2


def test_sh_script_uses_bash(sync_mod, fixture_yaml_path, monkeypatch):
    by_name = _declared_by_name(sync_mod, fixture_yaml_path, monkeypatch)

    agent_id, task = by_name["shell_task"]
    name, schedule, cmd = sync_mod.build_cron_line(agent_id, task)
    assert "bash " in cmd
    assert sync_mod.PYTHON_BIN not in cmd
    assert cmd.endswith("rotate_logs.sh >> " + os.path.join(sync_mod.REPO_ROOT, "logs", "fixture_rotate.log") + " 2>&1")
    assert "timeout 30m" in cmd


def test_disabled_task_not_declared(sync_mod, fixture_yaml_path, monkeypatch):
    by_name = _declared_by_name(sync_mod, fixture_yaml_path, monkeypatch)
    assert "disabled_task" not in by_name


def test_python_task_still_uses_python_bin(sync_mod, fixture_yaml_path, monkeypatch):
    by_name = _declared_by_name(sync_mod, fixture_yaml_path, monkeypatch)
    agent_id, task = by_name["python_task"]
    _, _, cmd = sync_mod.build_cron_line(agent_id, task)
    assert f"{sync_mod.PYTHON_BIN} " in cmd
    assert cmd.strip().endswith("2>&1")
