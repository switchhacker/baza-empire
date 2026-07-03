"""Tests for agents/duke_harmon/crons/drive_context.py (Task 16 of the
cron-improvements plan) -- Duke's weekday-morning jobsite drive-time
briefing.

All external calls are mocked: home-address geocoding (core.geocode.geocode,
monkeypatched on the drive_context module object), OSRM
(agents.duke_harmon.crons.drive_context._fetch_osrm, the single HTTP seam),
and Telegram delivery (core.telegram_fmt.post_html, the seam both
send_alert() and send_report()'s immediate-send path funnel through). DBs
are tmp SQLite files -- the business DB's ahb_projects DDL is copied
verbatim from `sqlite3 dashboard/baza_projects.db ".schema ahb_projects"`
(same approach as tests/test_geocode.py and tests/test_weather_watch.py),
and cron_health.db is pointed at a tmp path via BAZA_CRON_HEALTH_DB + a
fresh reimport (mirrors tests/test_cron_helpers_routing.py's `ch` fixture).
"""
import datetime
import importlib
import inspect
import json
import os
import stat
import sqlite3
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


# ── ahb_projects DDL (copied via sqlite3 .schema, same as test_geocode.py) ──

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


def insert_project(conn, id, title="Test Job", address="1 Test St, Philadelphia, PA",
                    status="In Progress", latitude=40.1, longitude=-75.1):
    conn.execute(
        "INSERT INTO ahb_projects (id, title, address, status, latitude, longitude) "
        "VALUES (?,?,?,?,?,?)",
        (id, title, address, status, latitude, longitude),
    )
    conn.commit()


NOW = datetime.datetime(2026, 7, 2, 6, 15, 0)  # a Thursday, matches the 15 6 * * 1-5 cron


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh core.cron_health_db (tmp path) + agents.cron_helpers (business DB_PATH
    pointed at a tmp file, quiet hours forced off) + a fresh
    agents.duke_harmon.crons.drive_context import, with HOME_COORDS_FILE
    repointed at a tmp file and BAZA_HOME_ADDRESS cleared. Mirrors
    tests/test_weather_watch.py's `env` fixture."""
    ch_db_path = tmp_path / "cron_health.db"
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", str(ch_db_path))

    for mod in (
        "core.cron_health_db",
        "agents.cron_helpers",
        "agents.duke_harmon.crons.drive_context",
    ):
        sys.modules.pop(mod, None)

    chdb = importlib.import_module("core.cron_health_db")
    chdb.init()

    ch = importlib.import_module("agents.cron_helpers")

    biz_db_path = tmp_path / "baza_projects.db"
    conn = sqlite3.connect(str(biz_db_path))
    conn.executescript(AHB_PROJECTS_DDL)
    conn.commit()
    conn.close()

    monkeypatch.setattr(ch, "DB_PATH", str(biz_db_path))
    monkeypatch.setattr(ch, "in_quiet_hours", lambda *a, **k: False)

    dc = importlib.import_module("agents.duke_harmon.crons.drive_context")
    monkeypatch.setattr(dc, "HOME_COORDS_FILE", str(tmp_path / "home_coords.json"))
    monkeypatch.delenv("BAZA_HOME_ADDRESS", raising=False)

    return {"dc": dc, "ch": ch, "chdb": chdb, "biz_db": str(biz_db_path)}


@pytest.fixture()
def posted(monkeypatch):
    """Recorder standing in for core.telegram_fmt.post_html -- the single seam
    both send_alert() and send_telegram() (used by send_report's immediate
    sends) funnel actual Telegram delivery through."""
    calls = []

    def fake_post_html(token, chat_id, text, *a, **k):
        calls.append({"token": token, "chat_id": chat_id, "text": text})
        return True

    import core.telegram_fmt as telegram_fmt
    monkeypatch.setattr(telegram_fmt, "post_html", fake_post_html)
    return calls


def _biz_conn(env):
    conn = sqlite3.connect(env["biz_db"])
    conn.row_factory = sqlite3.Row
    return conn


def _osrm_ok(duration_s, distance_m):
    return {"code": "Ok", "routes": [{"duration": duration_s, "distance": distance_m}]}


# ── brief's 4 required tests ─────────────────────────────────────────────

