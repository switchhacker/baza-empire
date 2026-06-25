from core import skill_registry as reg
from core import skill_selector

def _seed(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    (shared / "invoice_calculator.py").write_text(
        'SKILL_META={"category":"financial","summary":"Total an invoice.",'
        '"when_to_use":"total an invoice","args":{}}\n')
    (shared / "drywall_calculator.py").write_text('"""Estimate drywall sheets."""\n')
    (shared / "artifact_save.py").write_text('"""Save an artifact file."""\n')
    json_path = tmp_path / "m.json"; db_path = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(json_path), out_db=str(db_path), tools=None)
    return str(json_path), str(db_path)

def test_select_includes_retrieved_and_pinned(tmp_path):
    json_path, db_path = _seed(tmp_path)
    res = skill_selector.select(
        "please total this invoice", agent_id="phil_hass",
        pinned=["artifact_save"], role_pins=[], top_k=5,
        json_path=json_path, db_path=db_path)
    names = {s["name"] for s in res["skills"]}
    assert "invoice_calculator" in names          # retrieved
    assert "artifact_save" in names                # pinned always present
    assert "financial" in res["categories"]

def test_select_empty_query_still_returns_pinned(tmp_path):
    json_path, db_path = _seed(tmp_path)
    res = skill_selector.select(
        "", agent_id="phil_hass", pinned=["artifact_save"], role_pins=["drywall_calculator"],
        top_k=5, json_path=json_path, db_path=db_path)
    names = {s["name"] for s in res["skills"]}
    assert "artifact_save" in names and "drywall_calculator" in names

def test_render_block_is_compact_text(tmp_path):
    json_path, db_path = _seed(tmp_path)
    res = skill_selector.select("invoice", agent_id="phil_hass", pinned=["artifact_save"],
                                role_pins=[], top_k=5, json_path=json_path, db_path=db_path)
    block = skill_selector.render_block(res)
    assert "RELEVANT SKILLS" in block
    assert "invoice_calculator" in block
    assert "skill_search" in block                 # nudge to self-discover
