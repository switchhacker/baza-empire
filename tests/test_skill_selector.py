from core import skill_registry as reg
from core import skill_selector

def _seed(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    (shared / "invoice_calculator.py").write_text(
        'SKILL_META={"category":"financial","summary":"Total an invoice.",'
        '"when_to_use":"total an invoice","args":{"items":"list of line items"}}\n')
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

def test_retrieved_skill_renders_arg_hint(tmp_path):
    # invoice_calculator is found via FTS (not pinned); its args must survive
    # into the rendered call block (FTS rows omit args; selector enriches them).
    json_path, db_path = _seed(tmp_path)
    res = skill_selector.select("total this invoice", agent_id="phil_hass",
                                pinned=[], role_pins=[], top_k=5,
                                json_path=json_path, db_path=db_path)
    inv = next(s for s in res["skills"] if s["name"] == "invoice_calculator")
    assert inv["args"] == {"items": "list of line items"}
    assert "items" in skill_selector.render_block(res)

def test_render_block_tool_entry_uses_call_tool():
    # A type=="tool" descriptor renders a valid call_tool form with JSON args.
    selection = {"skills": [{"name": "sam_axe/generate-image", "type": "tool",
                             "summary": "Generate an image.",
                             "args": {"agent": "sam_axe", "tool": "generate-image",
                                      "input": "dict"}}],
                 "categories": {}}
    block = skill_selector.render_block(selection)
    assert "call_tool" in block
    assert '"agent": "sam_axe"' in block
    assert '"tool": "generate-image"' in block


def test_render_block_web_directive_when_web_skill_present():
    sel = {"skills": [{"name": "web_search", "type": "skill", "summary": "s"}],
           "categories": {}}
    block = skill_selector.render_block(sel)
    assert "WEB RESEARCH" in block and "web_search" in block


def test_render_block_no_web_directive_without_web_skill():
    sel = {"skills": [{"name": "artifact_save", "type": "skill", "summary": "s"}],
           "categories": {}}
    assert "WEB RESEARCH" not in skill_selector.render_block(sel)
