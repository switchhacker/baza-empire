"""Tests for core/geocode.py — Nominatim geocoding for AHBCO project addresses.

All HTTP is mocked; no live Nominatim calls happen here (the real 1.1s
rate-limited sleep only lives in scripts/backfill_geocode.py, which is not
exercised by this test module).
"""
import importlib
import json
import os
import sys
import sqlite3
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import core.geocode as geocode_mod  # noqa: E402


# ---- fixtures --------------------------------------------------------

AHB_PROJECTS_DDL = """
CREATE TABLE ahb_projects (
    id TEXT PRIMARY KEY,
    client_id TEXT,
    title TEXT,
    address TEXT,
    scope TEXT,
    description TEXT,
    budget_low REAL,
    budget_high REAL,
    status TEXT DEFAULT 'estimate',
    start_date TEXT,
    end_date TEXT,
    assigned_agents TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    acquisition_type TEXT DEFAULT '',
    value REAL DEFAULT 0,
    client_email TEXT DEFAULT '',
    contact_info TEXT DEFAULT '',
    location TEXT DEFAULT '',
    client_name TEXT DEFAULT '',
    year TEXT DEFAULT '',
    latitude REAL,
    longitude REAL,
    geocoded_at TEXT,
    commission_pct REAL DEFAULT 10,
    commission_value REAL DEFAULT 0,
    commission_beneficiary TEXT DEFAULT '',
    terms_conditions TEXT,
    payment_terms TEXT DEFAULT ''
);
"""


@pytest.fixture()
def conn():
    tmp = tempfile.mkdtemp(prefix="test_geocode_")
    db_path = os.path.join(tmp, "test.db")
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript(AHB_PROJECTS_DDL)
    c.commit()
    yield c
    c.close()


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---- geocode() ---------------------------------------------------------

def test_geocode_parses_first_result(monkeypatch):
    body = json.dumps([
        {"lat": "39.9526", "lon": "-75.1652", "display_name": "Philadelphia, PA"},
        {"lat": "0.0", "lon": "0.0", "display_name": "should not be used"},
    ]).encode()

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = req.headers
        captured["timeout"] = timeout
        return _FakeResponse(body)

    monkeypatch.setattr(geocode_mod.urllib.request, "urlopen", fake_urlopen)

    result = geocode_mod.geocode("1234 Market St, Philadelphia, PA")
    assert result == (39.9526, -75.1652)
    # global-constraints.md: UA header + reasonable timeout
    assert "baza-empire" in captured["headers"].get("User-agent", "")
    assert captured["timeout"] is not None and captured["timeout"] <= 10


