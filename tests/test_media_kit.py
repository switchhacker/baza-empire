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
