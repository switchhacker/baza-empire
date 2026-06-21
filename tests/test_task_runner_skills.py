"""Tests for skill execution inside the autonomous task runner.

Regression guard for the 'simulated research' bug: the task runner emitted
##SKILL:web_search## markers but never executed them, so the LLM hallucinated
("simulated") results instead of using real search data.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _clean_modules():
    for m in list(sys.modules):
        if m.startswith("core."):
            del sys.modules[m]
    yield


def _task():
    return {"id": "t1", "project_id": "p1",
            "title": "Research latest industry trends",
            "description": "research competitor activity", "priority": "high"}


def _cfg():
    return {"model": "qwen2.5:14b", "name": "Claw", "system_prompt": "You are Claw."}


def test_skill_markers_are_executed_not_simulated(monkeypatch):
    """A ##SKILL:web_search## call in pass-1 output must actually run, then a
    pass-2 reformat must ground the deliverable in the real skill data."""
    from core import task_runner as tr

    responses = [
        # pass 1: agent asks for a search (no completion yet)
        'Researching trends...\n##SKILL:web_search{"query":"AI agent frameworks 2026"}##',
        # pass 2: agent writes the real report using returned data
        "Trends report grounded in real search data.\nTASK_COMPLETE",
    ]
    calls = {"n": 0, "payloads": []}

    def fake_post(url, json=None, timeout=0):
        i = calls["n"]
        calls["n"] += 1
        calls["payloads"].append(json)
        m = MagicMock()
        m.json.return_value = {"message": {"content": responses[min(i, len(responses) - 1)]}}
        m.raise_for_status.return_value = None
        return m

    # Execute the skill deterministically — no network.
    def fake_run(self, skill_name, args=None, **kw):
        assert skill_name == "web_search"
        return {"success": True, "skill": "web_search",
                "output": "1. Real result\n   https://example.com\n   real snippet",
                "duration_ms": 5}

    monkeypatch.setattr(tr.SkillsEngine, "run", fake_run)

    with patch.object(tr, "requests") as mock_req:
        mock_req.post = fake_post
        mock_req.exceptions.ReadTimeout = Exception
        out = tr.run_task_with_llm("claw_batto", _cfg(), _task())

    # The skill ran -> two LLM round-trips (pass 1 + reformat pass 2)
    assert calls["n"] == 2
    # Pass-2 prompt carried the REAL skill data
    pass2_user = calls["payloads"][1]["messages"][-1]["content"]
    assert "Real result" in pass2_user
    # Final deliverable is the grounded report, completion detected
    assert out["success"] is True
    assert out["completed"] is True
    # No raw, unexecuted skill marker leaked into the deliverable
    assert "##SKILL:web_search" not in out["output"]


def test_no_skill_markers_means_single_pass(monkeypatch):
    """Tasks with no skill calls must not trigger a spurious second LLM call."""
    from core import task_runner as tr

    calls = {"n": 0}

    def fake_post(url, json=None, timeout=0):
        calls["n"] += 1
        m = MagicMock()
        m.json.return_value = {"message": {"content": "Plain deliverable.\nTASK_COMPLETE"}}
        m.raise_for_status.return_value = None
        return m

    with patch.object(tr, "requests") as mock_req:
        mock_req.post = fake_post
        mock_req.exceptions.ReadTimeout = Exception
        out = tr.run_task_with_llm("claw_batto", _cfg(), _task())

    assert calls["n"] == 1
    assert out["completed"] is True


def test_execute_skill_saves_tolerates_literal_newlines(monkeypatch):
    """LLMs emit literal newlines inside artifact_save JSON content; the saver
    must parse it (strict JSON would reject control chars) and store CLEAN
    markdown, not the raw ##SKILL:## wrapper."""
    from core import task_runner as tr
    import skills.shared.save_artifact as sa

    captured = {}

    def fake_save(filename, content, project_id, agent_id, **kw):
        captured["content"] = content
        captured["filename"] = filename
        return {"success": True, "path": "x/" + filename}

    monkeypatch.setattr(sa, "save_artifact", fake_save)
    monkeypatch.setattr(tr, "_task_events", None)

    out = ('##SKILL:artifact_save{"filename":"r.md",'
           '"content":"# Title\n\nLine two\nLine three",'
           '"project_id":"p1"}##')
    n = tr._execute_skill_saves("claw_batto", out)

    assert n == 1
    assert captured["content"] == "# Title\n\nLine two\nLine three"
    assert "##SKILL" not in captured["content"]


def test_execute_skill_saves_recovers_from_unescaped_quotes(monkeypatch):
    """LLMs emit unescaped double-quotes inside artifact_save content (e.g. a
    quoted product name). json.loads can't parse that at all, so the saver must
    fall back to a tolerant extractor and still store the real markdown."""
    from core import task_runner as tr
    import skills.shared.save_artifact as sa

    captured = {}

    def fake_save(filename, content, project_id, agent_id, **kw):
        captured.update(filename=filename, content=content, project_id=project_id)
        return {"success": True, "path": "x/" + filename}

    monkeypatch.setattr(sa, "save_artifact", fake_save)
    monkeypatch.setattr(tr, "_task_events", None)

    out = ('##SKILL:artifact_save{"filename":"r.md","content":'
           '"# Title\n\nGartner has an "Agentic AI Roadmap," per analysts.\nDone.",'
           '"project_id":"p1"}##')
    n = tr._execute_skill_saves("claw_batto", out)

    assert n == 1
    assert "##SKILL" not in captured["content"]
    assert captured["content"].startswith("# Title")
    assert "Agentic AI Roadmap" in captured["content"]
    assert captured["content"].rstrip().endswith("Done.")
    assert captured["filename"] == "r.md"
    assert captured["project_id"] == "p1"


def test_parse_and_run_excludes_named_skills():
    """Excluded skills are left verbatim for downstream handling, not executed."""
    from core.skills_engine import SkillsEngine

    eng = SkillsEngine("claw_batto")
    text = 'note ##SKILL:artifact_save{"filename":"a.md","content":"x"}## end'
    out, results = eng.parse_and_run(text, exclude={"artifact_save"})

    assert "##SKILL:artifact_save" in out  # marker preserved
    assert results == []                    # nothing executed
