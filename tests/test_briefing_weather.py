"""Tests for agents/simon_bately/briefing_cron.py's Task 7 additions
(cron-improvements plan): per-jobsite weather section, overnight-FYI flush,
and the artifact-reuse helper.

All external calls are mocked: forecast (core.weather_sources.get_forecast,
monkeypatched on the briefing_cron module the same way
tests/test_weather_watch.py monkeypatches it on the weather_watch module),
the Philadelphia-fallback weather skill (SkillsEngine, monkeypatched to a
fake), Telegram delivery (core.telegram_fmt.post_html), and the LLM
(build_dynamic_briefing itself is monkeypatched out in the one main()
smoke test -- no live Ollama). cron_health.db is pointed at a tmp path via
BAZA_CRON_HEALTH_DB + a fresh reimport, mirroring test_weather_watch.py's
`env` fixture. ahb_projects DDL is copied verbatim from
`sqlite3 dashboard/baza_projects.db ".schema ahb_projects"`, same approach
as test_geocode.py / test_weather_watch.py.
"""
import datetime
import importlib
import os
import sqlite3
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import agents.simon_bately.briefing_cron as bc  # noqa: E402


# ── ahb_projects DDL (copied via sqlite3 .schema, same as test_weather_watch.py) ──

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


@pytest.fixture()
def biz_conn(tmp_path):
    db_path = tmp_path / "biz_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(AHB_PROJECTS_DDL)
    conn.commit()
    yield conn
    conn.close()


def _make_forecast(daily=None):
    return {"source": "nws", "daily": daily or [], "hourly": []}


def make_day(high_f=80, low_f=60, precip_prob_max=10, wind_mph=8):
    return {
        "date": "2026-07-02", "high_f": high_f, "low_f": low_f,
        "precip_prob_max": precip_prob_max, "precip_in": 0.0,
        "wind_mph": wind_mph, "gust_mph": wind_mph + 5, "conditions": "Clear",
    }


# ── build_site_weather_section ──────────────────────────────────────────

def test_weather_section_no_geocoded_sites_falls_back_to_philadelphia(biz_conn, monkeypatch):
    monkeypatch.setattr(bc, "_philadelphia_fallback_weather", lambda: "PHILLY-FALLBACK")
    monkeypatch.setattr(bc, "get_forecast", lambda lat, lon: (_ for _ in ()).throw(
        AssertionError("get_forecast should not be called with zero geocoded sites")))

    result = bc.build_site_weather_section(biz_conn)
    assert result == "PHILLY-FALLBACK"


def test_weather_section_falls_back_when_sites_have_no_coords(biz_conn, monkeypatch):
    insert_project(biz_conn, "p1", status="In Progress", latitude=None, longitude=None)
    monkeypatch.setattr(bc, "_philadelphia_fallback_weather", lambda: "PHILLY-FALLBACK")

    result = bc.build_site_weather_section(biz_conn)
    assert result == "PHILLY-FALLBACK"


def test_weather_section_skips_non_in_progress_sites(biz_conn, monkeypatch):
    insert_project(biz_conn, "p-active", status="In Progress", title="Active Job",
                    latitude=40.10, longitude=-75.10)
    insert_project(biz_conn, "p-estimate", status="estimate", title="Estimate Job",
                    latitude=41.00, longitude=-76.00)

    calls = []

    def fake_forecast(lat, lon):
        calls.append((lat, lon))
        return _make_forecast(daily=[make_day()])

    monkeypatch.setattr(bc, "get_forecast", fake_forecast)

    result = bc.build_site_weather_section(biz_conn)
    assert len(calls) == 1
    assert calls[0] == (40.10, -75.10)
    assert "Active Job" in result
    assert "Estimate Job" not in result


def test_weather_section_dedupes_coords_one_forecast_call(biz_conn, monkeypatch):
    # Same rounded (lat, lon) -> round(40.101, 2) == round(40.104, 2) == 40.1
    insert_project(biz_conn, "p1", status="In Progress", title="Site One",
                    latitude=40.101, longitude=-75.101)
    insert_project(biz_conn, "p2", status="In Progress", title="Site Two",
                    latitude=40.104, longitude=-75.104)

    calls = []

    def fake_forecast(lat, lon):
        calls.append((lat, lon))
        return _make_forecast(daily=[make_day(high_f=78, low_f=61, precip_prob_max=20, wind_mph=9)])

    monkeypatch.setattr(bc, "get_forecast", fake_forecast)

    result = bc.build_site_weather_section(biz_conn)
    assert len(calls) == 1
    assert "Site One" in result
    assert "Site Two" in result
    assert "78" in result and "61" in result and "20%" in result