def test_no_home_address_setup_nag_once(env, posted):
    """BAZA_HOME_ADDRESS unset -> one deduped setup nag, no active-sites
    lookup attempted, exits cleanly (no exception)."""
    dc = env["dc"]
    dc.main(now=NOW)
    dc.main(now=NOW)

    assert len(posted) == 1
    assert "BAZA_HOME_ADDRESS" in posted[0]["text"]


def test_route_message_format(env, posted, monkeypatch):
    dc = env["dc"]
    monkeypatch.setenv("BAZA_HOME_ADDRESS", "123 Home St, Philadelphia, PA")
    monkeypatch.setattr(dc, "geocode", lambda addr: (40.0, -75.0))

    conn = _biz_conn(env)
    insert_project(conn, "site-1", title="Deck rebuild", address="123 Main St",
                    status="In Progress", latitude=40.2, longitude=-75.2)
    conn.close()

    # 1920s = 32 min, 28968.19m ~= 18.0 mi
    monkeypatch.setattr(dc, "_fetch_osrm", lambda *a, **k: _osrm_ok(1920, 28968.19))

    dc.main(now=NOW)

    assert len(posted) == 1
    text = posted[0]["text"]
    assert "123 Main St (Deck rebuild)" in text
    assert "~32 min" in text
    assert "18 mi" in text
    assert "no-traffic baseline" in text
    assert "rough estimate" not in text


def test_osrm_down_haversine_fallback(env, posted, monkeypatch):
    dc = env["dc"]
    monkeypatch.setenv("BAZA_HOME_ADDRESS", "123 Home St, Philadelphia, PA")
    monkeypatch.setattr(dc, "geocode", lambda addr: (40.0, -75.0))

    conn = _biz_conn(env)
    insert_project(conn, "site-2", title="Bath remodel", address="456 Oak Ave",
                    status="In Progress", latitude=40.1, longitude=-75.05)
    conn.close()

    monkeypatch.setattr(dc, "_fetch_osrm", lambda *a, **k: None)  # OSRM unreachable

    dc.main(now=NOW)

    assert len(posted) == 1
    text = posted[0]["text"]
    assert "rough estimate" in text
    assert "no-traffic baseline" not in text

    expected_miles = dc._haversine_miles(40.0, -75.0, 40.1, -75.05)
    expected_minutes = round(expected_miles * dc.HAVERSINE_MIN_PER_MILE)
    assert f"~{expected_minutes} min" in text
    assert f"({expected_miles:.0f} mi, rough estimate)" in text


def test_osrm_non_ok_code_also_falls_back(env, posted, monkeypatch):
    """A parsed-but-non-'Ok' OSRM response (e.g. NoRoute) must fall back
    exactly like a hard failure, not raise or produce a bogus ETA."""
    dc = env["dc"]
    monkeypatch.setenv("BAZA_HOME_ADDRESS", "123 Home St, Philadelphia, PA")
    monkeypatch.setattr(dc, "geocode", lambda addr: (40.0, -75.0))

    conn = _biz_conn(env)
    insert_project(conn, "site-3", status="In Progress", latitude=40.3, longitude=-75.3)
    conn.close()

    monkeypatch.setattr(dc, "_fetch_osrm", lambda *a, **k: {"code": "NoRoute", "routes": []})

    dc.main(now=NOW)

    assert len(posted) == 1
    assert "rough estimate" in posted[0]["text"]


def test_no_active_sites_silent(env, posted, monkeypatch):
    dc = env["dc"]
    monkeypatch.setenv("BAZA_HOME_ADDRESS", "123 Home St, Philadelphia, PA")
    monkeypatch.setattr(dc, "geocode", lambda addr: (40.0, -75.0))
    # No projects inserted at all -- ahb_projects is empty.
    dc.main(now=NOW)
    assert len(posted) == 0


# ── extra edge-case coverage ──────────────────────────────────────────────

def test_active_status_without_coords_excluded(env, posted, monkeypatch):
    dc = env["dc"]
    monkeypatch.setenv("BAZA_HOME_ADDRESS", "123 Home St")
    monkeypatch.setattr(dc, "geocode", lambda addr: (40.0, -75.0))

    conn = _biz_conn(env)
    insert_project(conn, "site-nogeo", status="In Progress", latitude=None, longitude=None)
    conn.close()

    dc.main(now=NOW)
    assert len(posted) == 0


