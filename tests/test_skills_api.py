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


def test_read_shared_returns_metadata_fields(client, tmp_path):
    _write(tmp_path, "skills/shared/make_quote.py",
           '"""Create a PDF quote"""\n'
           "SKILL_META = {'category': 'financial', 'summary': 'Create a PDF quote', 'when_to_use': 'on request', 'args': {'amount': 'total'}}\n"
           "print('x')\n")
    r = client.get("/api/skills/read/shared/make_quote")
    assert r.status_code == 200
    data = r.get_json()
    assert data["name"] == "make_quote"
    assert data["scope"] == "shared"
    assert data["category"] == "financial"
    assert data["summary"] == "Create a PDF quote"
    assert data["when_to_use"] == "on request"
    assert data["args"] == {"amount": "total"}
    assert "SKILL_META" in data["code"]


def test_read_per_agent(client, tmp_path):
    _write(tmp_path, "agents/simon_bately/skills/ping.py", '"""Ping"""\nprint("pong")\n')
    data = client.get("/api/skills/read/simon_bately/ping").get_json()
    assert data["scope"] == "simon_bately"
    assert "pong" in data["code"]


def test_read_rejects_path_traversal_scope(client, tmp_path):
    r = client.get("/api/skills/read/..%2f..%2fetc/passwd")
    assert r.status_code in (400, 404)


from core import skill_registry


def test_save_shared_writes_parseable_meta(client, tmp_path):
    r = client.post("/api/skills/save", json={
        "scope": "shared", "name": "make_quote",
        "summary": "Create a PDF quote", "when_to_use": "on request",
        "category": "financial", "args": {"amount": "dollar total"},
        "code": "import os, json\nargs = json.loads(os.environ.get('SKILL_ARGS','{}'))\nprint('hi')\n",
    })
    assert r.status_code == 200, r.get_json()
    path = tmp_path / "skills" / "shared" / "make_quote.py"
    assert path.exists()
    meta = skill_registry.extract_meta(str(path))
    assert meta["category"] == "financial"
    assert meta["summary"] == "Create a PDF quote"
    assert meta["args"] == {"amount": "dollar total"}
    assert "print('hi')" in path.read_text()


def test_save_per_agent_lands_in_agent_dir(client, tmp_path):
    r = client.post("/api/skills/save", json={
        "scope": "simon_bately", "name": "ping",
        "summary": "Ping", "when_to_use": "", "category": "general",
        "args": {}, "code": "print('pong')\n",
    })
    assert r.status_code == 200, r.get_json()
    assert (tmp_path / "agents" / "simon_bately" / "skills" / "ping.py").exists()


def test_save_rejects_unknown_agent_scope(client):
    r = client.post("/api/skills/save", json={
        "scope": "nope_agent", "name": "x", "summary": "s",
        "when_to_use": "", "category": "general", "args": {}, "code": "print(1)\n",
    })
    assert r.status_code == 400


def test_save_rebuilds_manifest(client, tmp_path):
    client.post("/api/skills/save", json={
        "scope": "shared", "name": "make_quote", "summary": "q",
        "when_to_use": "", "category": "financial", "args": {}, "code": "print(1)\n",
    })
    assert (tmp_path / "dashboard" / "skills_manifest.json").exists()


def test_save_survives_manifest_failure(client, tmp_path, monkeypatch):
    import dashboard.app as appmod
    monkeypatch.setattr(appmod, "_rebuild_skill_manifest", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.post("/api/skills/save", json={
        "scope": "shared", "name": "make_quote", "summary": "q",
        "when_to_use": "", "category": "financial", "args": {}, "code": "print(1)\n",
    })
    assert r.status_code == 200