def test_weather_section_wind_flag_above_threshold(biz_conn, monkeypatch):
    insert_project(biz_conn, "p1", status="In Progress", title="Windy Job",
                    latitude=40.1, longitude=-75.1)
    monkeypatch.setattr(bc, "get_forecast",
                         lambda lat, lon: _make_forecast(daily=[make_day(wind_mph=25)]))

    result = bc.build_site_weather_section(biz_conn)
    assert "💨" in result


def test_weather_section_no_wind_flag_below_threshold(biz_conn, monkeypatch):
    insert_project(biz_conn, "p1", status="In Progress", title="Calm Job",
                    latitude=40.1, longitude=-75.1)
    monkeypatch.setattr(bc, "get_forecast",
                         lambda lat, lon: _make_forecast(daily=[make_day(wind_mph=5)]))

    result = bc.build_site_weather_section(biz_conn)
    assert "💨" not in result


def test_weather_section_forecast_unavailable_for_one_coord_does_not_crash(biz_conn, monkeypatch):
    insert_project(biz_conn, "p1", status="In Progress", title="No Forecast Job",
                    latitude=40.1, longitude=-75.1)
    monkeypatch.setattr(bc, "get_forecast", lambda lat, lon: None)

    result = bc.build_site_weather_section(biz_conn)
    assert "No Forecast Job" in result
    assert "unavailable" in result.lower()


def test_weather_section_db_error_falls_back_to_philadelphia(monkeypatch):
    class BoomConn:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("no such table: ahb_projects")

    monkeypatch.setattr(bc, "_philadelphia_fallback_weather", lambda: "PHILLY-FALLBACK")
    result = bc.build_site_weather_section(BoomConn())
    assert result == "PHILLY-FALLBACK"


def test_philadelphia_fallback_uses_weather_skill(monkeypatch):
    calls = []

    class FakeSkills:
        def __init__(self, framework_dir):
            pass

        def run(self, name, args):
            calls.append((name, args))
            return {"success": True, "output": "WEATHER: Philly 75F sunny"}

    monkeypatch.setattr(bc, "SkillsEngine", FakeSkills)
    result = bc._philadelphia_fallback_weather()
    assert result == "WEATHER: Philly 75F sunny"
    assert calls == [("weather", {"location": "Philadelphia, PA"})]


def test_philadelphia_fallback_skill_failure_degrades(monkeypatch):
    class FakeSkills:
        def __init__(self, framework_dir):
            pass

        def run(self, name, args):
            raise RuntimeError("boom")

    monkeypatch.setattr(bc, "SkillsEngine", FakeSkills)
    result = bc._philadelphia_fallback_weather()
    assert result == "WEATHER: unavailable"


# ── build_fyi_section ────────────────────────────────────────────────────

@pytest.fixture()
def chdb(tmp_path, monkeypatch):
    """Fresh core.cron_health_db pointed at a tmp DB. bc.build_fyi_section()
    resolves core.cron_health_db fresh from sys.modules on every call (its
    _chdb() deferred-import helper), so we don't need to reimport bc itself
    -- only cron_health_db, after the env var is set. Mirrors
    tests/test_weather_watch.py's `env` fixture / agents.cron_helpers._chdb()."""
    ch_db_path = tmp_path / "cron_health.db"
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", str(ch_db_path))
    sys.modules.pop("core.cron_health_db", None)
    mod = importlib.import_module("core.cron_health_db")
    mod.init()
    return mod


def test_fyi_section_empty_when_nothing_pending(chdb):
    assert bc.build_fyi_section() == ("", [])


def test_fyi_section_returns_bullets_and_ids_without_consuming(chdb):
    """build_fyi_section() defers consumption to the caller now (main(),
    only after a successful send) -- reading it must NOT mark rows
    consumed as a side effect, so a repeat read (e.g. a retry, or simply
    not having sent yet) still sees the same pending items."""
    past = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat(timespec="seconds")
    id1 = chdb.enqueue_fyi("infra_health", "All systems nominal overnight.", past)
    id2 = chdb.enqueue_fyi("code_review", "3 minor lint findings, no action needed.", past)

    text, ids = bc.build_fyi_section()
    assert text.startswith("📥 Overnight FYIs")
    assert "infra_health" in text
    assert "All systems nominal overnight." in text
    assert "code_review" in text
    assert "3 minor lint findings" in text
    assert ids == [id1, id2]

    # Reading again WITHOUT marking consumed must return the same pending
    # items -- build_fyi_section() itself never consumes.
    text2, ids2 = bc.build_fyi_section()
    assert text2 == text
    assert ids2 == ids

    # Only once the caller explicitly marks them consumed does a
    # subsequent read find nothing left.
    chdb.mark_fyis_consumed(ids)
    assert bc.build_fyi_section() == ("", [])


