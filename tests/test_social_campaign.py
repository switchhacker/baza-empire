import json, os, sys, subprocess, sqlite3
from pathlib import Path
from PIL import Image

FRAMEWORK = Path(__file__).resolve().parent.parent
SKILL = FRAMEWORK / "skills/shared/social_campaign.py"


def _make_db(tmp_path):
    db = tmp_path / "baza_projects.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE ahb_social_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, preset_id INTEGER, project_id INTEGER,
        source_media_ids TEXT NOT NULL DEFAULT '[]', platform TEXT NOT NULL,
        variant TEXT NOT NULL, asset_path TEXT, cover_path TEXT, caption TEXT,
        hashtags TEXT, first_comment TEXT, status TEXT NOT NULL DEFAULT 'draft',
        score INTEGER, ai_meta TEXT DEFAULT '{}', render_params TEXT DEFAULT '{}',
        scheduled_at TEXT, posted_at TEXT, posted_url TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    con.commit(); con.close()
    return db


def run_skill(args, env):
    e = dict(os.environ); e["SKILL_ARGS"] = json.dumps(args); e.update(env)
    p = subprocess.run([sys.executable, str(SKILL)], capture_output=True, text=True, env=e)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_campaign_renders_variants_and_queues(tmp_path):
    db = _make_db(tmp_path)
    photo = tmp_path / "job.jpg"
    Image.new("RGB", (1600, 1200), (120, 90, 60)).save(photo)
    out = run_skill(
        {"topic": "kitchen remodel reveal", "photo": str(photo),
         "platforms": ["ig_square", "fb"], "queue": True, "project_id": 4},
        env={"BAZA_DASHBOARD_DB": str(db),
             "BAZA_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
             "OLLAMA_HOST": "http://127.0.0.1:9",      # force template copy
             "BAZA_TOOL_SERVER": "http://127.0.0.1:9"}) # force photo-only (no SD)
    assert out["skill"] == "social_campaign"
    assert len(out["artifacts"]) == 2
    for a in out["artifacts"]:
        assert Path(a["path"]).exists()
    con = sqlite3.connect(db)
    rows = con.execute("SELECT platform, status FROM ahb_social_posts").fetchall()
    con.close()
    assert sorted(r[0] for r in rows) == ["fb", "ig_square"]
    assert all(r[1] == "draft" for r in rows)


def test_campaign_no_queue_skips_db(tmp_path):
    db = _make_db(tmp_path)
    photo = tmp_path / "job.jpg"
    Image.new("RGB", (1600, 1200), (120, 90, 60)).save(photo)
    out = run_skill(
        {"topic": "bathroom", "photo": str(photo), "platforms": ["ig_square"],
         "queue": False},
        env={"BAZA_DASHBOARD_DB": str(db),
             "BAZA_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
             "OLLAMA_HOST": "http://127.0.0.1:9",
             "BAZA_TOOL_SERVER": "http://127.0.0.1:9"})
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM ahb_social_posts").fetchone()[0]
    con.close()
    assert n == 0
    assert out["queued"] == []


def test_campaign_topic_with_slash_is_safe(tmp_path):
    art = tmp_path / "artifacts"
    photo = tmp_path / "job.jpg"
    Image.new("RGB", (1600, 1200), (120, 90, 60)).save(photo)
    out = run_skill(
        {"topic": "kitchen/bath & remodel", "photo": str(photo),
         "platforms": ["ig_square"], "queue": False},
        env={"BAZA_DASHBOARD_DB": str(tmp_path / "x.db"),
             "BAZA_ARTIFACTS_DIR": str(art),
             "OLLAMA_HOST": "http://127.0.0.1:9",
             "BAZA_TOOL_SERVER": "http://127.0.0.1:9"})
    assert len(out["artifacts"]) == 1
    p = Path(out["artifacts"][0]["path"])
    assert p.exists()
    assert "/" not in p.name and "&" not in p.name
    # artifact lives directly under the shared project dir, no stray subdirs from the topic
    assert p.parent.name == "shared"
