"""Tests for core/cron_health_db.py — infra DB (heartbeats, delta-hashing,
FYI queue, alert dedup) that Task 1 of the cron-improvements plan builds.
"""
import importlib
import os
import sqlite3
import sys
import tempfile

import pytest


@pytest.fixture()
def db(monkeypatch):
    """Fresh module instance pointing at a temporary DB file."""
    tmpdir = tempfile.mkdtemp(prefix="cron_health_db_")
    path = os.path.join(tmpdir, "cron_health.db")
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", path)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    if "core.cron_health_db" in sys.modules:
        del sys.modules["core.cron_health_db"]
    mod = importlib.import_module("core.cron_health_db")
    mod.init()
    return mod


def test_init_idempotent(db):
    # Calling init() again must not raise and must leave tables usable.
    db.init()
    db.init()
    with db.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"cron_runs", "report_hashes", "fyi_queue", "cron_alert_state"} <= tables


def test_record_run_lifecycle(db):
    run_id = db.record_run_start("infra_health")
    assert isinstance(run_id, int) and run_id > 0

    rows = db.recent_runs(cron_name="infra_health")
    assert len(rows) == 1
    assert rows[0]["status"] is None
    assert rows[0]["finished_at"] is None

    db.record_run_end(run_id, "ok")

    rows = db.recent_runs(cron_name="infra_health")
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["finished_at"] is not None
    assert rows[0]["duration_s"] is not None
    assert rows[0]["duration_s"] >= 0

    last = db.last_runs_by_cron()
    assert "infra_health" in last
    assert last["infra_health"]["id"] == run_id


def test_record_run_lifecycle_error(db):
    run_id = db.record_run_start("flaky_cron")
    db.record_run_end(run_id, "error", error="boom")
    rows = db.recent_runs(cron_name="flaky_cron")
    assert rows[0]["status"] == "error"
    assert rows[0]["error"] == "boom"


def test_delta_changed_first_true_repeat_false(db):
    assert db.delta_changed("weather_cron", "same body") is True
    assert db.delta_changed("weather_cron", "same body") is False
    assert db.delta_changed("weather_cron", "different body") is True


def test_delta_force_after(db):
    from datetime import datetime, timedelta

    assert db.delta_changed("stale_cron", "body") is True
    assert db.delta_changed("stale_cron", "body") is False

    # Backdate last_sent_at by 73 hours (past the 72h default force window).
    # Use Python's local datetime (not SQLite's datetime('now',...), which is
    # UTC) since the module stores local-time ISO timestamps.
    backdated = (datetime.now() - timedelta(hours=73)).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute(
            "UPDATE report_hashes SET last_sent_at = ? WHERE cron_name = ?",
            (backdated, "stale_cron"),
        )
        conn.commit()

    assert db.delta_changed("stale_cron", "body") is True


def test_fyi_enqueue_release_consume(db):
    from datetime import datetime, timedelta

    now = datetime.now()
    past = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    future = (now + timedelta(hours=1)).isoformat(timespec="seconds")
    now_iso = now.isoformat(timespec="seconds")

    id_past = db.enqueue_fyi("digest_cron", "hello now", past)
    id_future = db.enqueue_fyi("digest_cron", "hello later", future)
    assert isinstance(id_past, int) and isinstance(id_future, int)

    pending = db.pending_fyis(now_iso)
    ids = {r["id"] for r in pending}
    assert id_past in ids
    assert id_future not in ids

    db.mark_fyis_consumed([id_past])
    pending_after = db.pending_fyis(now_iso)
    assert id_past not in {r["id"] for r in pending_after}


def test_should_alert_new_true_repeat_false_with_renotify(db):
    send_now, row_id = db.should_alert("disk_full", renotify_hours=1.0)
    assert send_now is True
    assert isinstance(row_id, int)

    send_now2, row_id2 = db.should_alert("disk_full", renotify_hours=1.0)
    assert row_id2 == row_id
    assert send_now2 is False


def test_should_alert_no_renotify_always_fires(db):
    send_now, row_id = db.should_alert("cpu_hot")
    assert send_now is True
    send_now2, row_id2 = db.should_alert("cpu_hot")
    assert row_id2 == row_id
    # No renotify_hours set -> repeat calls still fire.
    assert send_now2 is True


