# tests/test_ahb123_render.py
import json, os
from ahb123_util import load, SRC

def _render_home():
    build = load("build")
    meta = json.load(open(os.path.join(SRC, "content", "meta.json")))["home"]
    body = open(os.path.join(SRC, "content", "home.html")).read()
    return build.render_page("home", meta, body)

def test_render_includes_title_and_description():
    html = _render_home()
    assert "<title>All Home Building Co LLC | Philadelphia Home Builder" in html
    assert 'name="description"' in html and "trusted home builder" in html

def test_render_includes_jsonld_and_legal_facts():
    html = _render_home()
    assert "PA175897" in html            # from v2 header JSON-LD
    assert "contactahbco@gmail.com" in html
    assert "serge@ahb123.com" not in html
    assert "info@ahb123.com" not in html

def test_render_includes_nova_and_footer():
    html = _render_home()
    assert 'name="nova-base" content="https://nova.ahb123.com"' in html
    assert "nova.ahb123.com/widget.js" in html
    assert "Bensalem, PA 19020" in html
    assert "(215) 554-5488" in html
    assert "484-6404" not in html

def test_render_header_shows_company_name_next_to_logo():
    # Squarespace parity: site-title text beside the logo in the nav
    html = _render_home()
    header = html.split("</header>")[0].split("<header")[1]
    assert 'src="/s/logo.png"' in header
    # visible text node (Squarespace site-title), not just the img alt attribute
    assert ">All Home Building Co<" in header

def test_render_has_no_ga_placeholder():
    assert "GA_MEASUREMENT_ID" not in _render_home()

def test_render_embeds_body_and_brand_css():
    html = _render_home()
    assert "Philadelphia's Trusted Home Builder" in html   # hero from home.html body
    assert '/assets/css/brand.css' in html
