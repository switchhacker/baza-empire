"""Scaffold wiring checks for task_runner — verify the flag gate and the
_run_scaffold_loop helper without hitting a real Ollama or DB."""
from core import scaffold_config
from core import task_runner


def test_task_runner_respects_flag_off():
    scaffold_config.reload()
    assert scaffold_config.is_enabled("claw_batto") is False


def test_agent_loop_importable_from_task_runner_context():
    from core import agent_loop
    assert hasattr(agent_loop, "run_loop")


class _FakeResp:
    def __init__(self, content): self._c = content
    def raise_for_status(self): pass
    def json(self): return {"message": {"content": self._c}}


class _FakeEngine:
    """Stands in for SkillsEngine; records parse_and_run kwargs."""
    def __init__(self, agent_id): self.agent_id = agent_id; self.calls = []
    def parse_and_run(self, text, **kw):
        self.calls.append(kw)
        return text, []  # no skills ran → loop stops after first step


def test_run_scaffold_loop_returns_final(monkeypatch):
    posted = {}

    def fake_post(url, json=None, timeout=None):
        posted["url"] = url
        posted["messages"] = json["messages"]
        return _FakeResp("FINAL: deliverable ready")

    monkeypatch.setattr(task_runner, "requests",
                        type("R", (), {"post": staticmethod(fake_post),
                                       "exceptions": task_runner.requests.exceptions}))
    monkeypatch.setattr(task_runner, "SkillsEngine", _FakeEngine)

    task = {"id": "abcd1234", "title": "Write report", "description": "do it",
            "project_id": "proj-ahb123"}
    out = task_runner._run_scaffold_loop(
        "phil_hass", task, system="SYS", user_msg="execute",
        model="qwen2.5:14b", target_url="http://localhost:11434")

    assert out == "FINAL: deliverable ready"
    # The selector block was appended to the system prompt sent to Ollama.
    assert "RELEVANT SKILLS" in posted["messages"][0]["content"]
