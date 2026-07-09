# tests/test_editor_wiring.py — edit.js wired into every page via _nav.html (spec B1)
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as f:
        return f.read()

def test_nav_includes_editor_assets_with_cache_bust():
    nav = read("dashboard", "templates", "_nav.html")
    assert "/static/edit.js?v=" in nav
    assert "/static/edit.css?v=" in nav

def test_app_registers_ui_blueprint_and_inits_db():
    src = read("dashboard", "app.py")
    assert "ui_editor" in src and "ui_bp" in src
    assert "init_db()" in src.split("ui_editor")[1][:500]

def test_edit_js_core_api_surface():
    js = read("dashboard", "static", "edit.js")
    for name in ["selectorFor", "fingerprintFor", "saveOverride", "refresh",
                 "setEditMode", "getSelected", "window.BazaEdit"]:
        assert name in js, f"missing {name}"
    # mutation-loop guard: text apply must be conditional
    assert "el.textContent !== o.value" in js

def test_inspector_capabilities_present():
    js = read("dashboard", "static", "edit.js")
    for feature in ["buildInspector", "api/ui/upload", "contenteditable",
                    "fontSize", "borderRadius", "Reset element", "Hide element"]:
        assert feature in js, f"inspector missing {feature}"

def test_asset_version_bumped():
    nav = read("dashboard", "templates", "_nav.html")
    assert "edit.js?v=2" in nav and "edit.css?v=2" in nav