def test_fyi_section_caps_at_ten_plus_more(chdb):
    past = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat(timespec="seconds")
    all_ids = [chdb.enqueue_fyi("some_cron", f"item {i}", past) for i in range(13)]

    text, ids = bc.build_fyi_section()
    lines = text.splitlines()
    bullet_lines = [l for l in lines if l.strip().startswith("•")]
    assert len(bullet_lines) == 10
    assert "+3 more" in text
    # All 13 (not just the shown 10) come back as pending ids -- it's the
    # caller's job to mark all of them consumed, not just the displayed ones.
    assert ids == all_ids


def test_fyi_section_future_release_not_included_or_consumed(chdb):
    future = (datetime.datetime.now() + datetime.timedelta(hours=5)).isoformat(timespec="seconds")
    chdb.enqueue_fyi("weather_watch", "not due yet", future)

    assert bc.build_fyi_section() == ("", [])

    # Still pending -- prove it wasn't accidentally consumed.
    far_future = (datetime.datetime.now() + datetime.timedelta(hours=6)).isoformat(timespec="seconds")
    assert len(chdb.pending_fyis(far_future)) == 1


def test_fyi_section_pending_fyis_error_is_exception_safe(monkeypatch):
    class BoomChdb:
        def pending_fyis(self, now_iso):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(bc, "_chdb", lambda: BoomChdb())
    assert bc.build_fyi_section() == ("", [])


# ── main()'s deferred FYI-consumption gate ──────────────────────────────
# build_fyi_section() no longer calls mark_fyis_consumed itself (see above)
# -- that call moved into main(), gated on send_telegram's return value.
# These two tests cover that gate directly.

