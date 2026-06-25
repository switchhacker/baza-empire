import textwrap
from core import skill_registry as reg

def test_extract_meta_literal(tmp_path):
    p = tmp_path / "demo.py"
    p.write_text(textwrap.dedent('''
        SKILL_META = {"category": "financial", "summary": "Total an invoice.",
                      "when_to_use": "User asks to total an invoice.",
                      "args": {"items": "list"}}
        print("ok")
    '''))
    meta = reg.extract_meta(str(p))
    assert meta["category"] == "financial"
    assert meta["summary"] == "Total an invoice."

def test_extract_meta_absent_returns_none(tmp_path):
    p = tmp_path / "nometa.py"
    p.write_text('"""Just a docstring summary."""\nprint(1)\n')
    assert reg.extract_meta(str(p)) is None

def test_extract_meta_malformed_returns_none(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text('SKILL_META = {"category": some_var}\nprint(1)\n')  # not a literal
    assert reg.extract_meta(str(p)) is None

def test_describe_uses_meta(tmp_path):
    p = tmp_path / "invoice_thing.py"
    p.write_text('SKILL_META = {"category":"financial","summary":"S","when_to_use":"W","args":{}}\n')
    d = reg.describe_skill(str(p), scope="shared")
    assert d["name"] == "invoice_thing"
    assert d["type"] == "skill"
    assert d["category"] == "financial"
    assert d["summary"] == "S"
    assert d["source_path"].endswith("invoice_thing.py")

def test_describe_legacy_fallback(tmp_path):
    p = tmp_path / "invoice_legacy.py"
    p.write_text('"""Calculate invoice totals from line items."""\nprint(1)\n')
    d = reg.describe_skill(str(p), scope="shared")
    assert d["summary"].startswith("Calculate invoice totals")
    assert d["category"] == "financial"        # inferred from "invoice_" prefix

def test_infer_category():
    assert reg.infer_category("invoice_calculator") == "financial"
    assert reg.infer_category("drywall_calculator") == "materials"
    assert reg.infer_category("system_health") == "infrastructure"
    assert reg.infer_category("totally_unknown_xyz") == "general"
