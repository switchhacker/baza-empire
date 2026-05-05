"""Tests for skills/shared/ahb_api.py — agent AHB toolbelt skill."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SKILL = os.path.join(ROOT, "skills", "shared", "ahb_api.py")
PY = sys.executable


def _run(args: dict, env_extra: dict | None = None) -> tuple[int, dict]:
    env = os.environ.copy()
    env["SKILL_ARGS"] = json.dumps(args)
    env["AGENT_ID"] = "test_agent"
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([PY, SKILL], capture_output=True, text=True, env=env, timeout=15)
    try:
        out = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        out = {"raw": r.stdout, "stderr": r.stderr}
    return r.returncode, out


def test_help_lists_actions():
    code, out = _run({"action": "help"})
    assert code == 0
    assert out["ok"] is True
    assert "clients_list" in out["actions"]
    assert "blueprints_render" in out["actions"]
    assert "raw" in out["actions"]


def test_missing_required_args():
    code, out = _run({"action": "clients_create", "args": {}})
    assert code == 1
    assert out["ok"] is False
    assert "missing" in out["error"].lower()


def test_unknown_action():
    code, out = _run({"action": "not_a_real_action"})
    assert code == 1
    assert "unknown action" in out["error"]


def test_privileged_blocks_without_approval():
    code, out = _run({"action": "clients_delete", "args": {"id": "abc"}})
    assert code == 3
    assert out["approval_required"] is True


def test_raw_path_must_start_with_api():
    code, out = _run({"action": "raw", "args": {"method": "GET", "path": "/something-bad"}})
    assert code == 1
    assert "/api/" in out["error"]


def test_raw_succeeds_against_test_endpoint(monkeypatch):
    """If localhost:8888 isn't reachable, the skill returns ok=False with an error
    instead of crashing — that's the desired behavior."""
    code, out = _run(
        {"action": "raw", "args": {"method": "GET", "path": "/api/ahb/clients"}},
        env_extra={"BAZA_DASHBOARD_URL": "http://localhost:1"},
    )
    # Connection refused → ok=False
    assert out["ok"] is False
    assert code == 2
