"""Tests for Social Studio v2.1 render pipeline extensions."""
import os
import sys
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def render_mod():
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    if "social_render" in sys.modules:
        del sys.modules["social_render"]
    import social_render
    yield social_render


def test_filter_graph_with_lut(render_mod):
    g = render_mod.build_filter_graph(
        in_w=1080, in_h=1920, platform="tiktok", fill_mode="blurred",
        hook_text=None, brand_corner=False, lut_path="/fake/cinematic.cube",
    )
    assert "lut3d=" in g
    assert "cinematic.cube" in g


def test_filter_graph_with_logo(render_mod):
    g = render_mod.build_filter_graph(
        in_w=1080, in_h=1920, platform="tiktok", fill_mode="blurred",
        hook_text=None, brand_corner=False, logo_path="/fake/logo.png",
    )
    assert "movie=" in g or "overlay=" in g


def test_filter_graph_with_subtitles(render_mod):
    g = render_mod.build_filter_graph(
        in_w=1080, in_h=1920, platform="tiktok", fill_mode="blurred",
        hook_text=None, brand_corner=False, subtitles_path="/fake/subs.srt",
    )
    assert "subtitles=" in g


def test_filter_graph_ken_burns(render_mod):
    g = render_mod.build_filter_graph(
        in_w=1080, in_h=1080, platform="ig_feed_square", fill_mode="blurred",
        hook_text=None, brand_corner=False, ken_burns=True,
    )
    assert "zoompan=" in g


def test_filter_graph_no_ken_burns(render_mod):
    g = render_mod.build_filter_graph(
        in_w=1080, in_h=1080, platform="ig_feed_square", fill_mode="blurred",
        hook_text=None, brand_corner=False, ken_burns=False,
    )
    assert "zoompan=" not in g


def test_snap_outpoints_to_beats(render_mod):
    # Beats at 1.0, 2.0, 3.0, 4.0, 5.0 seconds
    beats = [1.0, 2.0, 3.0, 4.0, 5.0]
    clips = [
        {"path": "a.mp4", "in_seconds": 0, "out_seconds": 2.3},
        {"path": "b.mp4", "in_seconds": 0, "out_seconds": 1.8},
    ]
    out = render_mod._snap_outpoints_to_beats(clips, beats)
    # First clip should snap to nearest beat ≥ 0.5s — likely 2.0
    assert out[0]["out_seconds"] in (2.0, 3.0)
    # Second clip starts at cursor (~2.0 or 3.0); outpoint should land on a beat
    assert out[1]["out_seconds"] > 0


def test_snap_outpoints_no_beats_is_noop(render_mod):
    clips = [{"path": "a.mp4", "in_seconds": 0, "out_seconds": 2.3}]
    out = render_mod._snap_outpoints_to_beats(clips, [])
    assert out[0]["out_seconds"] == 2.3


def test_edits_filter_chain_empty(render_mod):
    assert render_mod.build_edits_filter_chain({}) == ""
    assert render_mod.build_edits_filter_chain(None) == ""


def test_edits_filter_chain_crop(render_mod):
    chain = render_mod.build_edits_filter_chain({"crop": {"x": 10, "y": 20, "w": 1080, "h": 1920}})
    assert "crop=1080:1920:10:20" in chain


def test_edits_filter_chain_rotate_90(render_mod):
    chain = render_mod.build_edits_filter_chain({"rotate": 90})
    assert "transpose=1" in chain
    assert "rotate=" not in chain


def test_edits_filter_chain_rotate_180(render_mod):
    chain = render_mod.build_edits_filter_chain({"rotate": 180})
    assert "transpose=1,transpose=1" in chain


def test_edits_filter_chain_rotate_270(render_mod):
    chain = render_mod.build_edits_filter_chain({"rotate": 270})
    assert "transpose=2" in chain


def test_edits_filter_chain_rotate_free(render_mod):
    chain = render_mod.build_edits_filter_chain({"rotate": 12.5})
    assert "rotate=" in chain
    # ~12.5° in radians is ~0.218
    assert "0.218" in chain or "0.217" in chain


def test_edits_filter_chain_eq(render_mod):
    chain = render_mod.build_edits_filter_chain({
        "brightness": 0.1, "contrast": 0.2, "saturation": -0.3,
    })
    assert "eq=" in chain
    assert "brightness=0.100" in chain
    assert "contrast=1.200" in chain
    assert "saturation=0.700" in chain


def test_edits_filter_chain_skips_zero_eq(render_mod):
    chain = render_mod.build_edits_filter_chain({"brightness": 0.0})
    assert "eq=" not in chain


def test_edits_filter_chain_filter_preset_to_lut(render_mod, tmp_path, monkeypatch):
    # Build a fake LUT file so the chain includes lut3d=
    lut_dir = tmp_path / "luts"
    lut_dir.mkdir()
    (lut_dir / "cinematic.cube").write_text("TITLE \"fake\"\n")
    monkeypatch.setattr(render_mod, "_LUT_DIR", str(lut_dir))
    chain = render_mod.build_edits_filter_chain({"filter": "cinematic"})
    assert "lut3d=" in chain
    assert "cinematic.cube" in chain


