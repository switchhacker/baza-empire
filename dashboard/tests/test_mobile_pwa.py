"""Tests for the mobile PWA repointing onto the full desktop dashboard.

Mirrors the client fixture pattern in tests/test_invoice_terms.py: import the
real app.py and use its Flask test client (the shared conftest `app` fixture
only wires the email blueprint, so it can't see these routes).
"""
import os
import sys

import pytest

# Make dashboard/ importable as a flat package from the test dir,
# AND make the parent (agent-framework-v3) importable so that
# `from dashboard.private_inbound import ...` inside app.py resolves.
DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT_DIR = os.path.dirname(DASHBOARD_DIR)
for _p in (DASHBOARD_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as appmod


@pytest.fixture
def client():
    with appmod.app.test_client() as c:
        yield c


def test_mobile_redirects_to_desktop_root(client):
    res = client.get("/mobile")
    assert res.status_code == 302
    # Location may be absolute or relative; the path must be the desktop root.
    loc = res.headers["Location"]
    assert loc.rstrip("/").endswith("") and loc.endswith("/"), loc
    assert loc in ("/", "http://localhost/")


def test_mobile_classic_still_serves_old_app(client):
    res = client.get("/mobile-classic")
    assert res.status_code == 200
    assert b"tab-bar-item" in res.data  # marker unique to mobile.html's bottom tab bar


def test_manifest_start_url_is_desktop_root(client):
    res = client.get("/mobile/manifest.json")
    assert res.status_code == 200
    data = res.get_json()
    assert data["start_url"] == "/"
    assert data["scope"] == "/"
    assert data["display"] == "standalone"


def test_nav_injects_pwa_and_desktop_scaling(client):
    # `/datahub` is a plain GET page that includes _nav.html and renders without
    # form state — a stable place to assert the shared nav injection ships.
    res = client.get("/datahub")
    assert res.status_code == 200, res.status_code
    html = res.data.decode("utf-8", "replace")
    # PWA install plumbing delivered on every nav page:
    assert "/mobile/manifest.json" in html
    assert "serviceWorker.register('/sw.js'" in html
    # Mobile-only true-desktop scaling:
    assert "width=1280" in html
    # Mobile-only safe-area offset keeps the sticky nav clear of the notch/camera:
    assert "safe-area-inset-top" in html
