"""Tests for the routing layer added to agents/cron_helpers.py (Task 4 of the
cron-improvements plan): cron_run() heartbeat context manager,
in_quiet_hours(), send_report() (delta suppression + fyi/alert priority +
quiet-hours queueing), and send_alert() (dedup via cron_health_db's alert
state). Builds on Task 1's core/cron_health_db.py.
"""
import datetime
import importlib
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def ch(monkeypatch):
    """Fresh agents.cron_helpers + core.cron_health_db, DB pointed at a tmp
    file. Mirrors the fresh-import fixture pattern in tests/test_cron_health_db.py
    so BAZA_CRON_HEALTH_DB (baked into core.cron_health_db.DB_PATH at import
    time) is honored per-test."""
    tmpdir = tempfile.mkdtemp(prefix="cron_helpers_routing_")
    path = os.path.join(tmpdir, "cron_health.db")
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", path)

    root = os.path.join(os.path.dirname(__file__), "..")
    sys.path.insert(0, root)
    for mod in ("core.cron_health_db", "agents.cron_helpers"):
        if mod in sys.modules:
            del sys.modules[mod]

    chdb = importlib.import_module("core.cron_health_db")
    chdb.init()
    mod = importlib.import_module("agents.cron_helpers")
    return mod


@pytest.fixture()
def sent(monkeypatch, ch):
    """Recorder standing in for cron_helpers.send_telegram. Returns True by
    default (simulates a successful delivery) -- send_report now passes
    send_telegram's real return value straight through, so callers relying
    on a successful-send outcome (True) need the fake to actually report
    one, same as the real post_html would on a real successful send."""
    calls = []

    def fake_send_telegram(message, token=None, chat_id=None):
        calls.append({"message": message, "token": token, "chat_id": chat_id})
        return True

    monkeypatch.setattr(ch, "send_telegram", fake_send_telegram)
    return calls


@pytest.fixture()
def posted(monkeypatch):
    """Recorder standing in for core.telegram_fmt.post_html (send_alert's
    deferred-import target)."""
    calls = []

    def fake_post_html(token, chat_id, text, *args, **kwargs):
        calls.append({"token": token, "chat_id": chat_id, "text": text})
        return True

    import core.telegram_fmt as telegram_fmt
    monkeypatch.setattr(telegram_fmt, "post_html", fake_post_html)
    return calls


# ── cron_run() ───────────────────────────────────────────────────────────

def test_cron_run_records_ok(ch):
    with ch.cron_run("t_ok"):
        pass
    rows = ch._chdb().recent_runs(cron_name="t_ok")
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["finished_at"] is not None
    assert rows[0]["error"] is None


def test_cron_run_records_error_and_reraises(ch):
    with pytest.raises(ValueError, match="boom"):
        with ch.cron_run("t_err"):
            raise ValueError("boom")
    rows = ch._chdb().recent_runs(cron_name="t_err")
    assert len(rows) == 1
    assert rows[0]["status"] == "error"
    assert "boom" in rows[0]["error"]


def test_cron_run_survives_registry_failure(ch, monkeypatch):
    def boom(name):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(ch._chdb(), "record_run_start", boom)

    ran = {"body": False}
    with ch.cron_run("t_registry_down"):
        ran["body"] = True
    assert ran["body"] is True
    # record_run_start itself failed -> no row id -> record_run_end never called either.
    assert ch._chdb().recent_runs(cron_name="t_registry_down") == []


def test_cron_run_survives_registry_failure_on_exception_path(ch, monkeypatch):
    """Registry failure swallowed on the record_run_end side too, and the
    body's own exception still propagates."""
    monkeypatch.setattr(
        ch._chdb(), "record_run_end",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )
    with pytest.raises(ValueError, match="boom"):
        with ch.cron_run("t_registry_down2"):
            raise ValueError("boom")


def test_cron_run_system_exit_zero_is_ok(ch):
    with pytest.raises(SystemExit):
        with ch.cron_run("t_exit0"):
            raise SystemExit(0)
    rows = ch._chdb().recent_runs(cron_name="t_exit0")
    assert rows[0]["status"] == "ok"


def test_cron_run_system_exit_nonzero_is_error(ch):
    with pytest.raises(SystemExit):
        with ch.cron_run("t_exit1"):
            raise SystemExit(1)
    rows = ch._chdb().recent_runs(cron_name="t_exit1")
    assert rows[0]["status"] == "error"


