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
