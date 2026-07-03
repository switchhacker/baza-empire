"""Tests for Task 6 of the cron-improvements plan:

- skills/shared/weather_history.py -- the rain-day ledger report skill
  (weather-delay evidence for client disputes).
- agents/duke_harmon/crons/deadline_enforcer.py's build_weather_lookahead(conn)
  -- the 7-day weather lookahead appended to Duke's morning deadline cron.

All external data (core.weather_sources.get_forecast) is monkeypatched; no
live network calls. Each test seeds its own tmp sqlite DB.
"""
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WEATHER_HISTORY_PATH = os.path.join(ROOT, "skills", "shared", "weather_history.py")
DEADLINE_ENFORCER_PATH = os.path.join(ROOT, "agents", "duke_harmon", "crons", "deadline_enforcer.py")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Exact DDL from the task-6 brief -- weather_observations is created by a
# parallel task (weather_watch cron); the skill must also create-if-missing
# with this same DDL so it never crashes on a fresh DB.
WEATHER_OBS_DDL = """
CREATE TABLE IF NOT EXISTS weather_observations (
    id INTEGER PRIMARY KEY, project_id TEXT, obs_date TEXT, lat REAL, lon REAL,
    temp_high_f REAL, temp_low_f REAL, precip_in REAL, wind_max_mph REAL,
    gust_max_mph REAL, conditions TEXT, source TEXT,
    created_at TEXT DEFAULT (datetime('now')), UNIQUE(project_id, obs_date)
)
"""


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def wh():
    """Fresh weather_history module import per test."""
    return _load(WEATHER_HISTORY_PATH, "weather_history_test")


@pytest.fixture()
def de():
    """Fresh deadline_enforcer module import per test."""
    return _load(DEADLINE_ENFORCER_PATH, "deadline_enforcer_test")


# ── skill: weather_history.py ───────────────────────────────────────────

OBS_ROWS = [
    # obs_date,     high, low,  precip, wind, gust, conditions
    ("2026-06-20", 88.0, 65.0, 0.0,  8.0,  15.0, "Sunny"),
    ("2026-06-21", 91.0, 70.0, 0.05, 10.0, 18.0, "Partly Cloudy"),   # hot day only
    ("2026-06-22", 82.0, 60.0, 0.4,  12.0, 20.0, "Rain Showers"),    # rain day
    ("2026-06-23", 75.0, 55.0, 0.0,  22.0, 30.0, "Windy"),           # high-wind (sustained)
    ("2026-06-24", 78.0, 58.0, 0.0,  5.0,  40.0, "Gusty"),           # high-wind (gust)
]


def _seed_observations(db_path, project_id="proj-1", rows=OBS_ROWS):
    conn = sqlite3.connect(db_path)
    conn.execute(WEATHER_OBS_DDL)
    for obs_date, high, low, precip, wind, gust, cond in rows:
        conn.execute(
            "INSERT INTO weather_observations (project_id, obs_date, lat, lon, "
            "temp_high_f, temp_low_f, precip_in, wind_max_mph, gust_max_mph, "
            "conditions, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, obs_date, 40.0, -75.0, high, low, precip, wind, gust, cond, "nws"),
        )
    conn.commit()
    conn.close()


