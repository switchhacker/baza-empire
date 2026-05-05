"""Tests for the iterate-until-complete loop in core/task_runner.py."""
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)


def _fake_task():
    return {"id": "t1", "project_id": "p1", "title": "Do thing",
            "description": "do all the things", "priority": "med"}


def _agent_cfg():
    return {"model": "qwen2.5:14b", "name": "Test Agent", "system_prompt": "You are Test."}


@pytest.fixture(autouse=True)
def _clean_modules():
    for m in list(sys.modules):
        if m.startswith("core."):
            del sys.modules[m]
    yield


def test_run_with_prior_output_uses_continuation_prompt():
    from core import task_runner as tr
    captured = {}

    def fake_post(url, json=None, timeout=0):
        captured["payload"] = json
        m = MagicMock()
        m.json.return_value = {"message": {"content": "all done.\nTASK_COMPLETE"}}
        m.raise_for_status.return_value = None
        return m

    with patch.object(tr, "requests") as mock_req:
        mock_req.post = fake_post
        mock_req.exceptions.ReadTimeout = Exception  # avoid attribute lookups
        out = tr.run_task_with_llm("a1", _agent_cfg(), _fake_task(),
                                   prior_output="part 1: ok\nTASK_IN_PROGRESS")
    assert out["success"] is True
    assert out["completed"] is True
    user_msg = captured["payload"]["messages"][-1]["content"]
    assert "PRIOR OUTPUT" in user_msg
    assert "part 1" in user_msg


def test_run_without_prior_output_uses_first_pass_prompt():
    from core import task_runner as tr

    def fake_post(url, json=None, timeout=0):
        fake_post.last = json
        m = MagicMock()
        m.json.return_value = {"message": {"content": "starting...\nTASK_IN_PROGRESS"}}
        m.raise_for_status.return_value = None
        return m

    with patch.object(tr, "requests") as mock_req:
        mock_req.post = fake_post
        mock_req.exceptions.ReadTimeout = Exception
        out = tr.run_task_with_llm("a1", _agent_cfg(), _fake_task())

    assert out["in_progress"] is True
    assert out["completed"] is False
    user_msg = fake_post.last["messages"][-1]["content"]
    assert "PRIOR OUTPUT" not in user_msg
    assert "Execute this task now" in user_msg


def test_max_iterations_env_default():
    from core import task_runner as tr
    assert tr.MAX_TASK_ITERATIONS >= 1


def test_iteration_loop_caps_at_max_iterations(monkeypatch):
    """Verify run_agent_tasks stops looping after MAX_TASK_ITERATIONS even
    when LLM keeps returning TASK_IN_PROGRESS."""
    monkeypatch.setenv("BAZA_MAX_TASK_ITERATIONS", "3")
    for m in list(sys.modules):
        if m.startswith("core."):
            del sys.modules[m]
    from core import task_runner as tr

    call_count = {"n": 0}

    def fake_run_with_llm(agent_id, agent_cfg, task, prior_output=""):
        call_count["n"] += 1
        return {"success": True, "output": f"step {call_count['n']}",
                "completed": False, "in_progress": True, "blocked": False,
                "block_reason": ""}

    monkeypatch.setattr(tr, "run_task_with_llm", fake_run_with_llm)
    monkeypatch.setattr(tr, "wait_for_ollama", lambda max_wait=0: True)
    monkeypatch.setattr(tr, "is_llm_actionable", lambda t: True)
    monkeypatch.setattr(tr, "get_my_tasks", lambda agent_id, status: (
        [_fake_task()] if status == "pending" else []))
    monkeypatch.setattr(tr, "start_task", lambda task_id, **kw: None)
    monkeypatch.setattr(tr, "update_task", lambda task_id, fields: None)
    monkeypatch.setattr(tr, "complete_task", lambda task_id, notes="": None)
    monkeypatch.setattr(tr, "_save_artifact", lambda *a, **k: None)
    monkeypatch.setattr(tr, "_execute_skill_saves", lambda *a, **k: 0)
    monkeypatch.setattr(tr, "process_dispatch_lines", lambda *a, **k: [])
    monkeypatch.setattr(tr, "check_dependencies", lambda t: True)
    monkeypatch.setattr(tr.time, "sleep", lambda s: None)
    monkeypatch.setattr(tr, "_emit", lambda *a, **k: None)

    results = tr.run_agent_tasks("a1", _agent_cfg())
    assert call_count["n"] == 3  # capped
    assert results[0]["status"] == "in_progress"


def test_iteration_loop_stops_on_completion(monkeypatch):
    monkeypatch.setenv("BAZA_MAX_TASK_ITERATIONS", "5")
    for m in list(sys.modules):
        if m.startswith("core."):
            del sys.modules[m]
    from core import task_runner as tr

    call_count = {"n": 0}

    def fake_run_with_llm(agent_id, agent_cfg, task, prior_output=""):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            return {"success": True, "output": "done", "completed": True,
                    "in_progress": False, "blocked": False, "block_reason": ""}
        return {"success": True, "output": "step 1", "completed": False,
                "in_progress": True, "blocked": False, "block_reason": ""}

    monkeypatch.setattr(tr, "run_task_with_llm", fake_run_with_llm)
    monkeypatch.setattr(tr, "wait_for_ollama", lambda max_wait=0: True)
    monkeypatch.setattr(tr, "is_llm_actionable", lambda t: True)
    monkeypatch.setattr(tr, "get_my_tasks", lambda agent_id, status: (
        [_fake_task()] if status == "pending" else []))
    monkeypatch.setattr(tr, "start_task", lambda task_id, **kw: None)
    monkeypatch.setattr(tr, "update_task", lambda task_id, fields: None)
    monkeypatch.setattr(tr, "complete_task", lambda task_id, notes="": None)
    monkeypatch.setattr(tr, "_save_artifact", lambda *a, **k: None)
    monkeypatch.setattr(tr, "_execute_skill_saves", lambda *a, **k: 0)
    monkeypatch.setattr(tr, "process_dispatch_lines", lambda *a, **k: [])
    monkeypatch.setattr(tr, "check_dependencies", lambda t: True)
    monkeypatch.setattr(tr.time, "sleep", lambda s: None)
    monkeypatch.setattr(tr, "_emit", lambda *a, **k: None)

    results = tr.run_agent_tasks("a1", _agent_cfg())
    assert call_count["n"] == 2  # stopped on completion at iter 2
    assert results[0]["status"] == "completed"
