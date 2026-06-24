import importlib.util, os, sys
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("media_kit", FRAMEWORK / "skills/shared/media_kit.py")
media_kit = importlib.util.module_from_spec(spec)
sys.modules["media_kit"] = media_kit
spec.loader.exec_module(media_kit)


def test_load_brand_returns_defaults_when_missing(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(media_kit, "BRAND_PATH", missing)
    brand = media_kit.load_brand()
    assert brand["short_name"] == "AHBCO"
    assert brand["colors"]["primary"].startswith("#")
    assert "headline" in brand["fonts"] and "body" in brand["fonts"]


def test_hex_to_rgb():
    assert media_kit.hex_to_rgb("#0A3D62") == (10, 61, 98)
    assert media_kit.hex_to_rgb("FFFFFF") == (255, 255, 255)


def test_load_brand_merges_partial_file(tmp_path, monkeypatch):
    p = tmp_path / "brand.json"
    p.write_text('{"colors": {"primary": "#112233"}}')
    monkeypatch.setattr(media_kit, "BRAND_PATH", p)
    brand = media_kit.load_brand()
    assert brand["colors"]["primary"] == "#112233"   # override kept
    assert brand["colors"]["accent"].startswith("#")  # default filled in
    assert brand["short_name"] == "AHBCO"             # default filled in


def test_load_brand_is_isolated_between_calls():
    b1 = media_kit.load_brand()
    b2 = media_kit.load_brand()
    b1["colors"]["primary"] = "#DEADBE"
    assert b2["colors"]["primary"] != "#DEADBE"


def test_pick_copy_model_excludes_cloud(monkeypatch):
    tags = {"models": [
        {"name": "gpt-oss:120b-cloud"},
        {"name": "qwen3-vl:latest"},        # vision -> skip
        {"name": "glm-ocr:latest"},          # ocr -> skip
        {"name": "qwen2.5:0.5b"},            # too small -> deprioritized
        {"name": "gemma4:26b-a4b-it-qat"},   # best general instruct
    ]}
    class R:
        status_code = 200
        def json(self): return tags
    monkeypatch.setattr(media_kit.requests, "get", lambda *a, **k: R())
    model = media_kit.pick_copy_model()
    assert model == "gemma4:26b-a4b-it-qat"
    assert "cloud" not in model


def test_pick_copy_model_none_when_unreachable(monkeypatch):
    def boom(*a, **k): raise OSError("down")
    monkeypatch.setattr(media_kit.requests, "get", boom)
    assert media_kit.pick_copy_model() is None


def test_write_copy_template_fallback_when_no_model(monkeypatch):
    monkeypatch.setattr(media_kit, "pick_copy_model", lambda: None)
    brand = media_kit.load_brand()
    out = media_kit.write_copy("kitchen remodel reveal", brand, kind="caption")
    assert out["caption"]                      # non-empty
    assert isinstance(out["hashtags"], list) and out["hashtags"]
    assert out["model"] == "template"


def test_write_copy_uses_model(monkeypatch):
    import json
    monkeypatch.setattr(media_kit, "pick_copy_model", lambda: "gemma4:26b-a4b-it-qat")
    payload = {"caption": "Fresh kitchen, fresh start.",
               "hashtags": ["#remodel", "#AHBCO"], "first_comment": "DM us!"}
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": json.dumps(payload)}}
    monkeypatch.setattr(media_kit.requests, "post", lambda *a, **k: R())
    brand = media_kit.load_brand()
    out = media_kit.write_copy("kitchen remodel", brand)
    assert out["caption"] == "Fresh kitchen, fresh start."
    assert "#AHBCO" in out["hashtags"]
    assert out["model"] == "gemma4:26b-a4b-it-qat"


from PIL import Image

def test_platforms_have_expected_sizes():
    assert media_kit.PLATFORMS["ig_square"] == (1080, 1080)
    assert media_kit.PLATFORMS["ig_reel"] == (1080, 1920)
    assert media_kit.PLATFORMS["fb"] == (1200, 630)
    assert media_kit.PLATFORMS["yt_thumb"] == (1280, 720)


def test_new_canvas_size_and_mode():
    img = media_kit.new_canvas("ig_square")
    assert img.size == (1080, 1080)
    assert img.mode == "RGB"


def test_load_photo_cover_fit(tmp_path):
    src = tmp_path / "p.png"
    Image.new("RGB", (2000, 500), (200, 100, 50)).save(src)
    out = media_kit.load_photo(str(src), (1080, 1080))
    assert out.size == (1080, 1080)   # cover-cropped to exact target


def test_draw_headline_and_logo_change_pixels():
    img = media_kit.new_canvas("ig_square", bg=(20, 20, 20))
    before = list(img.getdata())
    brand = media_kit.load_brand()
    media_kit.draw_headline(img, "KITCHEN REMODEL", (60, 700, 1020, 1000),
                            color=(255, 255, 255), font_path=brand["fonts"]["headline"])
    media_kit.scrim(img, side="bottom", height_frac=0.4)
    after = list(img.getdata())
    assert before != after            # something was drawn


def test_place_text_logo_fallback_when_no_file(tmp_path):
    img = media_kit.new_canvas("ig_square")
    brand = media_kit.load_brand()
    brand["logo"] = ""                 # force wordmark fallback
    # should not raise
    media_kit.place_logo(img, brand, corner="br")


def test_draw_headline_empty_text_is_noop():
    img = media_kit.new_canvas("ig_square", bg=(10, 10, 10))
    before = list(img.getdata())
    brand = media_kit.load_brand()
    media_kit.draw_headline(img, "", (60, 700, 1020, 1000),
                            color=(255, 255, 255), font_path=brand["fonts"]["headline"])
    media_kit.draw_headline(img, None, (60, 700, 1020, 1000),
                            color=(255, 255, 255), font_path=brand["fonts"]["headline"])
    assert list(img.getdata()) == before  # nothing drawn for empty/None


import sqlite3

def test_gen_background_returns_path(monkeypatch, tmp_path):
    out_png = tmp_path / "bg.png"
    Image.new("RGB", (10, 10), (5, 5, 5)).save(out_png)
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"success": True, "output": {"path": str(out_png)}}
    monkeypatch.setattr(media_kit.requests, "post", lambda *a, **k: R())
    p = media_kit.gen_background("modern kitchen, soft light", 1080, 1080)
    assert p == str(out_png)


def test_gen_background_none_on_failure(monkeypatch):
    def boom(*a, **k): raise OSError("sd down")
    monkeypatch.setattr(media_kit.requests, "post", boom)
    assert media_kit.gen_background("x", 1080, 1080) is None


def test_save_deliverable_writes_png(tmp_path, monkeypatch):
    monkeypatch.setattr(media_kit, "ARTIFACTS_DIR", tmp_path)
    img = media_kit.new_canvas("ig_square")
    res = media_kit.save_deliverable(img, "campaign_ig.png",
                                     project_id="ahb123", agent_id="sam_axe",
                                     description="test")
    assert res["success"] is True
    assert Path(res["path"]).exists()
    assert Path(res["path"]).suffix == ".png"


def test_queue_social_post_inserts_draft(tmp_path, monkeypatch):
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
    monkeypatch.setenv("BAZA_DASHBOARD_DB", str(db))
    pid = media_kit.queue_social_post(platform="ig_square", variant="feed",
                                      asset_path="/x/a.png", caption="hi",
                                      hashtags=["#AHBCO"], project_id=4)
    con = sqlite3.connect(db)
    row = con.execute("SELECT platform, status, caption FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    con.close()
    assert row == ("ig_square", "draft", "hi")