def test_history_table_and_counts(wh, tmp_path):
    """5 seeded rows -> markdown table with all dates + correct rain/wind/hot counts."""
    db_path = str(tmp_path / "t.db")
    _seed_observations(db_path)
    conn = wh.get_conn(db_path)
    try:
        report = wh.build_history_report(conn, "proj-1", "2026-06-20", "2026-06-24")
    finally:
        conn.close()

    # markdown table shape
    assert "| Date | High | Low | Precip | Wind | Gust | Conditions |" in report
    for obs_date, *_ in OBS_ROWS:
        assert obs_date in report
    assert "88°F" in report
    assert "0.40in" in report
    assert "Rain Showers" in report

    # counts: rain day = precip_in>=0.1 OR conditions has rain/storm/shower ->
    #   only 2026-06-22 (precip 0.4, "Rain Showers")
    # high-wind day = wind_max>=20 or gust_max>=35 -> 06-23 (wind 22) and 06-24 (gust 40)
    # >=90 day = temp_high_f>=90 -> only 06-21 (91)
    assert "5 day(s) observed" in report
    assert "1 rain day(s)" in report
    assert "2 high-wind day(s)" in report
    assert "1 day(s) ≥90°F" in report

    # also verify against the per-row predicates directly
    rows = conn = wh.get_conn(db_path)
    try:
        obs = conn.execute(
            "SELECT * FROM weather_observations WHERE project_id='proj-1' ORDER BY obs_date"
        ).fetchall()
    finally:
        conn.close()
    assert sum(1 for r in obs if wh.is_rain_day(r)) == 1
    assert sum(1 for r in obs if wh.is_high_wind_day(r)) == 2
    assert sum(1 for r in obs if wh.is_hot_day(r)) == 1


def test_history_empty_range_message(wh, tmp_path):
    """A date range with no matching observations -> a clear no-data message,
    not an empty/broken table."""
    db_path = str(tmp_path / "t.db")
    _seed_observations(db_path)  # rows are all in June 2026
    conn = wh.get_conn(db_path)
    try:
        report = wh.build_history_report(conn, "proj-1", "2026-01-01", "2026-01-31")
    finally:
        conn.close()

    assert "No weather observations" in report
    assert "proj-1" in report
    assert "2026-01-01" in report and "2026-01-31" in report
    assert "|" not in report  # no table rendered


def test_history_creates_table_missing(wh, tmp_path):
    """Fresh DB with no weather_observations table at all -> the skill
    creates it (per the DDL) rather than crashing."""
    db_path = str(tmp_path / "brand_new.db")
    # DB file doesn't even exist yet -- sqlite3.connect will create an empty file.
    conn = wh.get_conn(db_path)
    try:
        report = wh.build_history_report(conn, "proj-1", "2026-01-01", "2026-01-31")
    finally:
        conn.close()
    assert "No weather observations" in report


def test_main_prints_report_via_skill_args(wh, tmp_path, capsys):
    """End-to-end: main() reads SKILL_ARGS (with the `_db_path` test seam)
    and prints the report to stdout -- the house skill pattern."""
    db_path = str(tmp_path / "t.db")
    _seed_observations(db_path)
    os.environ["SKILL_ARGS"] = json.dumps({
        "project_id": "proj-1", "start": "2026-06-20", "end": "2026-06-24",
        "_db_path": db_path,
    })
    try:
        wh.main()
    finally:
        os.environ.pop("SKILL_ARGS", None)
    out = capsys.readouterr().out
    assert "Weather Ledger" in out
    assert "5 day(s) observed" in out


def test_main_missing_args_reports_error(wh, capsys):
    os.environ["SKILL_ARGS"] = json.dumps({"project_id": "proj-1"})
    try:
        wh.main()
    finally:
        os.environ.pop("SKILL_ARGS", None)
    out = capsys.readouterr().out
    assert "required" in out.lower()


# ── deadline_enforcer.py: build_weather_lookahead ───────────────────────

def _make_projects_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ahb_projects (id TEXT PRIMARY KEY, title TEXT, address TEXT, "
        "latitude REAL, longitude REAL, start_date TEXT, end_date TEXT, status TEXT)"
    )
    return conn


