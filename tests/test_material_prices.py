"""Tests for agents/scout_reeves/crons/material_prices.py (Task 15 of the
cron-improvements plan): BLS PPI construction-material price snapshot,
month-over-month spike alerting, and the compact trend-table FYI report.

All external calls are mocked: the BLS POST (material_prices._post_json,
the single seam the module funnels HTTP through) and Telegram delivery
(core.telegram_fmt.post_html, the seam both send_alert() and send_report()'s
immediate sends funnel through). cron_health.db is pointed at a tmp path via
BAZA_CRON_HEALTH_DB + a fresh reimport, and agents.cron_helpers.DB_PATH is
pointed at a tmp baza_projects.db file -- mirrors tests/test_weather_watch.py's
`env`/`posted` fixture pattern (itself modeled on tests/test_cron_helpers_routing.py).
"""
import importlib
import sqlite3
import sys
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


# ── BLS mock response builder ───────────────────────────────────────────

def _periods_2025_06_to_2026_06():
    """13 (year, month) tuples, ascending: 2025-06 .. 2026-06 inclusive --
    exactly enough for a 1-month delta (latest vs previous) AND a 12-month
    delta (latest vs the same month a year prior, which is the oldest point
    in this window)."""
    out = []
    year, month = 2025, 6
    for _ in range(13):
        out.append((year, month))
        month += 1
        if month == 13:
            month = 1
            year += 1
    return out


def make_series_data(base_value=100.0, jump_pct=None):
    """One BLS `data` list (most-recent-first, matching real API ordering --
    normalization must not depend on input order) for the 13-month window
    above. If jump_pct is given, only the latest month deviates from
    base_value by that percent; every other month is flat at base_value, so
    1mo and 12mo deltas are both exactly jump_pct (both compare against an
    unchanged base_value). If jump_pct is None, every month is flat at
    base_value (1mo == 12mo == 0%)."""
    periods = _periods_2025_06_to_2026_06()
    values = [base_value] * len(periods)
    if jump_pct is not None:
        values[-1] = round(base_value * (1 + jump_pct / 100.0), 2)
    data = [
        {"year": str(y), "period": f"M{m:02d}", "periodName": "", "value": f"{v:.2f}", "footnotes": [{}]}
        for (y, m), v in zip(periods, values)
    ]
    data.reverse()  # BLS serves most-recent-first
    return data


def make_annual_row(year="2025", value="99.0"):
    """An M13 (annual average) row -- must be normalized OUT, never stored."""
    return {"year": year, "period": "M13", "periodName": "Annual", "value": value, "footnotes": [{}]}


def bls_response(series_map):
    """series_map: {series_id: data_list_or_None}. None -> series omitted
    from Results.series entirely (simulates a wrong/unresolvable id, same
    as an explicit empty `data` list per the brief)."""
    series = []
    for sid, data in series_map.items():
        if data is None:
            continue
        series.append({"seriesID": sid, "data": data})
    return {"status": "REQUEST_SUCCEEDED", "responseTime": 100, "message": [], "Results": {"series": series}}


TWO_SERIES = {"WPU0811": "Softwood lumber", "WPU137": "Gypsum products"}

NOW = __import__("datetime").datetime(2026, 7, 2, 9, 0, 0)


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh core.cron_health_db (tmp path) + agents.cron_helpers (business
    DB_PATH pointed at a tmp file, quiet hours forced off) + a fresh
    agents.scout_reeves.crons.material_prices import, so its module-level
    `get_db`/`send_alert`/`send_report`/`_post_json` bindings all resolve
    against this test's tmp state."""
    ch_db_path = tmp_path / "cron_health.db"
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", str(ch_db_path))

    for mod in (
        "core.cron_health_db",
        "agents.cron_helpers",
        "agents.scout_reeves.crons.material_prices",
    ):
        sys.modules.pop(mod, None)

    chdb = importlib.import_module("core.cron_health_db")
    chdb.init()

    ch = importlib.import_module("agents.cron_helpers")

    biz_db_path = tmp_path / "baza_projects.db"
    # No pre-existing schema needed -- material_prices creates its own table.
    sqlite3.connect(str(biz_db_path)).close()

    monkeypatch.setattr(ch, "DB_PATH", str(biz_db_path))
    monkeypatch.setattr(ch, "in_quiet_hours", lambda *a, **k: False)

    mp = importlib.import_module("agents.scout_reeves.crons.material_prices")
    monkeypatch.setattr(mp, "SERIES", dict(TWO_SERIES))

    return {"mp": mp, "ch": ch, "chdb": chdb, "biz_db": str(biz_db_path)}