def test_non_active_status_with_coords_excluded(env, posted, monkeypatch):
    dc = env["dc"]
    monkeypatch.setenv("BAZA_HOME_ADDRESS", "123 Home St")
    monkeypatch.setattr(dc, "geocode", lambda addr: (40.0, -75.0))

    conn = _biz_conn(env)
    insert_project(conn, "site-planning", status="Planning", latitude=40.1, longitude=-75.1)
    conn.close()

    dc.main(now=NOW)
    assert len(posted) == 0


def test_home_geocode_failure_is_silent(env, posted, monkeypatch):
    """BAZA_HOME_ADDRESS is set but can't be geocoded (bad address, Nominatim
    down) -> no crash, no message (nothing meaningful to report)."""
    dc = env["dc"]
    monkeypatch.setenv("BAZA_HOME_ADDRESS", "Nonexistent Address, Nowhere")
    monkeypatch.setattr(dc, "geocode", lambda addr: None)

    conn = _biz_conn(env)
    insert_project(conn, "site-y", status="In Progress", latitude=40.1, longitude=-75.1)
    conn.close()

    dc.main(now=NOW)
    assert len(posted) == 0


def test_multiple_sites_all_lines_sorted(env, posted, monkeypatch):
    dc = env["dc"]
    monkeypatch.setenv("BAZA_HOME_ADDRESS", "123 Home St")
    monkeypatch.setattr(dc, "geocode", lambda addr: (40.0, -75.0))
    monkeypatch.setattr(dc, "_fetch_osrm", lambda *a, **k: _osrm_ok(600, 8000))

    conn = _biz_conn(env)
    insert_project(conn, "site-b", title="B site", address="B Ave",
                    status="In Progress", latitude=40.1, longitude=-75.1)
    insert_project(conn, "site-a", title="A site", address="A Ave",
                    status="In Progress", latitude=40.2, longitude=-75.2)
    conn.close()

    dc.main(now=NOW)

    assert len(posted) == 1
    text = posted[0]["text"]
    assert "A Ave" in text and "B Ave" in text
    assert text.index("A Ave") < text.index("B Ave")


# ── home-coords cache ──────────────────────────────────────────────────────

def test_home_coords_cached_across_runs_no_regeocode(env, posted, monkeypatch):
    dc = env["dc"]
    monkeypatch.setenv("BAZA_HOME_ADDRESS", "123 Home St, Philadelphia, PA")
    calls = []

    def fake_geocode(addr):
        calls.append(addr)
        return (40.0, -75.0)
    monkeypatch.setattr(dc, "geocode", fake_geocode)
    monkeypatch.setattr(dc, "_fetch_osrm", lambda *a, **k: _osrm_ok(600, 8000))

    conn = _biz_conn(env)
    insert_project(conn, "site-cache", status="In Progress", latitude=40.2, longitude=-75.2)
    conn.close()

    dc.main(now=NOW)
    dc.main(now=NOW)

    assert len(calls) == 1  # second run hits the on-disk cache
    assert len(posted) == 2  # both runs still send -- no delta suppression

    with open(dc.HOME_COORDS_FILE) as f:
        cached = json.load(f)
    assert cached["address"] == "123 Home St, Philadelphia, PA"
    assert cached["lat"] == 40.0
    assert cached["lon"] == -75.0


def test_home_coords_cache_file_mode_0600(env, posted, monkeypatch):
    dc = env["dc"]
    monkeypatch.setenv("BAZA_HOME_ADDRESS", "123 Home St")
    monkeypatch.setattr(dc, "geocode", lambda addr: (40.0, -75.0))
    monkeypatch.setattr(dc, "_fetch_osrm", lambda *a, **k: None)

    conn = _biz_conn(env)
    insert_project(conn, "site-perm", status="In Progress", latitude=40.1, longitude=-75.1)
    conn.close()

    dc.main(now=NOW)

    mode = stat.S_IMODE(os.stat(dc.HOME_COORDS_FILE).st_mode)
    assert mode == 0o600


