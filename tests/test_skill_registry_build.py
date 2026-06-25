import textwrap
from core import skill_registry as reg

def _make_skill(d, name, body):
    (d / f"{name}.py").write_text(body)

def test_build_and_search(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    _make_skill(shared, "invoice_calculator",
                'SKILL_META={"category":"financial","summary":"Total an invoice from line items.",'
                '"when_to_use":"total or price an invoice","args":{}}\n')
    _make_skill(shared, "drywall_calculator",
                '"""Estimate drywall sheets for a room."""\n')
    json_path = tmp_path / "manifest.json"
    db_path = tmp_path / "manifest.db"
    n = reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "noagents"),
                  out_json=str(json_path), out_db=str(db_path), tools=None)
    assert n == 2
    hits = reg.search("invoice total", db_path=str(db_path), top_k=5)
    assert any(h["name"] == "invoice_calculator" for h in hits)
    cats = reg.categories(json_path=str(json_path))
    assert cats.get("financial", 0) >= 1
    assert cats.get("materials", 0) >= 1

def test_search_sanitizes_query(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    _make_skill(shared, "system_health", '"""Report CPU and memory health."""\n')
    db_path = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(tmp_path / "m.json"), out_db=str(db_path), tools=None)
    hits = reg.search('cpu health!! "AND"', db_path=str(db_path), top_k=5)
    assert isinstance(hits, list)

def test_get_returns_descriptor(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    _make_skill(shared, "foo_bar", '"""Does foo."""\n')
    json_path = tmp_path / "m.json"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(json_path), out_db=str(tmp_path / "m.db"), tools=None)
    d = reg.get("foo_bar", json_path=str(json_path))
    assert d and d["summary"].startswith("Does foo")
