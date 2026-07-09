# tests/test_web_page.py — /web command center (spec B3, phase B-i slice)
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as f:
        return f.read()

def test_web_page_wires_the_override_apis():
    web = read("dashboard", "templates", "web.html")
    for api in ["/api/ui/overrides/summary", "/api/ui/overrides/history",
                "/revert", "/api/ui/overrides/reset", "/api/ahb/web/status"]:
        assert api in web, f"missing {api}"

def test_web_page_lists_dash_pages_with_edit_links():
    web = read("dashboard", "templates", "web.html")
    assert "edit=1" in web
    for path in ["/ahb123", "/datahub", "/projects", "/cloud", "/settings"]:
        assert f"'{path}'" in web, f"page list missing {path}"
