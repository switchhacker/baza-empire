"""Tests for skills/shared/baza_proj.py — agent project access skill."""
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SKILL = os.path.join(ROOT, "skills", "shared", "baza_proj.py")
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
    assert "create" in out["actions"]
    assert "file_write" in out["actions"]
    assert "run" in out["actions"]
    assert "raw" in out["actions"]


def test_missing_required_args_create():
    code, out = _run({"action": "create", "args": {}})
    assert code == 1
    assert "missing" in out["error"].lower()


def test_unknown_action():
    code, out = _run({"action": "not_real"})
    assert code == 1
    assert "unknown action" in out["error"]


def test_delete_blocked_without_approval():
    code, out = _run({"action": "delete", "args": {"id": "abc"}})
    assert code == 3
    assert out["approval_required"] is True


def test_run_deploy_slot_blocked_without_approval():
    code, out = _run({"action": "run", "args": {"id": "abc", "slot": "deploy"}})
    assert code == 3
    assert out["approval_required"] is True


def test_run_test_slot_does_not_require_approval():
    """Should attempt the HTTP call. With unreachable dashboard it'll fail
    cleanly with ok=False, but should NOT be blocked by the approval gate."""
    code, out = _run(
        {"action": "run", "args": {"id": "no-such", "slot": "test"}},
        env_extra={"BAZA_DASHBOARD_URL": "http://localhost:1"},
    )
    assert out.get("approval_required") is None or out.get("approval_required") is False
    assert out["ok"] is False


def test_raw_path_must_start_with_api_baza():
    code, out = _run({"action": "raw", "args": {"method": "GET", "path": "/api/something-else"}})
    assert code == 1
    assert "/api/baza/" in out["error"]


def test_file_read_uses_query_string():
    """Even when dashboard is unreachable, the skill should not crash. We're
    primarily checking the args validation path here."""
    code, out = _run(
        {"action": "file_read", "args": {"id": "x", "path": "README.md"}},
        env_extra={"BAZA_DASHBOARD_URL": "http://localhost:1"},
    )
    assert out["ok"] is False  # connection refused, but no validation error