CANNED_DAILY = [
    {"date": "2026-07-06", "high_f": 85, "low_f": 65, "precip_prob_max": 10, "precip_in": 0.0,
     "wind_mph": 5, "gust_mph": 10, "conditions": "Sunny"},                                    # Mon - best
    {"date": "2026-07-07", "high_f": 88, "low_f": 66, "precip_prob_max": 80, "precip_in": 0.3,
     "wind_mph": 15, "gust_mph": 20, "conditions": "Rain Showers"},                             # Tue - rainy
    {"date": "2026-07-08", "high_f": 85, "low_f": 64, "precip_prob_max": 15, "precip_in": 0.0,
     "wind_mph": 6, "gust_mph": 10, "conditions": "Clear"},                                     # Wed - best
    {"date": "2026-07-09", "high_f": 87, "low_f": 67, "precip_prob_max": 20, "precip_in": 0.0,
     "wind_mph": 25, "gust_mph": 30, "conditions": "Windy"},                                    # Thu - windy
    {"date": "2026-07-10", "high_f": 91, "low_f": 70, "precip_prob_max": 25, "precip_in": 0.0,
     "wind_mph": 8, "gust_mph": 12, "conditions": "Hot"},                                       # Fri - hot
    {"date": "2026-07-11", "high_f": 92, "low_f": 71, "precip_prob_max": 0, "precip_in": 0.0,
     "wind_mph": 3, "gust_mph": 5, "conditions": "Clear"},                                      # Sat - weekend
    {"date": "2026-07-12", "high_f": 93, "low_f": 72, "precip_prob_max": 0, "precip_in": 0.0,
     "wind_mph": 2, "gust_mph": 4, "conditions": "Clear"},                                      # Sun - weekend
]


def test_lookahead_best_days(de, tmp_path, monkeypatch):
    """Canned forecast -> picks the 2 lowest-(precip_prob, wind) weekdays (Mon/Wed),
    renders the compact week line with rain%/icons, and flags the start-date collision."""
    db_path = str(tmp_path / "proj.db")
    conn = _make_projects_conn(db_path)
    conn.execute(
        "INSERT INTO ahb_projects (id, title, address, latitude, longitude, "
        "start_date, end_date, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("p1", "Deck Rebuild", "123 Main St", 40.0, -75.0, "2026-07-07", "2026-08-20", "In Progress"),
    )
    conn.commit()

    monkeypatch.setattr(de, "get_forecast", lambda lat, lon: {"source": "nws", "daily": CANNED_DAILY, "hourly": []})

    result = de.build_weather_lookahead(conn)
    conn.close()

    assert "Deck Rebuild" in result
    # compact week line matches the brief's example shape: "Mon☀️ Tue🌧80% ..."
    assert "Mon☀️" in result
    assert "Tue🌧80%" in result
    assert "Thu💨" in result
    assert "Fri🔥" in result
    # best exterior days = lowest (precip_prob_max, wind_mph) weekdays -> Mon(10,5), Wed(15,6)
    assert "Best exterior days: Mon/Wed" in result
    # start_date (2026-07-07 = Tue, precip_prob_max=80 >= 50) collides
    assert "Start date 2026-07-07 collides" in result
    assert "80%" in result
    # end_date (2026-08-20) isn't in the forecast window -> no end collision line
    assert "End date" not in result


def test_lookahead_no_sites_returns_empty(de, tmp_path):
    """No active sites (empty ahb_projects) -> build_weather_lookahead returns ''."""
    db_path = str(tmp_path / "empty.db")
    conn = _make_projects_conn(db_path)
    conn.commit()

    result = de.build_weather_lookahead(conn)
    conn.close()
    assert result == ""


def test_lookahead_skips_site_without_coords(de, tmp_path, monkeypatch):
    """An active site with no lat/lon can't be forecast -> skipped, not crashed."""
    db_path = str(tmp_path / "proj.db")
    conn = _make_projects_conn(db_path)
    conn.execute(
        "INSERT INTO ahb_projects (id, title, address, latitude, longitude, "
        "start_date, end_date, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("p1", "No Coords Site", "456 Elm St", None, None, "2026-07-07", "2026-08-20", "In Progress"),
    )
    conn.commit()

    calls = []
    monkeypatch.setattr(de, "get_forecast", lambda lat, lon: calls.append((lat, lon)) or None)

    result = de.build_weather_lookahead(conn)
    conn.close()
    assert result == ""
    assert calls == []  # never even tried to fetch a forecast


