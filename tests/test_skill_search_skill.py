import json, os, subprocess, sys
from core import skill_registry as reg

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def test_skill_search_outputs_matches(tmp_path, monkeypatch):
    shared = tmp_path / "shared"; shared.mkdir()
    (shared / "invoice_calculator.py").write_text(
        'SKILL_META={"category":"financial","summary":"Total an invoice.",'
        '"when_to_use":"total an invoice","args":{}}\n')
    json_path = tmp_path / "m.json"; db_path = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(json_path), out_db=str(db_path), tools=None)
    env = dict(os.environ)
    env["SKILL_ARGS"] = json.dumps({"query": "invoice total"})
    env["SKILL_MANIFEST_DB"] = str(db_path)
    env["SKILL_MANIFEST_JSON"] = str(json_path)
    out = subprocess.run([sys.executable, os.path.join(FRAMEWORK, "skills", "shared", "skill_search.py")],
                         capture_output=True, text=True, env=env, timeout=30)
    assert out.returncode == 0
    assert "invoice_calculator" in out.stdout
