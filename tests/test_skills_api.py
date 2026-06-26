import os
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Redirect all skill I/O into a temp framework tree."""
    (tmp_path / "skills" / "shared").mkdir(parents=True)
    (tmp_path / "agents" / "simon_bately" / "skills").mkdir(parents=True)
    (tmp_path / "dashboard").mkdir(parents=True)
    import dashboard.app as appmod
    monkeypatch.setattr(appmod, "FRAMEWORK_DIR", str(tmp_path))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_list_includes_category_and_summary(client, tmp_path):
    _write(tmp_path, "skills/shared/make_quote.py",
           '"""Create a PDF quote"""\n'
           "SKILL_META = {'category': 'financial', 'summary': 'Create a PDF quote', 'when_to_use': 'on request', 'args': {}}\n"
           "print('x')\n")
    r = client.get("/api/skills/list")
    assert r.status_code == 200
    rows = r.get_json()
    row = next(x for x in rows if x["name"] == "make_quote")
    assert row["scope"] == "shared"
    assert row["category"] == "financial"
    assert row["summary"] == "Create a PDF quote"
    assert row["when_to_use"] == "on request"


def test_list_includes_per_agent_scope(client, tmp_path):
    _write(tmp_path, "agents/simon_bately/skills/ping.py",
           '"""Ping something"""\nprint("pong")\n')
    rows = client.get("/api/skills/list").get_json()
    row = next(x for x in rows if x["name"] == "ping")
    assert row["scope"] == "simon_bately"
    assert row["category"]  # inferred, non-empty