def test_alert_ack_blocks(db):
    send_now, row_id = db.should_alert("ollama_down")
    assert send_now is True

    db.alert_ack(row_id)

    send_now2, row_id2 = db.should_alert("ollama_down")
    assert row_id2 == row_id
    assert send_now2 is False

    row = db.alert_get(row_id)
    assert row["acked_at"] is not None


def test_snooze_blocks_until_expiry(db):
    send_now, row_id = db.should_alert("gpu_temp_high")
    assert send_now is True

    db.alert_snooze(row_id, hours=24.0)
    send_now2, _ = db.should_alert("gpu_temp_high")
    assert send_now2 is False

    # Backdate snoozed_until into the past (local time, matching the module's
    # own timestamp convention) -> should fire again.
    from datetime import datetime, timedelta

    backdated = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute(
            "UPDATE cron_alert_state SET snoozed_until = ? WHERE id = ?",
            (backdated, row_id),
        )
        conn.commit()

    send_now3, row_id3 = db.should_alert("gpu_temp_high")
    assert row_id3 == row_id
    assert send_now3 is True


def test_alert_get_missing_returns_none(db):
    assert db.alert_get(999999) is None


def test_should_alert_new_key_race_no_integrity_error(db, monkeypatch):
    """Regression: two concurrent callers with a fresh key both see row=None
    before either INSERTs (the classic new-key race on the UNIQUE(key)
    constraint). Simulate the interleaving by wrapping connect() so that,
    the moment should_alert()'s own INSERT is about to run, a second
    connection wins the race and inserts the same key first. The INSERT must
    not raise sqlite3.IntegrityError, and should_alert() must still return a
    sane (bool, row_id) tuple with exactly one row left for the key.
    """
    key = "race_key"
    state = {"raced": False}

    class _RacyConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if not state["raced"] and sql.lstrip().upper().startswith(
                "INSERT INTO CRON_ALERT_STATE"
            ):
                state["raced"] = True
                # A second, concurrent caller wins the insert race for the
                # same fresh key before this statement runs.
                other = sqlite3.connect(str(db.DB_PATH), timeout=5)
                try:
                    other.execute(
                        """INSERT INTO cron_alert_state
                           (key, first_seen, last_seen, meta)
                           VALUES (?, ?, ?, ?)""",
                        (key, "2020-01-01T00:00:00", "2020-01-01T00:00:00", None),
                    )
                    other.commit()
                finally:
                    other.close()
            return super().execute(sql, parameters)

    def racy_connect():
        db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db.DB_PATH), timeout=5, factory=_RacyConnection)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(db, "connect", racy_connect)

    # Must not raise sqlite3.IntegrityError even though a row for `key`
    # now already exists by the time our own INSERT runs.
    send_now, row_id = db.should_alert(key)
    assert isinstance(send_now, bool)
    assert isinstance(row_id, int)
    assert state["raced"] is True

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cron_alert_state WHERE key = ?", (key,)
        ).fetchall()
    assert len(rows) == 1


def test_delta_changed_serializes_concurrent_callers(db):
    """Regression: delta_changed()'s read-compare-write must be wrapped in a
    BEGIN IMMEDIATE transaction so overlapping callers serialize instead of
    both computing changed=True from a stale read. Prove the write lock is
    held across the whole read-compare-write by manually holding one via a
    separate connection and confirming a concurrent delta_changed() call
    blocks until it's released, rather than proceeding immediately.
    """
    import threading
    import time

    holder = db.connect()
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        """INSERT OR REPLACE INTO report_hashes (cron_name, last_hash, last_sent_at)
           VALUES (?, ?, ?)""",
        ("locked_cron", "preexisting-hash", "2020-01-01T00:00:00"),
    )

    results = {}

    def call_delta_changed():
        results["changed"] = db.delta_changed("locked_cron", "new body")

    t = threading.Thread(target=call_delta_changed)
    t.start()
    time.sleep(0.3)
    # The background call's BEGIN IMMEDIATE should still be waiting on the
    # write lock held by `holder` -- it must not have proceeded yet.
    assert t.is_alive()

    holder.commit()
    holder.close()

    t.join(timeout=5)
    assert not t.is_alive()
    assert results["changed"] is True


def test_record_run_end_rejects_invalid_status(db):
    run_id = db.record_run_start("bad_status_cron")
    with pytest.raises(ValueError):
        db.record_run_end(run_id, "not_a_real_status")

    # The row must remain untouched (still "running") after the rejection.
    rows = db.recent_runs(cron_name="bad_status_cron")
    assert rows[0]["status"] is None
    assert rows[0]["finished_at"] is None