# ── in_quiet_hours() ─────────────────────────────────────────────────────

def test_quiet_hours_wrap(ch, monkeypatch):
    monkeypatch.setenv("BAZA_QUIET_HOURS", "21:00-06:30")
    d = datetime.date(2026, 7, 2)
    assert ch.in_quiet_hours(datetime.datetime.combine(d, datetime.time(22, 0))) is True
    assert ch.in_quiet_hours(datetime.datetime.combine(d, datetime.time(12, 0))) is False
    assert ch.in_quiet_hours(datetime.datetime.combine(d, datetime.time(6, 0))) is True


def test_quiet_hours_boundaries(ch, monkeypatch):
    monkeypatch.setenv("BAZA_QUIET_HOURS", "21:00-06:30")
    d = datetime.date(2026, 7, 2)
    # Start inclusive
    assert ch.in_quiet_hours(datetime.datetime.combine(d, datetime.time(21, 0))) is True
    # End exclusive
    assert ch.in_quiet_hours(datetime.datetime.combine(d, datetime.time(6, 30))) is False


def test_quiet_hours_uses_default_env(ch, monkeypatch):
    monkeypatch.delenv("BAZA_QUIET_HOURS", raising=False)
    d = datetime.date(2026, 7, 2)
    assert ch.in_quiet_hours(datetime.datetime.combine(d, datetime.time(23, 0))) is True
    assert ch.in_quiet_hours(datetime.datetime.combine(d, datetime.time(9, 0))) is False


def test_quiet_hours_malformed_env_never_quiet(ch, monkeypatch):
    monkeypatch.setenv("BAZA_QUIET_HOURS", "not-a-window")
    assert ch.in_quiet_hours(datetime.datetime(2026, 7, 2, 23, 0)) is False


# ── send_report() ────────────────────────────────────────────────────────

def test_send_report_delta_suppress(ch, sent, monkeypatch):
    # Isolate delta-suppression from quiet-hours routing.
    monkeypatch.setattr(ch, "in_quiet_hours", lambda *a, **k: False)

    r1 = ch.send_report("cronA", "same body", priority="fyi", delta_key="cronA")
    assert r1 is True
    assert len(sent) == 1

    r2 = ch.send_report("cronA", "same body", priority="fyi", delta_key="cronA")
    assert r2 is False
    assert len(sent) == 1  # no second send


def test_send_report_fyi_quiet_enqueues(ch, sent, monkeypatch):
    monkeypatch.setattr(ch, "in_quiet_hours", lambda *a, **k: True)
    fixed_end = datetime.datetime(2026, 7, 3, 6, 30)
    monkeypatch.setattr(ch, "_next_quiet_hours_end", lambda *a, **k: fixed_end)

    result = ch.send_report("cronB", "quiet body", priority="fyi")
    assert result is False
    assert len(sent) == 0

    pending = ch._chdb().pending_fyis(fixed_end.isoformat(timespec="seconds"))
    assert any(r["cron_name"] == "cronB" and r["message"] == "quiet body" for r in pending)


def test_send_report_alert_sends_immediately_and_returns_outcome(ch, sent, monkeypatch):
    """priority="alert" always sends immediately regardless of quiet hours
    (unlike "fyi", which queues) -- but (Blocker B4 fix) the return value is
    the real send_telegram outcome, not an unconditional True. Renamed from
    test_send_report_alert_always_sends, which asserted the old hollow-True
    behavior; that assertion was true only because it always faked a
    successful send, not because send_report actually reported the real
    result."""
    # Even during quiet hours, priority="alert" must send immediately.
    monkeypatch.setattr(ch, "in_quiet_hours", lambda *a, **k: True)
    result = ch.send_report("cronC", "alert body", priority="alert")
    assert result is True
    assert len(sent) == 1
    assert sent[0]["message"] == "alert body"


def test_send_report_alert_returns_false_on_send_failure(ch, monkeypatch):
    """The alert path must propagate a failed send as False, not swallow it
    to True -- this is the actual bug Blocker B4 fixes: a caller gating on
    send_report's return (e.g. briefing_cron's FYI-consumption gate) needs
    to see the real outcome."""
    monkeypatch.setattr(ch, "in_quiet_hours", lambda *a, **k: False)
    monkeypatch.setattr(ch, "send_telegram", lambda *a, **k: False)
    result = ch.send_report("cronC2", "alert body 2", priority="alert")
    assert result is False


