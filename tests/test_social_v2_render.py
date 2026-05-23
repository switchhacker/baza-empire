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
