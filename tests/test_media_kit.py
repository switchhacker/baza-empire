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