def test_lookahead_site_selection_start_date_within_week(de, tmp_path):
    """A site that isn't 'In Progress' but starts within the next 7 days still
    counts as active; one that starts further out or has no status doesn't."""
    import datetime
    db_path = str(tmp_path / "proj.db")
    conn = _make_projects_conn(db_path)
    ref = datetime.date(2026, 7, 5)
    upcoming_start = (ref + datetime.timedelta(days=3)).isoformat()
    far_start = (ref + datetime.timedelta(days=30)).isoformat()
    conn.execute(
        "INSERT INTO ahb_projects (id, title, address, latitude, longitude, "
        "start_date, end_date, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("p2", "Upcoming Build", "789 Oak St", 40.0, -75.0, upcoming_start, "2026-09-01", "Planning"),
    )
    conn.execute(
        "INSERT INTO ahb_projects (id, title, address, latitude, longitude, "
        "start_date, end_date, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("p3", "Far Future Build", "1 Far Ave", 40.0, -75.0, far_start, "2026-10-01", "Planning"),
    )
    conn.commit()

    sites = de._active_sites(conn, ref)
    conn.close()
    assert [s["id"] for s in sites] == ["p2"]


def test_lookahead_missing_ahb_projects_table_returns_empty(de, tmp_path):
    """Schema drift (table missing entirely) degrades to '' rather than raising."""
    db_path = str(tmp_path / "no_table.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    result = de.build_weather_lookahead(conn)
    conn.close()
    assert result == ""


def _insert_site(conn, id_, title, address, lat, lon, start_date, status="In Progress"):
    conn.execute(
        "INSERT INTO ahb_projects (id, title, address, latitude, longitude, "
        "start_date, end_date, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (id_, title, address, lat, lon, start_date, "2026-08-20", status),
    )


def test_lookahead_dedupes_same_coord_sites(de, tmp_path, monkeypatch):
    """Two active sites sharing the same rounded (lat, lon) -> get_forecast is
    called exactly once, not once per site (review finding: redundant serial
    HTTP fetches for sites sharing coordinates)."""
    db_path = str(tmp_path / "proj.db")
    conn = _make_projects_conn(db_path)
    _insert_site(conn, "p1", "Site A", "1 A St", 40.0, -75.0, "2026-07-07")
    # Rounds to the same (40.0, -75.0) key as p1.
    _insert_site(conn, "p2", "Site B", "2 B St", 40.001, -75.001, "2026-07-08")
    conn.commit()

    calls = []

    def fake_get_forecast(lat, lon):
        calls.append((lat, lon))
        return {"source": "nws", "daily": CANNED_DAILY, "hourly": []}

    monkeypatch.setattr(de, "get_forecast", fake_get_forecast)

    result = de.build_weather_lookahead(conn)
    conn.close()

    assert "Site A" in result
    assert "Site B" in result
    assert len(calls) == 1


def test_lookahead_caps_at_eight_sites(de, tmp_path, monkeypatch):
    """More than MAX_LOOKAHEAD_SITES active sites -> only the first 8 (sorted
    by title) are rendered, plus a truncation trailer naming how many were
    dropped (review finding: unbounded message length as project count grows)."""
    db_path = str(tmp_path / "proj.db")
    conn = _make_projects_conn(db_path)
    for i in range(10):
        title = f"Site {chr(ord('A') + i)}"  # Site A..Site J, alphabetical
        _insert_site(conn, f"p{i}", title, f"{i} Main St", 40.0 + i, -75.0 - i, "2026-07-07")
    conn.commit()

    monkeypatch.setattr(
        de, "get_forecast",
        lambda lat, lon: {"source": "nws", "daily": CANNED_DAILY, "hourly": []},
    )

    result = de.build_weather_lookahead(conn)
    conn.close()

    for letter in "ABCDEFGH":
        assert f"Site {letter}" in result
    for letter in "IJ":
        assert f"Site {letter}" not in result
    assert "… and 2 more sites" in result


# ── cron_run wrapping / py_compile sanity ───────────────────────────────

def test_main_wrapped_in_cron_run():
    """main() in deadline_enforcer.py source calls cron_run('deadline_enforcer')."""
    with open(DEADLINE_ENFORCER_PATH) as f:
        src = f.read()
    assert 'cron_run("deadline_enforcer")' in src
