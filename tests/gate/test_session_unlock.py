import subprocess
import pytest
from gate import session_unlock


def test_unlock_runs_loginctl_and_returns_true(monkeypatch):
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        class R: returncode = 0
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert session_unlock.unlock_session() is True
    assert calls["cmd"][0] == "loginctl"
    assert "unlock-sessions" in calls["cmd"]


def test_unlock_returns_false_on_nonzero(monkeypatch):
    def fake_run(cmd, **kw):
        class R: returncode = 1
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert session_unlock.unlock_session() is False


def test_unlock_returns_false_when_binary_missing(monkeypatch):
    def fake_run(cmd, **kw):
        raise FileNotFoundError("loginctl")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert session_unlock.unlock_session() is False


def test_unlock_forwards_timeout(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        class R: returncode = 0
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert session_unlock.unlock_session(timeout=2.0) is True
    assert seen.get("timeout") == 2.0