def test_send_report_fyi_day_sends_now(ch, sent, monkeypatch):
    monkeypatch.setattr(ch, "in_quiet_hours", lambda *a, **k: False)
    result = ch.send_report("cronE", "daytime fyi", priority="fyi")
    assert result is True
    assert len(sent) == 1


def test_send_report_fyi_day_returns_false_on_send_failure(ch, monkeypatch):
    """Same real-outcome propagation as the alert path (Blocker B4), for the
    fyi-outside-quiet-hours ("fyi day") send-now branch."""
    monkeypatch.setattr(ch, "in_quiet_hours", lambda *a, **k: False)
    monkeypatch.setattr(ch, "send_telegram", lambda *a, **k: False)
    result = ch.send_report("cronE2", "daytime fyi 2", priority="fyi")
    assert result is False


def test_send_report_registry_failure_fails_open(ch, sent, monkeypatch):
    """delta_changed() raising must not swallow the report -- fail open and
    still route/send it."""
    monkeypatch.setattr(ch, "in_quiet_hours", lambda *a, **k: False)
    monkeypatch.setattr(
        ch._chdb(), "delta_changed",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )
    result = ch.send_report("cronF", "body", priority="fyi", delta_key="cronF")
    assert result is True
    assert len(sent) == 1


# ── send_alert() ─────────────────────────────────────────────────────────

def test_send_alert_dedups_by_key(ch, posted):
    # Dedup (repeat call -> no second send) only kicks in with renotify_hours
    # set -- cron_health_db.should_alert's own contract is "no renotify_hours
    # -> repeat calls still fire" (see test_should_alert_no_renotify_always_fires
    # in tests/test_cron_health_db.py), so the key is deduped here via the
    # renotify window, not merely by repeating alert_key.
    r1 = ch.send_alert("cronD", "Disk full\ndetails here", alert_key="disk_full",
                       renotify_hours=6.0)
    assert r1 is True
    assert len(posted) == 1

    r2 = ch.send_alert("cronD", "Disk full\ndetails here", alert_key="disk_full",
                       renotify_hours=6.0)
    assert r2 is False
    assert len(posted) == 1  # deduped, no second send


def test_send_alert_renotify_hours_reopens_after_window(ch, posted):
    r1 = ch.send_alert("cronD2", "GPU hot", alert_key="gpu_hot", renotify_hours=1.0)
    assert r1 is True

    # Backdate last_seen past the renotify window -> should fire again.
    from datetime import datetime as dt, timedelta
    with ch._chdb().connect() as conn:
        backdated = (dt.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        conn.execute(
            "UPDATE cron_alert_state SET last_seen = ? WHERE key = ?",
            (backdated, "gpu_hot"),
        )
        conn.commit()

    r2 = ch.send_alert("cronD2", "GPU hot", alert_key="gpu_hot", renotify_hours=1.0)
    assert r2 is True
    assert len(posted) == 2


def test_send_alert_stores_title_in_meta(ch, posted):
    import json
    ch.send_alert("cronG", "Disk full on /home\nmore detail", alert_key="disk_full_meta")
    row = None
    with ch._chdb().connect() as conn:
        row = conn.execute(
            "SELECT meta FROM cron_alert_state WHERE key = ?", ("disk_full_meta",)
        ).fetchone()
    assert row is not None
    meta = json.loads(row["meta"])
    assert meta == {"title": "Disk full on /home"}


def test_send_alert_registry_failure_fails_open(ch, posted, monkeypatch):
    monkeypatch.setattr(
        ch._chdb(), "should_alert",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )
    result = ch.send_alert("cronH", "still send me", alert_key="whatever")
    assert result is True
    assert len(posted) == 1


def test_send_alert_buttons_param_has_no_effect_yet(ch, posted):
    """buttons=True is accepted (forward-compat with a later task) but must
    not change today's send-without-reply_markup behavior or its args."""
    r_true = ch.send_alert("cronI", "msg1", alert_key="k1", buttons=True)
    r_false = ch.send_alert("cronI2", "msg2", alert_key="k2", buttons=False)
    assert r_true is True and r_false is True
    assert len(posted) == 2
