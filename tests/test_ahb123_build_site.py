# tests/test_ahb123_build_site.py
import hashlib, os, tempfile
from ahb123_util import load

def _build():
    build = load("build")
    d = tempfile.mkdtemp(prefix="ahb123dist_")
    build.build_site(d)
    return d

def test_clean_url_tree():
    d = _build()
    assert os.path.isfile(os.path.join(d, "index.html"))              # home
    for slug in ["services", "portfolio", "about", "contact", "plan"]:
        assert os.path.isfile(os.path.join(d, slug, "index.html")), slug

def test_images_and_css_copied():
    d = _build()
    # count only the numbered portfolio images, so this stays correct after
    # Task 5 adds the (unnumbered) og-homepage.jpg to assets/s.
    portfolio_jpgs = [f for f in os.listdir(os.path.join(d, "s"))
                      if f.endswith(".jpg") and f[0].isdigit()]
    assert len(portfolio_jpgs) == 48
    assert os.path.isfile(os.path.join(d, "s", "logo.png"))
    assert os.path.isfile(os.path.join(d, "assets", "css", "brand.css"))

def test_sitemap_lists_six_canonical_urls():
    d = _build()
    with open(os.path.join(d, "sitemap.xml")) as f:
        sm = f.read()
    for url in ["https://ahb123.com/", "https://ahb123.com/services",
                "https://ahb123.com/portfolio", "https://ahb123.com/about",
                "https://ahb123.com/contact", "https://ahb123.com/plan"]:
        assert f"<loc>{url}</loc>" in sm
    assert sm.count("<loc>") == 6

def test_no_ga_placeholder_anywhere():
    d = _build()
    for root, _, files in os.walk(d):
        for fn in files:
            if fn.endswith((".html", ".xml", ".txt")):
                assert "GA_MEASUREMENT_ID" not in open(os.path.join(root, fn)).read()

def test_build_is_idempotent():
    d1, d2 = _build(), _build()
    m1 = open(os.path.join(d1, "_manifest.txt")).read()
    m2 = open(os.path.join(d2, "_manifest.txt")).read()
    assert m1 == m2 and "_manifest.txt" not in m1