def test_main_marks_fyis_consumed_only_after_successful_send(tmp_path, monkeypatch):
    ch_db_path = tmp_path / "cron_health.db"
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", str(ch_db_path))
    sys.modules.pop("core.cron_health_db", None)
    chdb_mod = importlib.import_module("core.cron_health_db")
    chdb_mod.init()

    monkeypatch.setattr(bc, "FRAMEWORK_DIR", str(tmp_path))
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(bc, "build_dynamic_briefing",
                         lambda *a, **k: "━━━━━━━━━━━━━━━━\nCanned briefing body\n━━━━━━━━━━━━━━━━")
    monkeypatch.setattr(bc, "get_team_status", lambda: "TEAM STATUS: all good")
    monkeypatch.setattr(bc, "get_recent_activity", lambda: "RECENT ACTIVITY: none")

    class FakeSkills:
        def __init__(self, framework_dir):
            pass

        def run(self, name, args):
            return {"success": True, "output": f"{name.upper()}: canned"}

    monkeypatch.setattr(bc, "SkillsEngine", FakeSkills)

    past = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat(timespec="seconds")
    fyi_id = chdb_mod.enqueue_fyi("infra_health", "Backups verified overnight.", past)

    import core.claim_verifier as cv
    monkeypatch.setattr(cv, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    # send_telegram() succeeds -> the FYI must be marked consumed.
    monkeypatch.setattr(bc, "send_telegram", lambda text: True)

    bc.main()

    now_iso = datetime.datetime.now().isoformat(timespec="seconds")
    assert chdb_mod.pending_fyis(now_iso) == []


def test_main_leaves_fyis_unconsumed_when_send_fails(tmp_path, monkeypatch):
    """Proves the fix: a failed send must NOT lose queued FYIs. Failure is
    simulated with a post_html recorder that returns False, routed through
    a fake cron_helpers.send_report that (unlike the real, unmodified
    cron_helpers.send_report's priority="alert" path -- see
    tests/test_cron_helpers_routing.py::test_send_report_alert_always_sends,
    which always returns True regardless of delivery outcome and is out of
    scope for this fix) actually honors post_html's result, so this test
    exercises main()'s own gating logic against a realistic "delivery
    failed" signal."""
    ch_db_path = tmp_path / "cron_health.db"
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", str(ch_db_path))
    sys.modules.pop("core.cron_health_db", None)
    chdb_mod = importlib.import_module("core.cron_health_db")
    chdb_mod.init()

    monkeypatch.setattr(bc, "FRAMEWORK_DIR", str(tmp_path))
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(bc, "build_dynamic_briefing",
                         lambda *a, **k: "━━━━━━━━━━━━━━━━\nCanned briefing body\n━━━━━━━━━━━━━━━━")
    monkeypatch.setattr(bc, "get_team_status", lambda: "TEAM STATUS: all good")
    monkeypatch.setattr(bc, "get_recent_activity", lambda: "RECENT ACTIVITY: none")

    class FakeSkills:
        def __init__(self, framework_dir):
            pass

        def run(self, name, args):
            return {"success": True, "output": f"{name.upper()}: canned"}

    monkeypatch.setattr(bc, "SkillsEngine", FakeSkills)

    past = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat(timespec="seconds")
    fyi_id = chdb_mod.enqueue_fyi("infra_health", "Backups verified overnight.", past)

    import core.claim_verifier as cv
    monkeypatch.setattr(cv, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    posted = []

    def fake_post_html(token, chat_id, text, *a, **k):
        posted.append(text)
        return False  # simulates a Telegram delivery failure

    import core.telegram_fmt as telegram_fmt
    monkeypatch.setattr(telegram_fmt, "post_html", fake_post_html)

    def fake_send_report(cron_name, message, priority="fyi", token=None, chat_id=None, **kw):
        from core.telegram_fmt import post_html
        return post_html(token, chat_id, message)

    import agents.cron_helpers as cron_helpers
    monkeypatch.setattr(cron_helpers, "send_report", fake_send_report)

    bc.main()

    assert len(posted) == 1  # a send was attempted...
    # ...but it failed, so the queued FYI must still be pending.
    now_iso = datetime.datetime.now().isoformat(timespec="seconds")
    pending = chdb_mod.pending_fyis(now_iso)
    assert len(pending) == 1
    assert pending[0]["id"] == fyi_id


def test_main_survives_mark_fyis_consumed_error_after_successful_send(tmp_path, monkeypatch):
    """mark_fyis_consumed is called post-send now (inside main(), not
    build_fyi_section()) -- it must stay exception-safe there too: a
    registry hiccup on the write must be logged and swallowed, not crash
    a briefing that otherwise sent successfully."""
    ch_db_path = tmp_path / "cron_health.db"
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", str(ch_db_path))
    sys.modules.pop("core.cron_health_db", None)
    chdb_mod = importlib.import_module("core.cron_health_db")
    chdb_mod.init()

    monkeypatch.setattr(bc, "FRAMEWORK_DIR", str(tmp_path))
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(bc, "build_dynamic_briefing",
                         lambda *a, **k: "━━━━━━━━━━━━━━━━\nCanned briefing body\n━━━━━━━━━━━━━━━━")
    monkeypatch.setattr(bc, "get_team_status", lambda: "TEAM STATUS: all good")
    monkeypatch.setattr(bc, "get_recent_activity", lambda: "RECENT ACTIVITY: none")

    class FakeSkills:
        def __init__(self, framework_dir):
            pass

        def run(self, name, args):
            return {"success": True, "output": f"{name.upper()}: canned"}

    monkeypatch.setattr(bc, "SkillsEngine", FakeSkills)

    past = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat(timespec="seconds")
    chdb_mod.enqueue_fyi("infra_health", "Backups verified overnight.", past)

    import core.claim_verifier as cv
    monkeypatch.setattr(cv, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    monkeypatch.setattr(bc, "send_telegram", lambda text: True)
    monkeypatch.setattr(chdb_mod, "mark_fyis_consumed",
                         lambda ids: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))

    bc.main()  # must not raise

    runs = chdb_mod.recent_runs(cron_name="team_briefing")
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"


# ── read_recent_artifact ─────────────────────────────────────────────────

def _touch(path, age_hours=0.0, content="hello"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    ts = datetime.datetime.now().timestamp() - age_hours * 3600
    os.utime(path, (ts, ts))


def test_read_recent_artifact_none_when_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path))
    assert bc.read_recent_artifact("proj-x/foo_*.md") is None


def test_read_recent_artifact_returns_newest_match_content(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path))
    older = str(tmp_path / "proj-x" / "foo_2026-07-01.md")
    newer = str(tmp_path / "proj-x" / "foo_2026-07-02.md")
    _touch(older, age_hours=2.0, content="OLDER CONTENT")
    _touch(newer, age_hours=0.5, content="NEWER CONTENT")

    result = bc.read_recent_artifact("proj-x/foo_*.md")
    assert result == "NEWER CONTENT"


