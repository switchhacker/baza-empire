"""Theme toggle route — sets session + cookie."""
import importlib
import sys


def _client():
    # Re-import dashboard.app fresh per test for isolated session config.
    if "dashboard.app" in sys.modules:
        del sys.modules["dashboard.app"]
    sys.path.insert(0, ".")
    mod = importlib.import_module("dashboard.app")
    mod.app.config["TESTING"] = True
    mod.app.config["SECRET_KEY"] = "test"
    return mod.app.test_client()


def test_theme_route_accepts_dark():
    c = _client()
    r = c.post("/settings/theme", json={"value": "dark"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "theme": "dark"}
    # Cookie set with theme=dark
    cookies = r.headers.getlist("Set-Cookie")
    assert any("theme=dark" in c for c in cookies), cookies


def test_theme_route_accepts_light():
    c = _client()
    r = c.post("/settings/theme", json={"value": "light"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "theme": "light"}


def test_theme_route_rejects_invalid():
    c = _client()
    r = c.post("/settings/theme", json={"value": "rainbow"})
    assert r.status_code == 400
