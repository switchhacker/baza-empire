import os
import sys
import tempfile
import subprocess

import pytest


def _have_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


@pytest.fixture()
def render_mod():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    if "social_render" in sys.modules:
        del sys.modules["social_render"]
    import social_render
    yield social_render
    if "social_render" in sys.modules:
        del sys.modules["social_render"]


def test_target_dims_for_platforms(render_mod):
    assert render_mod.target_dims("tiktok") == (1080, 1920)
    assert render_mod.target_dims("ig_reel") == (1080, 1920)
    assert render_mod.target_dims("ig_feed_square") == (1080, 1080)
    assert render_mod.target_dims("ig_feed_portrait") == (1080, 1350)
    assert render_mod.target_dims("ig_story") == (1080, 1920)


def test_target_dims_rejects_unknown(render_mod):
    with pytest.raises(ValueError):
        render_mod.target_dims("myspace")


def test_filter_graph_includes_aspect_crop(render_mod):
    g = render_mod.build_filter_graph(
        in_w=1920, in_h=1080, platform="tiktok",
        fill_mode="blurred", hook_text=None, brand_corner=False,
    )
    assert "scale=" in g
    assert "1080:1920" in g.replace(" ", "")


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg required for render integration")
def test_render_still_creates_jpg(render_mod, tmp_path):
    src = tmp_path / "src.jpg"
    from PIL import Image
    Image.new("RGB", (1920, 1080), (10, 200, 100)).save(src)
    out = tmp_path / "out.jpg"
    render_mod.render_still(
        src=str(src), out=str(out), platform="ig_feed_square",
        hook_text=None, brand_corner=False,
    )
    assert out.exists() and out.stat().st_size > 100