def test_read_recent_artifact_none_when_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path))
    p = str(tmp_path / "proj-x" / "foo_2026-06-01.md")
    _touch(p, age_hours=20.0, content="STALE")

    assert bc.read_recent_artifact("proj-x/foo_*.md", max_age_h=12.0) is None


def test_read_recent_artifact_respects_custom_max_age(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path))
    p = str(tmp_path / "proj-x" / "foo_2026-06-01.md")
    _touch(p, age_hours=20.0, content="STILL FRESH ENOUGH")

    assert bc.read_recent_artifact("proj-x/foo_*.md", max_age_h=24.0) == "STILL FRESH ENOUGH"


def test_read_recent_artifact_ignores_meta_sidecars(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path))
    real = str(tmp_path / "proj-x" / "foo_2026-07-01.md")
    meta = str(tmp_path / "proj-x" / "foo_2026-07-01.md.meta")
    _touch(real, age_hours=1.0, content="REAL CONTENT")
    _touch(meta, age_hours=0.1, content='{"agent_id":"duke_harmon"}')  # newer mtime than real

    # Broad glob that would match both real + its .meta sidecar.
    result = bc.read_recent_artifact("proj-x/foo_*")
    assert result == "REAL CONTENT"


def test_read_recent_artifact_swallows_directory_match(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path))
    os.makedirs(str(tmp_path / "proj-x" / "foo_is_a_dir"), exist_ok=True)
    assert bc.read_recent_artifact("proj-x/foo_*") is None


# ── get_tasks_summary() wiring to read_recent_artifact ──────────────────

def test_tasks_summary_prefers_fresh_duke_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    artifact_path = str(tmp_path / "artifacts" / "proj-baza-empire" / "task_manager_2026-07-02.md")
    _touch(artifact_path, age_hours=1.0, content="# Task Manager\n\nPipeline healthy, 12 active tasks.")

    result = bc.get_tasks_summary()
    assert "Pipeline healthy, 12 active tasks." in result
    assert "TASK BOARD (from Duke's last project_tracker run):" in result


def test_tasks_summary_falls_back_when_artifact_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(bc, "FRAMEWORK_DIR", str(tmp_path))  # no baza_projects.db under here

    result = bc.get_tasks_summary()
    assert result == "TASKS: local DB not found"


# ── main() wiring smoke test (cron_run heartbeat + section assembly) ────

def test_main_records_heartbeat_and_appends_fyi_section(tmp_path, monkeypatch):
    # cron_health.db -> tmp
    ch_db_path = tmp_path / "cron_health.db"
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", str(ch_db_path))
    sys.modules.pop("core.cron_health_db", None)
    chdb_mod = importlib.import_module("core.cron_health_db")
    chdb_mod.init()

    # No business DB / artifacts -> deterministic recompute fallbacks.
    monkeypatch.setattr(bc, "FRAMEWORK_DIR", str(tmp_path))
    monkeypatch.setattr(bc, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    # Never hit real Ollama.
    monkeypatch.setattr(bc, "build_dynamic_briefing",
                         lambda *a, **k: "━━━━━━━━━━━━━━━━\nCanned briefing body\n━━━━━━━━━━━━━━━━")

    # Never hit real subprocess/postgres for team status / activity.
    monkeypatch.setattr(bc, "get_team_status", lambda: "TEAM STATUS: all good")
    monkeypatch.setattr(bc, "get_recent_activity", lambda: "RECENT ACTIVITY: none")

    # SkillsEngine stands in for both the "news" call and the Philadelphia
    # weather fallback (no in-progress geocoded sites since there's no DB).
    class FakeSkills:
        def __init__(self, framework_dir):
            pass

        def run(self, name, args):
            return {"success": True, "output": f"{name.upper()}: canned"}

    monkeypatch.setattr(bc, "SkillsEngine", FakeSkills)

    # Overnight FYI due now.
    past = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat(timespec="seconds")
    chdb_mod.enqueue_fyi("infra_health", "Backups verified overnight.", past)

    # Claim verifier should not reach out to the real repo's artifacts dir.
    import core.claim_verifier as cv
    monkeypatch.setattr(cv, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    sent = []

    def fake_post_html(token, chat_id, text, *a, **k):
        sent.append(text)
        return True

    import core.telegram_fmt as telegram_fmt
    monkeypatch.setattr(telegram_fmt, "post_html", fake_post_html)

    bc.main()

    assert len(sent) == 1
    assert "Canned briefing body" in sent[0]
    assert "Overnight FYIs" in sent[0]
    assert "Backups verified overnight." in sent[0]

    runs = chdb_mod.recent_runs(cron_name="team_briefing")
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"
