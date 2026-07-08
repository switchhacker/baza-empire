# tests/test_ahb123_finalize.py
import os
from PIL import Image
from ahb123_util import SRC

def test_og_image_exists_and_correct_size():
    p = os.path.join(SRC, "assets", "s", "og-homepage.jpg")
    assert os.path.isfile(p)
    assert Image.open(p).size == (1200, 630)

def test_base_template_uses_png_logo_not_svg():
    base = open(os.path.join(SRC, "templates", "base.html")).read()
    assert "logo.svg" not in base
    assert "GA_MEASUREMENT_ID" not in base

def test_readme_has_rollback_ips_and_cancel_step():
    readme = open(os.path.join(SRC, "README.md")).read()
    assert "198.49.23.144" in readme          # Squarespace rollback A record
    assert "ext-sq.squarespace.com" in readme
    assert "pages.dev" in readme
    assert "Cancel" in readme or "cancel" in readme
