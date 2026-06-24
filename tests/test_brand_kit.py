import importlib.util, json, os, sys, subprocess
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parent.parent
SKILL = FRAMEWORK / "skills/shared/brand_kit.py"


def run_skill(args, env=None):
    e = dict(os.environ)
    e["SKILL_ARGS"] = json.dumps(args)
    if env:
        e.update(env)
    p = subprocess.run([sys.executable, str(SKILL)], capture_output=True, text=True, env=e)
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_show_returns_brand(tmp_path):
    out = run_skill({"mode": "show"})
    assert out["brand"]["short_name"] == "AHBCO"


def test_set_patches_color(tmp_path):
    bp = tmp_path / "brand.json"
    out = run_skill({"mode": "set", "patch": {"colors": {"accent": "#ABCDEF"}}},
                    env={"BAZA_BRAND_PATH": str(bp)})
    assert out["brand"]["colors"]["accent"] == "#ABCDEF"
    assert json.loads(bp.read_text())["colors"]["accent"] == "#ABCDEF"


def test_detect_falls_back_when_site_down(tmp_path):
    bp = tmp_path / "brand.json"
    out = run_skill({"mode": "detect", "site": "http://127.0.0.1:9"},  # nothing listening
                    env={"BAZA_BRAND_PATH": str(bp)})
    assert out["source"] == "fallback"
    assert out["brand"]["short_name"] == "AHBCO"


import re as _re

def test_og_image_regex_both_orders():
    # both attribute orders should be matchable by the detect logic's pattern approach
    html_a = '<meta property="og:image" content="https://x/img1.png">'
    html_b = '<meta content="https://x/img2.png" property="og:image">'
    def extract(html):
        for m in _re.finditer(r'<meta[^>]+>', html, _re.I):
            tag = m.group(0)
            if _re.search(r'(property|name)=["\']og:image["\']', tag, _re.I):
                cm = _re.search(r'content=["\']([^"\']+)["\']', tag, _re.I)
                if cm:
                    return cm.group(1)
        return ""
    assert extract(html_a) == "https://x/img1.png"
    assert extract(html_b) == "https://x/img2.png"
