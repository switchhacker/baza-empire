# tests/test_nav_main_tabs.py — Email + Web in the main nav; banner shrunk (spec A3/A4)
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as f:
        return f.read()

def test_main_nav_has_email_and_web_links():
    nav = read("dashboard", "templates", "_nav.html")
    assert 'href="/email"' in nav
    assert 'href="/web"' in nav
    # active-state wiring for both keys
    assert "_act == 'email'" in nav and "_act == 'web'" in nav

def test_web_route_and_template_exist():
    app_src = read("dashboard", "app.py")
    assert "@app.route('/web')" in app_src
    web = read("dashboard", "templates", "web.html")
    assert "nav_active = 'web'" in web.replace('"', "'")
    assert "_nav.html" in web

def test_banner_shrink_rules_present():
    nav = read("dashboard", "templates", "_nav.html")
    assert ".nav-brand h1{font-size:13px" in nav
