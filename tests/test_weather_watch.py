"""Tests for core/weather_profile.py + agents/duke_harmon/crons/weather_watch.py
(Task 5 of the cron-improvements plan).

All external calls are mocked: forecast/alerts (core.weather_sources),
LLM (agents.cron_helpers.ollama_generate), and Telegram delivery
(core.telegram_fmt.post_html, the single seam both send_alert() and
send_telegram() funnel through). DBs are tmp SQLite files -- the business
DB's ahb_projects DDL is copied verbatim from `sqlite3 dashboard/baza_projects.db
".schema ahb_projects"` (same approach as tests/test_geocode.py), and
cron_health.db is pointed at a tmp path via BAZA_CRON_HEALTH_DB + a fresh
reimport (mirrors tests/test_cron_helpers_routing.py's `ch` fixture).
"""
import datetime
import importlib
import json
import os
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
    payment_terms TEXT DEFAULT '',
    weather_profile TEXT
);
"""
# Note: the real production ahb_projects table (as of this task) does NOT yet
# have `weather_profile` -- it's added by core.weather_profile.ensure_weather_profile_column()
# (an idempotent ALTER TABLE), which both weather_watch.main() and this test
# file's fixtures call before relying on the column. It's included directly in
# this copy of the DDL (rather than requiring every fixture to call the ALTER
# TABLE first) purely so insert_project()'s INSERT below can populate it;
# test_profile_ensure_column_adds_when_missing separately exercises the actual
# ALTER TABLE codepath against a table that's missing the column entirely.


def insert_project(conn, id, title="Test Job", address="1 Test St, Philadelphia, PA",
                    status="In Progress", start_date=None, latitude=40.1, longitude=-75.1,
                    scope="Deck rebuild", description="Exterior deck rebuild with new concrete footings",
                    weather_profile=None):
    conn.execute(
        "INSERT INTO ahb_projects (id, title, address, status, start_date, latitude, longitude, "
        "scope, description, weather_profile) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (id, title, address, status, start_date, latitude, longitude, scope, description, weather_profile),
    )
    conn.commit()


# ── core/weather_profile.py tests: standalone, no cron_health_db needed ─────

@pytest.fixture()
def profile_conn(tmp_path):
    db_path = tmp_path / "profile_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(AHB_PROJECTS_DDL)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture()
def ollama(monkeypatch):
    """Patch agents.cron_helpers.ollama_generate -- core.weather_profile deferred-
    imports it fresh on every call, so patching the attribute on the already-
    imported module is enough (no reimport dance needed here)."""
    import agents.cron_helpers as ch
    calls = []

    def _set(fn):
        def wrapped(*a, **k):
            calls.append(a)
            return fn(*a, **k)
        monkeypatch.setattr(ch, "ollama_generate", wrapped)

    return calls, _set


def test_profile_cached_no_llm_second_call(profile_conn, ollama, monkeypatch):
    from core import weather_profile as wp

    calls, set_ollama = ollama
    set_ollama(lambda *a, **k: '{"exterior": true, "trades": ["concrete"]}')

    insert_project(profile_conn, "p1", weather_profile=None)
    row = profile_conn.execute("SELECT * FROM ahb_projects WHERE id='p1'").fetchone()

    profile1 = wp.get_weather_profile(profile_conn, row)
    assert profile1 == {"exterior": True, "trades": ["concrete"]}
    assert len(calls) == 1

    # Cached to the column -> re-fetch the row and call again; must NOT hit the LLM.
    row2 = profile_conn.execute("SELECT * FROM ahb_projects WHERE id='p1'").fetchone()
    assert row2["weather_profile"] is not None

    def boom(*a, **k):
        raise AssertionError("ollama_generate should not be called on a cache hit")
    import agents.cron_helpers as ch
    monkeypatch.setattr(ch, "ollama_generate", boom)

    profile2 = wp.get_weather_profile(profile_conn, row2)
    assert profile2 == {"exterior": True, "trades": ["concrete"]}
    assert len(calls) == 1  # still just the one LLM call from before


def test_profile_llm_garbage_falls_back(profile_conn, ollama):
    from core import weather_profile as wp
    from core.weather_rules import default_profile

    _, set_ollama = ollama
    set_ollama(lambda *a, **k: "I'm not going to give you JSON, sorry.")

    insert_project(profile_conn, "p2", weather_profile=None)
    row = profile_conn.execute("SELECT * FROM ahb_projects WHERE id='p2'").fetchone()

    profile = wp.get_weather_profile(profile_conn, row)
    assert profile == default_profile()

    # Failure must NOT be cached -- column stays empty so the next run retries.
    row2 = profile_conn.execute("SELECT weather_profile FROM ahb_projects WHERE id='p2'").fetchone()
    assert row2["weather_profile"] is None


def test_profile_llm_unavailable_falls_back(profile_conn, ollama):
    """ollama_generate itself raising (Ollama down) must also fall back cleanly."""
    from core import weather_profile as wp
    from core.weather_rules import default_profile

    _, set_ollama = ollama

    def raise_it(*a, **k):
        raise RuntimeError("ollama down")
    set_ollama(raise_it)

    insert_project(profile_conn, "p3", weather_profile=None)
    row = profile_conn.execute("SELECT * FROM ahb_projects WHERE id='p3'").fetchone()
    assert wp.get_weather_profile(profile_conn, row) == default_profile()


def test_profile_ensure_column_idempotent(profile_conn):
    from core import weather_profile as wp
    # weather_profile already exists in the DDL above -- calling twice must not raise.
    wp.ensure_weather_profile_column(profile_conn)
    wp.ensure_weather_profile_column(profile_conn)
    cols = {r[1] for r in profile_conn.execute("PRAGMA table_info(ahb_projects)").fetchall()}
    assert "weather_profile" in cols


def test_profile_ensure_column_adds_when_missing(tmp_path):
    from core import weather_profile as wp
    # Minimal table genuinely missing the column, to prove the ALTER TABLE path fires.
    conn = sqlite3.connect(str(tmp_path / "nocol.db"))
    conn.execute("CREATE TABLE ahb_projects (id TEXT PRIMARY KEY, title TEXT)")
    conn.commit()
    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(ahb_projects)").fetchall()}
    assert "weather_profile" not in cols_before

    wp.ensure_weather_profile_column(conn)
    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(ahb_projects)").fetchall()}
    assert "weather_profile" in cols_after
    conn.close()


# ── weather_watch.py main() integration tests ───────────────────────────────

def _make_forecast(source="nws", daily=None, hourly=None):
    return {"source": source, "daily": daily or [], "hourly": hourly or []}


def make_day(date, high_f=80, low_f=60, precip_prob_max=0, precip_in=0.0,
             wind_mph=5, gust_mph=10, conditions="Clear"):
    return {
        "date": date, "high_f": high_f, "low_f": low_f,
        "precip_prob_max": precip_prob_max, "precip_in": precip_in,
        "wind_mph": wind_mph, "gust_mph": gust_mph, "conditions": conditions,
    }


def make_hour(ts, temp_f=75, rh=50, precip_prob=0, wind_mph=5, gust_mph=10):
    return {
        "ts": ts, "temp_f": temp_f, "rh": rh,
        "precip_prob": precip_prob, "wind_mph": wind_mph, "gust_mph": gust_mph,
    }


TODAY = "2026-07-02"
NOW = datetime.datetime(2026, 7, 2, 9, 0, 0)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh core.cron_health_db (tmp path) + agents.cron_helpers (business DB_PATH
    pointed at a tmp file, quiet hours forced off) + a fresh
    agents.duke_harmon.crons.weather_watch import, so its module-level
    `chdb`/`get_db`/`send_alert`/etc. bindings all resolve against this
    test's tmp state. Mirrors tests/test_cron_helpers_routing.py's `ch` fixture."""
    ch_db_path = tmp_path / "cron_health.db"
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", str(ch_db_path))

    for mod in (
        "core.cron_health_db",
        "agents.cron_helpers",
        "agents.duke_harmon.crons.weather_watch",
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

    ww = importlib.import_module("agents.duke_harmon.crons.weather_watch")

    return {"ww": ww, "ch": ch, "chdb": chdb, "biz_db": str(biz_db_path)}


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


def test_main_alerts_on_heat_for_exterior_site(env, posted, monkeypatch):
    ww = env["ww"]
    conn = _biz_conn(env)
    insert_project(conn, "site-heat", status="In Progress")
    conn.close()

    forecast = _make_forecast(daily=[make_day(TODAY)],
                               hourly=[make_hour(f"{TODAY}T14:00:00-04:00", temp_f=100, rh=65)])
    monkeypatch.setattr(ww, "get_forecast", lambda lat, lon: forecast)
    monkeypatch.setattr(ww, "get_active_alerts", lambda lat, lon: [])
    monkeypatch.setattr(ww, "get_weather_profile", lambda conn, site: {"exterior": True, "trades": []})

    ww.main(now=NOW)

    assert len(posted) == 1
    assert "Heat" in posted[0]["text"]
    assert "site-heat" not in posted[0]["text"]  # human label, not raw id
    assert "1 Test St" in posted[0]["text"]


def test_main_skips_interior_rain(env, posted, monkeypatch):
    ww = env["ww"]
    conn = _biz_conn(env)
    insert_project(conn, "site-interior", status="In Progress")
    conn.close()

    forecast = _make_forecast(daily=[make_day(TODAY, precip_in=0.5)],
                               hourly=[make_hour(f"{TODAY}T09:00:00-04:00", precip_prob=80)])
    monkeypatch.setattr(ww, "get_forecast", lambda lat, lon: forecast)
    monkeypatch.setattr(ww, "get_active_alerts", lambda lat, lon: [])
    monkeypatch.setattr(ww, "get_weather_profile", lambda conn, site: {"exterior": False, "trades": []})

    ww.main(now=NOW)

    # No hazards at all for an interior-only site given only rain conditions.
    assert len(posted) == 0


def test_main_nag_missing_address_dedup(env, posted, monkeypatch):
    ww = env["ww"]
    conn = _biz_conn(env)
    insert_project(conn, "site-noaddr", status="In Progress", address=None,
                    latitude=None, longitude=None)
    conn.close()

    monkeypatch.setattr(ww, "ensure_project_coords", lambda conn, pid: None)
    monkeypatch.setattr(ww, "get_forecast", lambda lat, lon: (_ for _ in ()).throw(
        AssertionError("get_forecast should not be called for a site with no coords")))

    ww.main(now=NOW)
    ww.main(now=NOW)

    assert len(posted) == 1
    assert "address" in posted[0]["text"].lower()


def test_ledger_row_upserted(env, posted, monkeypatch):
    ww = env["ww"]
    conn = _biz_conn(env)
    insert_project(conn, "site-ledger", status="In Progress")
    conn.close()

    forecast = _make_forecast(daily=[make_day(TODAY, high_f=91, low_f=68, precip_in=0.0,
                                               wind_mph=8, gust_mph=14, conditions="Sunny")],
                               hourly=[])
    monkeypatch.setattr(ww, "get_forecast", lambda lat, lon: forecast)
    monkeypatch.setattr(ww, "get_active_alerts", lambda lat, lon: [])
    monkeypatch.setattr(ww, "get_weather_profile", lambda conn, site: {"exterior": True, "trades": []})

    ww.main(now=NOW)

    conn = _biz_conn(env)
    rows = conn.execute("SELECT * FROM weather_observations WHERE project_id='site-ledger'").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["obs_date"] == TODAY
    assert row["temp_high_f"] == 91
    assert row["temp_low_f"] == 68
    assert row["precip_in"] == 0.0
    assert row["wind_max_mph"] == 8
    assert row["gust_max_mph"] == 14
    assert row["conditions"] == "Sunny"
    assert row["source"] == "nws"
    conn.close()

    # Second run with updated actuals -> UPSERT, not a second row.
    forecast2 = _make_forecast(daily=[make_day(TODAY, high_f=95, low_f=70)], hourly=[])
    monkeypatch.setattr(ww, "get_forecast", lambda lat, lon: forecast2)
    ww.main(now=NOW)

    conn = _biz_conn(env)
    rows2 = conn.execute("SELECT * FROM weather_observations WHERE project_id='site-ledger'").fetchall()
    assert len(rows2) == 1
    assert rows2[0]["temp_high_f"] == 95
    conn.close()


def test_site_fetch_dedup(env, posted, monkeypatch):
    ww = env["ww"]
    conn = _biz_conn(env)
    # Two projects, same rounded coords.
    insert_project(conn, "site-a", status="In Progress", latitude=40.101, longitude=-75.102)
    insert_project(conn, "site-b", status="In Progress", latitude=40.104, longitude=-75.099)
    conn.close()

    forecast = _make_forecast(daily=[make_day(TODAY)], hourly=[])
    calls = {"forecast": 0, "alerts": 0}

    def fake_forecast(lat, lon):
        calls["forecast"] += 1
        return forecast

    def fake_alerts(lat, lon):
        calls["alerts"] += 1
        return []

    monkeypatch.setattr(ww, "get_forecast", fake_forecast)
    monkeypatch.setattr(ww, "get_active_alerts", fake_alerts)
    monkeypatch.setattr(ww, "get_weather_profile", lambda conn, site: {"exterior": True, "trades": []})

    ww.main(now=NOW)

    assert calls["forecast"] == 1
    assert calls["alerts"] == 1


def test_alert_dedup_across_runs(env, posted, monkeypatch):
    ww = env["ww"]
    conn = _biz_conn(env)
    insert_project(conn, "site-dedup", status="In Progress")
    conn.close()

    forecast = _make_forecast(daily=[make_day(TODAY)],
                               hourly=[make_hour(f"{TODAY}T14:00:00-04:00", temp_f=100, rh=65)])
    monkeypatch.setattr(ww, "get_forecast", lambda lat, lon: forecast)
    monkeypatch.setattr(ww, "get_active_alerts", lambda lat, lon: [])
    monkeypatch.setattr(ww, "get_weather_profile", lambda conn, site: {"exterior": True, "trades": []})

    ww.main(now=NOW)
    ww.main(now=NOW)

    assert len(posted) == 1  # renotify_hours=24 keeps the second run's identical hazard deduped


def test_main_fyi_combined_report(env, posted, monkeypatch):
    """A far-day (fyi-severity) hazard is collected and sent as one combined
    report, not an individual send_alert."""
    ww = env["ww"]
    conn = _biz_conn(env)
    insert_project(conn, "site-fyi", status="In Progress")
    conn.close()

    daily = [make_day(TODAY), make_day("2026-07-03"), make_day("2026-07-04", wind_mph=25)]
    forecast = _make_forecast(daily=daily, hourly=[])
    monkeypatch.setattr(ww, "get_forecast", lambda lat, lon: forecast)
    monkeypatch.setattr(ww, "get_active_alerts", lambda lat, lon: [])
    monkeypatch.setattr(ww, "get_weather_profile", lambda conn, site: {"exterior": True, "trades": []})

    ww.main(now=NOW)

    assert len(posted) == 1
    assert "Weather FYI" in posted[0]["text"]
    assert "Wind" in posted[0]["text"]


def test_main_all_clear_notification(env, posted, monkeypatch):
    """Simplified all-clear (controller-approved approximation): a
    previously alert-severity NWS hazard for a site, unacked and not
    reconfirmed active in >6h, whose event no longer appears in
    get_active_alerts() -> one all-clear line for that site."""
    ww = env["ww"]
    chdb = env["chdb"]
    conn = _biz_conn(env)
    insert_project(conn, "site-clear", status="In Progress")
    conn.close()

    # Seed a prior NWS alert-state row for this site, backdated past the
    # ALL_CLEAR_STALE_HOURS window and unacknowledged.
    key = "weather:site-clear:nws:Heat Advisory:2026-07-01"
    chdb.should_alert(key, renotify_hours=24, meta={"title": "prior heat advisory"})
    backdated = (NOW - datetime.timedelta(hours=8)).isoformat(timespec="seconds")
    with chdb.connect() as c:
        c.execute("UPDATE cron_alert_state SET last_seen = ? WHERE key = ?", (backdated, key))
        c.commit()

    forecast = _make_forecast(daily=[make_day(TODAY)], hourly=[])
    monkeypatch.setattr(ww, "get_forecast", lambda lat, lon: forecast)
    monkeypatch.setattr(ww, "get_active_alerts", lambda lat, lon: [])  # advisory has cleared
    monkeypatch.setattr(ww, "get_weather_profile", lambda conn, site: {"exterior": True, "trades": []})

    ww.main(now=NOW)

    all_clear_msgs = [c["text"] for c in posted if "All clear" in c["text"]]
    assert len(all_clear_msgs) == 1
    assert "Heat Advisory" in all_clear_msgs[0]


def test_main_all_clear_acked_not_resent_on_next_run(env, posted, monkeypatch):
    """Regression (Blocker B3): _check_all_clear used to never ack the
    cleared nws: row, and the all-clear alert_key is date-scoped -- so every
    expired NWS alert re-sent "All clear" on every subsequent run, forever.
    Two consecutive runs (same day) after the alert has expired must produce
    exactly one all-clear total, not one per run."""
    ww = env["ww"]
    chdb = env["chdb"]
    conn = _biz_conn(env)
    insert_project(conn, "site-clear2", status="In Progress")
    conn.close()

    key = "weather:site-clear2:nws:Heat Advisory:2026-07-01"
    chdb.should_alert(key, renotify_hours=24, meta={"title": "prior heat advisory"})
    backdated = (NOW - datetime.timedelta(hours=8)).isoformat(timespec="seconds")
    with chdb.connect() as c:
        c.execute("UPDATE cron_alert_state SET last_seen = ? WHERE key = ?", (backdated, key))
        c.commit()

    forecast = _make_forecast(daily=[make_day(TODAY)], hourly=[])
    monkeypatch.setattr(ww, "get_forecast", lambda lat, lon: forecast)
    monkeypatch.setattr(ww, "get_active_alerts", lambda lat, lon: [])  # advisory has cleared
    monkeypatch.setattr(ww, "get_weather_profile", lambda conn, site: {"exterior": True, "trades": []})

    ww.main(now=NOW)
    ww.main(now=NOW)

    all_clear_msgs = [c["text"] for c in posted if "All clear" in c["text"]]
    assert len(all_clear_msgs) == 1

    # The originally-alerted row is now acked -- proves the fix, not just
    # its symptom (no second send).
    with chdb.connect() as c:
        row = c.execute(
            "SELECT acked_at FROM cron_alert_state WHERE key = ?", (key,)
        ).fetchone()
    assert row["acked_at"] is not None


def test_main_no_sites_is_noop(env, posted):
    ww = env["ww"]
    # No projects inserted at all -- ahb_projects is empty.
    ww.main(now=NOW)
    assert len(posted) == 0


def test_main_is_import_safe_and_standalone(env):
    """main(now=None) exists and importing the module has no side effects
    (already proven implicitly by the `env` fixture's fresh import, but
    assert explicitly that calling main() with an explicit `now` doesn't
    require any live network/LLM -- everything here is mocked away by the
    other tests; this just checks the signature/defaults)."""
    ww = env["ww"]
    import inspect
    sig = inspect.signature(ww.main)
    assert list(sig.parameters) == ["now"]
    assert sig.parameters["now"].default is None


# ── Fix round 1 regression tests ─────────────────────────────────────────────

def test_main_no_double_html_escape_on_ampersand(env, posted, monkeypatch):
    """Regression: _format_hazard_line used to html.escape() interpolated
    text before handing it to send_alert() -> post_html(already_html=False)
    -> md_to_html(), which HTML-escapes the whole message itself. That
    double-escaped a raw "&" into "&amp;" (which then rendered literally as
    "&amp;" in Telegram instead of "&"). `posted[]` records the text exactly
    as passed into post_html -- i.e. pre-md_to_html -- so a site titled
    "Jones & Sons" must show up with a raw, single-escaped "&" and no
    "&amp;" anywhere in the captured message."""
    ww = env["ww"]
    conn = _biz_conn(env)
    insert_project(conn, "site-amp", status="In Progress",
                    address="1 Test St, Philadelphia, PA", title="Jones & Sons")
    conn.close()

    forecast = _make_forecast(daily=[make_day(TODAY)],
                               hourly=[make_hour(f"{TODAY}T14:00:00-04:00", temp_f=100, rh=65)])
    monkeypatch.setattr(ww, "get_forecast", lambda lat, lon: forecast)
    monkeypatch.setattr(ww, "get_active_alerts", lambda lat, lon: [])
    monkeypatch.setattr(ww, "get_weather_profile", lambda conn, site: {"exterior": True, "trades": []})

    ww.main(now=NOW)

    assert len(posted) == 1
    assert "Jones & Sons" in posted[0]["text"]
    assert "&amp;" not in posted[0]["text"]


def test_main_site_selection_start_date_within_lookahead(env, posted, monkeypatch):
    """_get_sites' OR-branch: a site that isn't 'In Progress' (here
    status='estimate') is still picked up when its start_date falls within
    LOOKAHEAD_DAYS (7d) of `now` -- weather coverage lined up before day
    one -- while one whose start_date is further out (9d) is excluded.
    Exercised at the main()-level (not by calling _get_sites directly) via
    the weather_observations ledger side effect: a row is upserted only for
    a site that _run() actually iterated."""
    ww = env["ww"]
    conn = _biz_conn(env)
    within = (NOW.date() + datetime.timedelta(days=3)).isoformat()
    outside = (NOW.date() + datetime.timedelta(days=9)).isoformat()
    insert_project(conn, "site-soon", status="estimate", start_date=within)
    insert_project(conn, "site-later", status="estimate", start_date=outside)
    conn.close()

    forecast = _make_forecast(daily=[make_day(TODAY)], hourly=[])
    monkeypatch.setattr(ww, "get_forecast", lambda lat, lon: forecast)
    monkeypatch.setattr(ww, "get_active_alerts", lambda lat, lon: [])
    monkeypatch.setattr(ww, "get_weather_profile", lambda conn, site: {"exterior": True, "trades": []})

    ww.main(now=NOW)

    conn = _biz_conn(env)
    seen = {r["project_id"] for r in conn.execute(
        "SELECT project_id FROM weather_observations").fetchall()}
    conn.close()
    assert "site-soon" in seen
    assert "site-later" not in seen