def test_home_coords_cache_invalidated_on_address_change(env, posted, monkeypatch):
    dc = env["dc"]
    calls = []

    def fake_geocode(addr):
        calls.append(addr)
        return (40.0, -75.0) if addr == "Address A" else (41.0, -76.0)
    monkeypatch.setattr(dc, "geocode", fake_geocode)
    monkeypatch.setattr(dc, "_fetch_osrm", lambda *a, **k: None)

    conn = _biz_conn(env)
    insert_project(conn, "site-x", status="In Progress", latitude=40.1, longitude=-75.1)
    conn.close()

    monkeypatch.setenv("BAZA_HOME_ADDRESS", "Address A")
    dc.main(now=NOW)
    assert calls == ["Address A"]

    monkeypatch.setenv("BAZA_HOME_ADDRESS", "Address B")
    dc.main(now=NOW)
    assert calls == ["Address A", "Address B"]  # address change -> cache miss -> re-geocode

    with open(dc.HOME_COORDS_FILE) as f:
        cached = json.load(f)
    assert cached["address"] == "Address B"


# ── OSRM URL/seam sanity (lon,lat order + User-Agent + timeout) ───────────

def test_osrm_url_uses_lon_lat_order_and_user_agent(env, monkeypatch):
    dc = env["dc"]
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(_osrm_ok(100, 100)).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = req.headers
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(dc.urllib.request, "urlopen", fake_urlopen)

    result = dc._fetch_osrm(40.0, -75.0, 41.0, -76.0)  # lat1, lon1, lat2, lon2

    assert result == _osrm_ok(100, 100)
    assert captured["url"] == (
        "https://router.project-osrm.org/route/v1/driving/"
        "-75.0,40.0;-76.0,41.0?overview=false"
    )
    assert captured["timeout"] == dc.OSRM_TIMEOUT
    assert any(k.lower() == "user-agent" for k in captured["headers"])
    ua_key = [k for k in captured["headers"] if k.lower() == "user-agent"][0]
    assert captured["headers"][ua_key] == dc.USER_AGENT


def test_osrm_fetch_returns_none_on_exception(env, monkeypatch):
    dc = env["dc"]

    def fake_urlopen(req, timeout=None):
        raise TimeoutError("boom")

    monkeypatch.setattr(dc.urllib.request, "urlopen", fake_urlopen)
    assert dc._fetch_osrm(40.0, -75.0, 41.0, -76.0) is None


# ── misc ────────────────────────────────────────────────────────────────

def test_main_is_import_safe_and_standalone(env):
    dc = env["dc"]
    sig = inspect.signature(dc.main)
    assert list(sig.parameters) == ["now"]
    assert sig.parameters["now"].default is None


def test_main_wrapped_in_cron_run_heartbeat(env):
    """Static check: main() wraps its body in cron_run(CRON_NAME) so a run
    is always recorded in cron_health.db's cron_runs table, even on an
    early return (setup nag / no sites)."""
    dc = env["dc"]
    source = inspect.getsource(dc.main)
    assert "cron_run(" in source
    assert dc.CRON_NAME in source or "CRON_NAME" in source


def test_cron_run_heartbeat_recorded_even_on_setup_nag(env, posted):
    """Even the early-return "no home address" path must still record a
    completed (status='ok') cron_runs heartbeat -- cron_run() wraps the
    whole body, not just the happy path."""
    dc = env["dc"]
    chdb = env["chdb"]

    dc.main(now=NOW)

    runs = chdb.recent_runs(cron_name=dc.CRON_NAME)
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"


def test_special_char_address_not_pre_escaped(env, posted, monkeypatch):
    """Regression: '&' in an address must reach send_report raw -- md_to_html
    escapes downstream; pre-escaping here double-escaped to '&amp;amp;'."""
    dc = env["dc"]
    monkeypatch.setenv("BAZA_HOME_ADDRESS", "123 Home St, Philadelphia, PA")
    monkeypatch.setattr(dc, "geocode", lambda addr: (40.0, -75.0))

    conn = _biz_conn(env)
    insert_project(conn, "site-amp", title="Jones & Sons", address="5th Ave & Main St",
                    status="In Progress", latitude=40.2, longitude=-75.2)
    conn.close()

    monkeypatch.setattr(dc, "_fetch_osrm", lambda *a, **k: _osrm_ok(1920, 28968.19))

    dc.main(now=NOW)

    assert len(posted) == 1
    text = posted[0]["text"]
    assert "5th Ave & Main St (Jones & Sons)" in text
    assert "&amp;" not in text
