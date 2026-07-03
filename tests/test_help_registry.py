import json, os, re, glob

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(REPO_ROOT, "dashboard")
REG = os.path.join(DASH, "static", "help_content.json")


def _registry():
    with open(REG) as f:
        return json.load(f)


def test_schema_and_min_steps():
    reg = _registry()
    assert reg, "registry must not be empty"
    for key, entry in reg.items():
        assert re.fullmatch(r"[a-z0-9_.\-]+", key), key
        assert entry["title"].strip()
        assert isinstance(entry["steps"], list) and len(entry["steps"]) >= 2, \
            f"{key}: hover-help is for 2+ step workflows only"
        assert all(isinstance(s, str) and s.strip() for s in entry["steps"])


def test_every_template_key_exists():
    reg = _registry()
    used = set()
    for tpl in glob.glob(os.path.join(DASH, "templates", "*.html")):
        used.update(re.findall(r'data-help="([^"]+)"', open(tpl).read()))
    missing = used - set(reg)
    assert not missing, f"data-help keys missing from registry: {missing}"


def test_nav_includes_assets():
    nav = open(os.path.join(DASH, "templates", "_nav.html")).read()
    assert "help.js" in nav and "help.css" in nav
