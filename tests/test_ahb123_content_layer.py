import json, os, re
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "web", "ahb123")
SLUGS = ["home", "services", "portfolio", "about", "contact", "plan"]

def test_all_content_bodies_present_and_nonempty():
    for slug in SLUGS:
        p = os.path.join(SRC, "content", f"{slug}.html")
        assert os.path.isfile(p), f"missing {p}"
        assert os.path.getsize(p) > 100, f"too small {p}"

def test_meta_json_has_all_slugs_with_required_keys():
    meta = json.load(open(os.path.join(SRC, "content", "meta.json")))
    assert set(meta) == set(SLUGS)
    for slug, m in meta.items():
        assert m["title"] and m["description"] and m["og_image"]

def test_v2_legal_facts_present_and_no_stale_email():
    about = open(os.path.join(SRC, "content", "about.html")).read()
    contact = open(os.path.join(SRC, "content", "contact.html")).read()
    assert "PA175897" in about
    assert "contactahbco@gmail.com" in contact
    for slug in SLUGS:
        body = open(os.path.join(SRC, "content", f"{slug}.html")).read()
        assert "info@ahb123.com" not in body, f"stale email in {slug}"
        assert "serge@ahb123.com" not in body, f"stale email in {slug}"

def test_all_portfolio_images_exist_in_assets():
    portfolio = open(os.path.join(SRC, "content", "portfolio.html")).read()
    refs = set(re.findall(r'/s/([0-9][^"\']+\.jpg)', portfolio))
    assert len(refs) == 48, f"expected 48 image refs, got {len(refs)}"
    for fn in refs:
        assert os.path.isfile(os.path.join(SRC, "assets", "s", fn)), f"missing image {fn}"

def test_logo_and_brand_css_present():
    assert os.path.isfile(os.path.join(SRC, "assets", "s", "logo.png"))
    assert os.path.getsize(os.path.join(SRC, "assets", "css", "brand.css")) > 500

def test_no_squarespace_cdn_refs_in_any_content():
    """After migration, no content page may depend on the Squarespace CDN."""
    for slug in SLUGS:
        body = open(os.path.join(SRC, "content", f"{slug}.html")).read()
        assert "squarespace-cdn.com" not in body, f"CDN ref left in {slug}"

def test_services_uses_local_image_paths():
    svc = open(os.path.join(SRC, "content", "services.html")).read()
    for fn in ["01-modern-kitchen-fishtown.jpg", "04-gut-rehab-kensington.jpg",
               "06-luxury-bath-center-city.jpg", "07-sunroom-ardmore.jpg",
               "14-new-build-kitchen-bensalem.jpg"]:
        assert f"/s/{fn}" in svc, f"missing local ref {fn}"