def test_geocode_none_on_error(monkeypatch):
    def raise_urlopen(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(geocode_mod.urllib.request, "urlopen", raise_urlopen)
    assert geocode_mod.geocode("nowhere") is None


def test_geocode_none_on_empty_results(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _FakeResponse(b"[]")

    monkeypatch.setattr(geocode_mod.urllib.request, "urlopen", fake_urlopen)
    assert geocode_mod.geocode("nonexistent address xyz") is None


def test_geocode_none_on_blank_address():
    assert geocode_mod.geocode("") is None
    assert geocode_mod.geocode("   ") is None


# ---- ensure_project_coords() -------------------------------------------

def test_ensure_coords_cached_no_fetch(conn, monkeypatch):
    conn.execute(
        "INSERT INTO ahb_projects (id, title, address, latitude, longitude) "
        "VALUES ('p1', 'Cached Job', '1 Cached Ave', 40.1, -75.1)"
    )
    conn.commit()

    def boom(address):
        raise AssertionError("geocode() should not be called when coords are cached")

    monkeypatch.setattr(geocode_mod, "geocode", boom)

    result = geocode_mod.ensure_project_coords(conn, "p1")
    assert result == (40.1, -75.1)


def test_ensure_coords_updates_row(conn, monkeypatch):
    conn.execute(
        "INSERT INTO ahb_projects (id, title, address) "
        "VALUES ('p2', 'New Job', '42 New St, Philadelphia, PA')"
    )
    conn.commit()

    monkeypatch.setattr(geocode_mod, "geocode", lambda address: (39.9, -75.2))

    result = geocode_mod.ensure_project_coords(conn, "p2")
    assert result == (39.9, -75.2)

    row = conn.execute(
        "SELECT latitude, longitude, geocoded_at FROM ahb_projects WHERE id = 'p2'"
    ).fetchone()
    assert row["latitude"] == 39.9
    assert row["longitude"] == -75.2
    assert row["geocoded_at"] is not None


def test_ensure_coords_falls_back_to_location(conn, monkeypatch):
    # No `address`, but `location` is populated — brief: COALESCE(address, location)
    conn.execute(
        "INSERT INTO ahb_projects (id, title, location) "
        "VALUES ('p3', 'Location Only Job', '9 Location Rd, Philadelphia, PA')"
    )
    conn.commit()

    seen = {}

    def fake_geocode(address):
        seen["address"] = address
        return (10.0, 20.0)

    monkeypatch.setattr(geocode_mod, "geocode", fake_geocode)

    result = geocode_mod.ensure_project_coords(conn, "p3")
    assert result == (10.0, 20.0)
    assert seen["address"] == "9 Location Rd, Philadelphia, PA"


def test_ensure_coords_none_when_geocode_fails(conn, monkeypatch):
    conn.execute(
        "INSERT INTO ahb_projects (id, title, address) VALUES ('p4', 'Bad Job', 'nonsense')"
    )
    conn.commit()
    monkeypatch.setattr(geocode_mod, "geocode", lambda address: None)

    result = geocode_mod.ensure_project_coords(conn, "p4")
    assert result is None

    row = conn.execute("SELECT latitude, longitude FROM ahb_projects WHERE id = 'p4'").fetchone()
    assert row["latitude"] is None
    assert row["longitude"] is None


def test_ensure_coords_none_when_no_address(conn, monkeypatch):
    conn.execute("INSERT INTO ahb_projects (id, title) VALUES ('p5', 'No Address Job')")
    conn.commit()

    def boom(address):
        raise AssertionError("geocode() should not be called with no address/location")

    monkeypatch.setattr(geocode_mod, "geocode", boom)

    assert geocode_mod.ensure_project_coords(conn, "p5") is None


def test_ensure_coords_none_when_project_missing(conn):
    assert geocode_mod.ensure_project_coords(conn, "does-not-exist") is None


# ---- dashboard hook: never raises ---------------------------------------

@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_PROJECTS_DB", str(tmp_path / "test.db"))
    sys.modules.pop("dashboard.app", None)
    mod = importlib.import_module("dashboard.app")
    mod.app.config["TESTING"] = True
    return mod


def test_hook_never_raises(app_module, monkeypatch):
    """Geocoding failure during a project create/update must never break the save."""
    def boom(conn, project_id):
        raise RuntimeError("nominatim is on fire")

    monkeypatch.setattr("core.geocode.ensure_project_coords", boom)

    client = app_module.app.test_client()

    r = client.post("/api/ahb/projects", json={
        "title": "Boom Test Job",
        "address": "1 Boom St, Philadelphia, PA",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    pid = body["id"]

    r2 = client.put(f"/api/ahb/projects/{pid}", json={
        "address": "2 Boom St, Philadelphia, PA",
    })
    assert r2.status_code == 200
    assert r2.get_json()["success"] is True


def test_hook_populates_coords_on_create(app_module, monkeypatch):
    """Proves the create route actually calls ensure_project_coords (not vacuous)."""
    calls = []

    def fake_ensure(conn, project_id):
        calls.append(project_id)
        conn.execute(
            "UPDATE ahb_projects SET latitude=?, longitude=?, geocoded_at=datetime('now') WHERE id=?",
            (1.23, 4.56, project_id),
        )
        conn.commit()
        return (1.23, 4.56)

    monkeypatch.setattr("core.geocode.ensure_project_coords", fake_ensure)

    client = app_module.app.test_client()
    r = client.post("/api/ahb/projects", json={
        "title": "Geocode Wired Job",
        "address": "1 Wired St, Philadelphia, PA",
    })
    assert r.status_code == 200
    pid = r.get_json()["id"]
    assert calls == [pid]

    conn = app_module._ahb_db()
    row = conn.execute("SELECT latitude, longitude FROM ahb_projects WHERE id=?", (pid,)).fetchone()
    conn.close()
    assert row["latitude"] == 1.23
    assert row["longitude"] == 4.56


def test_hook_skips_geocode_without_address(app_module, monkeypatch):
    """No address/location on create → ensure_project_coords must not be called."""
    def boom(conn, project_id):
        raise AssertionError("should not geocode a project with no address")

    monkeypatch.setattr("core.geocode.ensure_project_coords", boom)

    client = app_module.app.test_client()
    r = client.post("/api/ahb/projects", json={"title": "No Address Job"})
    assert r.status_code == 200
    assert r.get_json()["success"] is True
