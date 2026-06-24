import json, os, sys, subprocess
from pathlib import Path
from PIL import Image

FRAMEWORK = Path(__file__).resolve().parent.parent
SKILL = FRAMEWORK / "skills/shared/before_after_showcase.py"


def run_skill(args, env):
    e = dict(os.environ); e["SKILL_ARGS"] = json.dumps(args); e.update(env)
    p = subprocess.run([sys.executable, str(SKILL)], capture_output=True, text=True, env=e)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_requires_two_photos(tmp_path):
    out = run_skill({"before": "", "after": ""},
                    env={"BAZA_ARTIFACTS_DIR": str(tmp_path / "art"),
                         "OLLAMA_HOST": "http://127.0.0.1:9"})
    assert "error" in out


def test_builds_showcase(tmp_path):
    b = tmp_path / "b.jpg"; a = tmp_path / "a.jpg"
    Image.new("RGB", (1200, 1600), (60, 60, 60)).save(b)
    Image.new("RGB", (1200, 1600), (200, 180, 160)).save(a)
    out = run_skill(
        {"before": str(b), "after": str(a), "title": "Ritz Water Damage",
         "details": "Full remediation", "platforms": ["ig_square"]},
        env={"BAZA_ARTIFACTS_DIR": str(tmp_path / "art"),
             "OLLAMA_HOST": "http://127.0.0.1:9"})
    assert out["skill"] == "before_after_showcase"
    assert len(out["artifacts"]) == 1
    art = out["artifacts"][0]
    assert Path(art["path"]).exists()
    assert Image.open(art["path"]).size == (1080, 1080)


def test_corrupt_photo_does_not_break_contract(tmp_path):
    b = tmp_path / "b.jpg"; a = tmp_path / "a.jpg"
    b.write_bytes(b"not an image")          # passes exists() but unreadable
    Image.new("RGB", (1200, 1600), (200, 180, 160)).save(a)
    out = run_skill(
        {"before": str(b), "after": str(a), "title": "Bad Photo",
         "platforms": ["ig_square"]},
        env={"BAZA_ARTIFACTS_DIR": str(tmp_path / "art"),
             "OLLAMA_HOST": "http://127.0.0.1:9"})
    # contract preserved: valid JSON, no artifacts, a compose-failed warning
    assert out["skill"] == "before_after_showcase"
    assert out["artifacts"] == []
    assert any("compose failed" in w for w in out["warnings"])
