from core import skill_registry as reg

def test_tool_descriptors_from_registry_dict():
    tools = {
        "claw_batto": ["run-command", "disk-usage"],
        "sam_axe": ["generate-image"],
    }
    descs = reg.tool_descriptors(tools)
    names = {d["name"] for d in descs}
    assert "claw_batto/run-command" in names
    sam = next(d for d in descs if d["name"] == "sam_axe/generate-image")
    assert sam["type"] == "tool"
    assert sam["args"]["agent"] == "sam_axe"
    assert sam["args"]["tool"] == "generate-image"

def test_tool_descriptors_none_input():
    assert reg.tool_descriptors(None) == []

def test_build_includes_tools(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    (shared / "noop.py").write_text('"""noop."""\n')
    db = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(tmp_path / "m.json"), out_db=str(db),
              tools={"sam_axe": ["generate-image"]})
    hits = reg.search("generate image", db_path=str(db), top_k=5)
    assert any(h["type"] == "tool" and "generate-image" in h["name"] for h in hits)