def test_edits_filter_chain_filter_none_is_skipped(render_mod):
    chain = render_mod.build_edits_filter_chain({"filter": "none"})
    assert "lut3d=" not in chain


def test_edits_filter_chain_combined_order(render_mod):
    chain = render_mod.build_edits_filter_chain({
        "crop": {"x": 0, "y": 0, "w": 100, "h": 100},
        "rotate": 90,
        "brightness": 0.1,
    })
    # Crop comes first, then rotate, then eq
    crop_pos = chain.find("crop=")
    rot_pos = chain.find("transpose=")
    eq_pos = chain.find("eq=")
    assert 0 <= crop_pos < rot_pos < eq_pos


def test_preprocess_with_edits_noop_returns_source(render_mod, tmp_path):
    src = tmp_path / "x.jpg"
    src.write_bytes(b"fake")
    out = render_mod.preprocess_with_edits(str(src), {})
    assert out == str(src)


# ── Advanced edit ops (flip / temperature / hue / sharpen / vignette) ────────

@pytest.fixture()
def studio_mod():
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    if "social_studio" in sys.modules:
        del sys.modules["social_studio"]
    import social_studio
    yield social_studio


def test_edits_filter_chain_flips(render_mod):
    chain = render_mod.build_edits_filter_chain({"flip_h": True, "flip_v": True})
    assert "hflip" in chain
    assert "vflip" in chain


def test_edits_filter_chain_flip_false_skipped(render_mod):
    chain = render_mod.build_edits_filter_chain({"flip_h": False})
    assert "hflip" not in chain


def test_edits_filter_chain_temperature_warm(render_mod):
    chain = render_mod.build_edits_filter_chain({"temperature": 1.0})
    # warm = lower Kelvin target
    assert "colortemperature=temperature=4500" in chain


def test_edits_filter_chain_temperature_cool(render_mod):
    chain = render_mod.build_edits_filter_chain({"temperature": -1.0})
    assert "colortemperature=temperature=8500" in chain


def test_edits_filter_chain_hue(render_mod):
    chain = render_mod.build_edits_filter_chain({"hue": 45})
    assert "hue=h=45" in chain


def test_edits_filter_chain_sharpen_positive(render_mod):
    chain = render_mod.build_edits_filter_chain({"sharpen": 0.5})
    assert "unsharp=5:5:1.00" in chain


def test_edits_filter_chain_sharpen_negative_blurs(render_mod):
    chain = render_mod.build_edits_filter_chain({"sharpen": -0.5})
    assert "unsharp=5:5:-1.00" in chain


def test_edits_filter_chain_vignette(render_mod):
    chain = render_mod.build_edits_filter_chain({"vignette": 0.6})
    assert "vignette=angle=" in chain


def test_edits_filter_chain_advanced_order(render_mod):
    chain = render_mod.build_edits_filter_chain({
        "rotate": 90, "flip_h": True, "brightness": 0.1,
        "temperature": 0.5, "hue": 10, "sharpen": 0.5, "vignette": 0.3,
    })
    rot = chain.find("transpose=")
    flip = chain.find("hflip")
    eq = chain.find("eq=")
    temp = chain.find("colortemperature=")
    hue = chain.find("hue=h=")
    sharp = chain.find("unsharp=")
    vig = chain.find("vignette=")
    assert 0 <= rot < flip < eq < temp < hue < sharp < vig


def test_normalize_edits_new_keys(studio_mod):
    out = studio_mod._normalize_edits({
        "flip_h": True, "flip_v": 1,
        "temperature": 0.4, "hue": 33.0, "sharpen": -0.3, "vignette": 0.7,
    })
    assert out["flip_h"] is True
    assert out["flip_v"] is True
    assert out["temperature"] == pytest.approx(0.4)
    assert out["hue"] == pytest.approx(33.0)
    assert out["sharpen"] == pytest.approx(-0.3)
    assert out["vignette"] == pytest.approx(0.7)


def test_normalize_edits_new_keys_clamped(studio_mod):
    out = studio_mod._normalize_edits({
        "temperature": 5, "hue": 999, "sharpen": -9, "vignette": 3,
    })
    assert out["temperature"] == 1.0
    assert out["hue"] == 180.0
    assert out["sharpen"] == -1.0
    assert out["vignette"] == 1.0


def test_normalize_edits_new_keys_zero_dropped(studio_mod):
    out = studio_mod._normalize_edits({
        "flip_h": False, "flip_v": 0,
        "temperature": 0, "hue": 0, "sharpen": 0.0, "vignette": 0,
    })
    for k in ("flip_h", "flip_v", "temperature", "hue", "sharpen", "vignette"):
        assert k not in out
