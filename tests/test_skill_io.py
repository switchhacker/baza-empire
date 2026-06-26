import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.skill_io import compose_skill_source
from core import skill_registry


def _write_and_extract(tmp_path, source):
    p = tmp_path / "s.py"
    p.write_text(source)
    return skill_registry.extract_meta(str(p))


def test_compose_roundtrips_via_extract_meta(tmp_path):
    src = compose_skill_source(
        summary="Create a PDF quote",
        when_to_use="When a client asks for a quote",
        category="financial",
        args={"client_id": "the AHB project id", "amount": "dollar total"},
        body_source="import os, json\nargs = json.loads(os.environ.get('SKILL_ARGS','{}'))\nprint('ok')\n",
    )
    ast.parse(src)
    meta = _write_and_extract(tmp_path, src)
    assert meta["category"] == "financial"
    assert meta["summary"] == "Create a PDF quote"
    assert meta["when_to_use"] == "When a client asks for a quote"
    assert meta["args"] == {"client_id": "the AHB project id", "amount": "dollar total"}


def test_compose_preserves_code_body_verbatim():
    body = "import os, json\nargs = json.loads(os.environ.get('SKILL_ARGS','{}'))\nresult = args.get('x', 1) * 2\nprint(result)\n"
    src = compose_skill_source("s", "w", "general", {}, body)
    for line in ("result = args.get('x', 1) * 2", "print(result)"):
        assert line in src


def test_compose_strips_old_header_no_duplicate_meta():
    existing = (
        "#!/usr/bin/env python3\n"
        '"""old summary"""\n\n'
        "SKILL_META = {'category': 'general', 'summary': 'old summary', 'when_to_use': '', 'args': {}}\n\n"
        "print('body')\n"
    )
    src = compose_skill_source("new summary", "now", "code", {}, existing)
    assert src.count("SKILL_META =") == 1
    assert "old summary" not in src
    assert "new summary" in src
    assert "print('body')" in src


def test_compose_inserts_meta_without_clobbering_imports():
    existing = (
        "#!/usr/bin/env python3\n"
        "import os, json\n"
        "SKILL_META = {'category': 'general', 'summary': 'x', 'when_to_use': '', 'args': {}}\n"
        "args = json.loads(os.environ.get('SKILL_ARGS','{}'))\n"
        "print('keep me')\n"
    )
    src = compose_skill_source("s", "w", "data", {}, existing)
    assert src.count("SKILL_META =") == 1
    assert "import os, json" in src
    assert "print('keep me')" in src


def test_compose_survives_syntax_error_body():
    src = compose_skill_source("s", "w", "general", {}, "def broken(:\n  pass\n")
    assert "SKILL_META =" in src
    assert "def broken(:" in src