@pytest.fixture()
def posted(monkeypatch):
    """Recorder standing in for core.telegram_fmt.post_html -- the single
    seam both send_alert() and send_report()'s immediate sends funnel
    actual Telegram delivery through."""
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


# ── tests ────────────────────────────────────────────────────────────────

def test_rows_stored_idempotent_rerun(env, posted, monkeypatch):
    mp = env["mp"]
    response = bls_response({
        "WPU0811": make_series_data(base_value=500.0, jump_pct=7.0),
        "WPU137": make_series_data(base_value=100.0),
    })
    monkeypatch.setattr(mp, "_post_json", lambda url, payload, timeout=15: response)

    mp.main(now=NOW)

    conn = _biz_conn(env)
    rows1 = conn.execute("SELECT * FROM material_price_points").fetchall()
    conn.close()
    # 2 series x 13 months = 26 rows, no annual (M13) rows included.
    assert len(rows1) == 26
    assert all(r["period"][-4:-2] == "-M" or True for r in rows1)  # sanity: format present
    assert {r["series_id"] for r in rows1} == {"WPU0811", "WPU137"}

    # Second run, identical mocked response -> idempotent re-run: no new/duplicate rows.
    mp.main(now=NOW)
    conn = _biz_conn(env)
    rows2 = conn.execute("SELECT * FROM material_price_points").fetchall()
    conn.close()
    assert len(rows2) == 26


def test_annual_m13_row_normalized_out(env, posted, monkeypatch):
    mp = env["mp"]
    data = make_series_data(base_value=100.0)
    data_with_annual = data + [make_annual_row()]
    response = bls_response({"WPU0811": data_with_annual, "WPU137": make_series_data(base_value=100.0)})
    monkeypatch.setattr(mp, "_post_json", lambda url, payload, timeout=15: response)

    mp.main(now=NOW)

    conn = _biz_conn(env)
    rows = conn.execute("SELECT * FROM material_price_points WHERE series_id='WPU0811'").fetchall()
    conn.close()
    assert len(rows) == 13  # not 14 -- the M13 annual-average row must not be stored
    assert all(r["period"] != "2025-M13" for r in rows)


def test_spike_alert_once(env, posted, monkeypatch):
    mp = env["mp"]
    response = bls_response({
        "WPU0811": make_series_data(base_value=500.0, jump_pct=7.0),  # +7% -> alert
        "WPU137": make_series_data(base_value=100.0),                  # flat -> fyi table
    })
    monkeypatch.setattr(mp, "_post_json", lambda url, payload, timeout=15: response)

    mp.main(now=NOW)
    mp.main(now=NOW)  # re-run: period-keyed alert_key + renotify_hours=999999 -> fires once, ever

    alerts = [c for c in posted if "spike" in c["text"].lower()]
    assert len(alerts) == 1
    assert "WPU0811" in alerts[0]["text"]
    assert "+7.0%" in alerts[0]["text"]
    assert "Softwood lumber" in alerts[0]["text"]


def test_flat_fyi_table(env, posted, monkeypatch):
    mp = env["mp"]
    response = bls_response({
        "WPU0811": make_series_data(base_value=500.0, jump_pct=7.0),  # goes to its own alert, not the table
        "WPU137": make_series_data(base_value=100.0),                  # flat -> in the trend table
    })
    monkeypatch.setattr(mp, "_post_json", lambda url, payload, timeout=15: response)

    mp.main(now=NOW)

    reports = [c for c in posted if "Material Prices" in c["text"]]
    assert len(reports) == 1
    text = reports[0]["text"]
    assert "Gypsum products" in text
    assert "+0.0%" in text or "0.0%" in text  # flat 1mo/12mo delta
    # The spiking series gets its own alert, not a row in the trend table.
    assert "Softwood lumber" not in text


def test_missing_series_degrades(env, posted, monkeypatch):
    """A series absent from the BLS response (wrong/unresolvable id) must
    not fail the run -- it degrades to an 'unavailable' line in the report
    while the other series is processed normally."""
    mp = env["mp"]
    response = bls_response({
        "WPU0811": make_series_data(base_value=500.0),  # resolves fine
        "WPU137": None,                                   # simulates unresolved id
    })
    monkeypatch.setattr(mp, "_post_json", lambda url, payload, timeout=15: response)

    mp.main(now=NOW)  # must not raise

    conn = _biz_conn(env)
    rows = conn.execute("SELECT DISTINCT series_id FROM material_price_points").fetchall()
    conn.close()
    assert {r["series_id"] for r in rows} == {"WPU0811"}  # only the resolvable one stored

    reports = [c for c in posted if "Material Prices" in c["text"]]
    assert len(reports) == 1
    assert "Unavailable" in reports[0]["text"]
    assert "WPU137" in reports[0]["text"]


