import json, os, sys, subprocess
from pathlib import Path
from PIL import Image

FRAMEWORK = Path(__file__).resolve().parent.parent
SKILL = FRAMEWORK / "skills/shared/marketing_flyer.py"


def run_skill(args, env):
    e = dict(os.environ); e["SKILL_ARGS"] = json.dumps(args); e.update(env)
    p = subprocess.run([sys.executable, str(SKILL)], capture_output=True, text=True, env=e)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_flyer_requires_offer_or_headline(tmp_path):
    out = run_skill({}, env={"BAZA_ARTIFACTS_DIR": str(tmp_path / "art"),
                             "OLLAMA_HOST": "http://127.0.0.1:9"})
    assert "error" in out


def test_flyer_renders_sizes(tmp_path):
    out = run_skill(
        {"headline": "Spring Roofing Special", "subhead": "20% off this month",
         "bullets": ["Licensed & insured", "Free estimates", "10-year warranty"],
         "cta": "Call (555) 123-4567", "sizes": ["flyer_portrait", "ad_square"]},
        env={"BAZA_ARTIFACTS_DIR": str(tmp_path / "art"),
             "OLLAMA_HOST": "http://127.0.0.1:9",
             "BAZA_TOOL_SERVER": "http://127.0.0.1:9"})  # no SD -> brand-color bg
    assert out["skill"] == "marketing_flyer"
    assert len(out["artifacts"]) == 2
    sizes = {Path(a["path"]).name: Image.open(a["path"]).size for a in out["artifacts"]}
    assert (1275, 1650) in sizes.values()
    assert (1080, 1080) in sizes.values()