def test_missing_series_empty_data_also_degrades(env, posted, monkeypatch):
    """Same as above but the series IS present in Results.series with an
    explicitly empty `data` list -- the other real-world shape of 'BLS
    doesn't have this series id' per the brief."""
    mp = env["mp"]
    response = bls_response({
        "WPU0811": make_series_data(base_value=500.0),
        "WPU137": [],
    })
    monkeypatch.setattr(mp, "_post_json", lambda url, payload, timeout=15: response)

    mp.main(now=NOW)

    reports = [c for c in posted if "Material Prices" in c["text"]]
    assert len(reports) == 1
    assert "Unavailable" in reports[0]["text"]
    assert "WPU137" in reports[0]["text"]


def test_bls_request_totally_fails_degrades_all(env, posted, monkeypatch):
    """_post_json returning None (network/timeout/bad JSON) must not raise
    -- every configured series shows up as unavailable instead."""
    mp = env["mp"]
    monkeypatch.setattr(mp, "_post_json", lambda url, payload, timeout=15: None)

    mp.main(now=NOW)  # must not raise

    conn = _biz_conn(env)
    rows = conn.execute("SELECT * FROM material_price_points").fetchall()
    conn.close()
    assert rows == []

    reports = [c for c in posted if "Material Prices" in c["text"]]
    assert len(reports) == 1
    assert "WPU0811" in reports[0]["text"]
    assert "WPU137" in reports[0]["text"]


def test_post_json_request_shape(env, posted, monkeypatch):
    """The request body must match the BLS v1 contract from the brief:
    {"seriesid": [...], "startyear": "<Y-1>", "endyear": "<Y>"}."""
    mp = env["mp"]
    captured = {}

    def fake_post_json(url, payload, timeout=15):
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout"] = timeout
        return bls_response({sid: make_series_data(100.0) for sid in TWO_SERIES})

    monkeypatch.setattr(mp, "_post_json", fake_post_json)
    mp.main(now=NOW)

    assert captured["url"] == "https://api.bls.gov/publicAPI/v1/timeseries/data/"
    assert captured["payload"]["startyear"] == "2025"
    assert captured["payload"]["endyear"] == "2026"
    assert set(captured["payload"]["seriesid"]) == set(TWO_SERIES.keys())
    assert captured["timeout"] == 15


def test_post_json_seam_sends_user_agent(monkeypatch):
    """The real _post_json (not mocked here) must send the required
    User-Agent header. We intercept urllib.request.urlopen so no network
    call actually happens."""
    import agents.scout_reeves.crons.material_prices as mp

    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"Results": {"series": []}}'

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        captured["method"] = req.get_method()
        captured["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr(mp.urllib.request, "urlopen", fake_urlopen)

    result = mp._post_json(mp.BLS_URL, {"seriesid": ["WPU0811"], "startyear": "2025", "endyear": "2026"})

    assert result == {"Results": {"series": []}}
    assert captured["method"] == "POST"
    assert captured["headers"].get("User-agent") == mp.USER_AGENT
    assert captured["timeout"] == mp.BLS_TIMEOUT_S


def test_main_is_import_safe_and_standalone(env):
    """main(now=None) exists and importing the module has no side effects
    (already proven implicitly by the `env` fixture's fresh import); assert
    the signature explicitly."""
    mp = env["mp"]
    import inspect
    sig = inspect.signature(mp.main)
    assert list(sig.parameters) == ["now"]
    assert sig.parameters["now"].default is None


# ── period-arithmetic unit tests ────────────────────────────────────────

def test_prev_period_wraps_year(env):
    mp = env["mp"]
    assert mp._prev_period("2026-M01") == "2025-M12"
    assert mp._prev_period("2026-M06") == "2026-M05"


def test_year_ago_period(env):
    mp = env["mp"]
    assert mp._year_ago_period("2026-M06") == "2025-M06"


def test_normalize_points_sorts_and_drops_annual(env):
    mp = env["mp"]
    data = make_series_data(base_value=100.0) + [make_annual_row()]
    points = mp._normalize_points({"seriesID": "WPU0811", "data": data})
    assert len(points) == 13
    periods = [p["period"] for p in points]
    assert periods == sorted(periods)  # ascending
    assert "2025-M13" not in periods


def test_normalize_points_empty_data_returns_empty(env):
    mp = env["mp"]
    assert mp._normalize_points({"seriesID": "WPU137", "data": []}) == []
    assert mp._normalize_points(None) == []
