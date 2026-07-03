"""Tests for agents/claw_batto/crons/backup_verify.py — Task 12 of the
cron-improvements plan (weekly backup verification: freshness, size sanity,
sqlite test-restore + integrity_check, postgres dump validity).

Pure-function tests (_dated_dirs, _dir_size, _check_sqlite_backup,
_check_pg_dump, verify) run against tmp_path fixtures. Where the checked
tool is a plain Unix utility available in any dev/CI box (gzip, sqlite3,
head), the real command runs -- no mocking needed for those, so the tests
actually exercise the test-restore path. `pg_restore` needs a real
postgres-produced archive to succeed against, so its custom-format checks
are monkeypatched at the _sh() level; the plain-text header-sniff path
(head -c 200 / gunzip -c | head -c 200) is real.

main()'s Telegram/cron_health_db wiring is covered separately with the
same fresh-reimport-against-a-tmp-DB fixture pattern as
tests/test_cron_helpers_routing.py.
"""
import gzip
import importlib
import os
import sqlite3
import sys
import tempfile
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import agents.claw_batto.crons.backup_verify as bv


# ── fixture helpers ──────────────────────────────────────────────────────────

def _make_dated_dir(root, sub, stamp, files):
    """root/sub/stamp/ populated with {filename: bytes}. Returns the path."""
    path = os.path.join(root, sub, stamp)
    os.makedirs(path, exist_ok=True)
    for name, content in files.items():
        with open(os.path.join(path, name), "wb") as f:
            f.write(content)
    return path


def _valid_sqlite_gz_bytes():
    """A real, valid gzip'd sqlite db (mirrors backup.sh's `sqlite3 .backup`
    + gzip -- a raw sqlite binary, not a text SQL dump)."""
    tmpdir = tempfile.mkdtemp(prefix="bv_fixture_")
    db_path = os.path.join(tmpdir, "t.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    with open(db_path, "rb") as f:
        raw = f.read()
    return gzip.compress(raw)


def _corrupt_sqlite_gz_bytes():
    """Valid gzip, but the decompressed payload is not a sqlite db at all."""
    return gzip.compress(b"this is not a sqlite database, just garbage bytes")


def _fresh_backup(root, stamp, sqlite_gz=None, pg_dump=b"fake dump", extra=None):
    files = {
        "baza_projects.db.gz": sqlite_gz if sqlite_gz is not None else _valid_sqlite_gz_bytes(),
        "baza_agents.dump": pg_dump,
    }
    if extra:
        files.update(extra)
    return _make_dated_dir(root, "daily", stamp, files)


# ── _dated_dirs ──────────────────────────────────────────────────────────────

def test_dated_dirs_dedupes_daily_and_weekly(tmp_path):
    root = str(tmp_path)
    _make_dated_dir(root, "daily", "2026-06-28T03-19-51", {"a": b"x"})
    _make_dated_dir(root, "weekly", "2026-06-28T03-19-51", {"a": b"x"})
    _make_dated_dir(root, "daily", "2026-06-29T03-19-30", {"a": b"x"})

    dirs = bv._dated_dirs(root)
    assert [s for s, _ in dirs] == ["2026-06-28T03-19-51", "2026-06-29T03-19-30"]


def test_dated_dirs_ignores_non_stamp_names(tmp_path):
    root = str(tmp_path)
    _make_dated_dir(root, "daily", "not-a-stamp", {"a": b"x"})
    _make_dated_dir(root, "daily", "2026-06-29T03-19-30", {"a": b"x"})

    dirs = bv._dated_dirs(root)
    assert [s for s, _ in dirs] == ["2026-06-29T03-19-30"]


def test_dated_dirs_empty_when_root_missing(tmp_path):
    assert bv._dated_dirs(os.path.join(str(tmp_path), "nope")) == []


# ── _dir_size ────────────────────────────────────────────────────────────────

def test_dir_size_sums_files(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "a").write_bytes(b"12345")
    (d / "b").write_bytes(b"1234567890")
    assert bv._dir_size(str(d)) == 15


# ── _check_sqlite_backup (real gzip + sqlite3, no mocking) ──────────────────

def test_check_sqlite_backup_valid_db_ok(tmp_path):
    p = tmp_path / "baza_projects.db.gz"
    p.write_bytes(_valid_sqlite_gz_bytes())
    assert bv._check_sqlite_backup(str(p)) is None


def test_check_sqlite_backup_corrupt_db_flagged(tmp_path):
    p = tmp_path / "baza_projects.db.gz"
    p.write_bytes(_corrupt_sqlite_gz_bytes())
    problem = bv._check_sqlite_backup(str(p))
    assert problem is not None
    assert "integrity_check failed" in problem


def test_check_sqlite_backup_bad_gzip_flagged(tmp_path):
    p = tmp_path / "baza_projects.db.gz"
    p.write_bytes(b"not actually gzip data at all")
    problem = bv._check_sqlite_backup(str(p))
    assert problem is not None
    assert "gunzip" in problem.lower()


def test_check_sqlite_backup_text_dump_skips_pragma(tmp_path, monkeypatch):
    """A .sql.gz text dump only gets gunzip -t -- PRAGMA integrity_check
    would fail against SQL text, not a real sqlite file, so it must never
    be invoked for this extension."""
    p = tmp_path / "baza_projects.sql.gz"
    p.write_bytes(gzip.compress(b"-- not really sql, just needs to be valid gzip"))

    calls = []
    real_sh = bv._sh

    def spy(cmd, timeout=60):
        calls.append(cmd)
        return real_sh(cmd, timeout)

    monkeypatch.setattr(bv, "_sh", spy)
    assert bv._check_sqlite_backup(str(p)) is None
    assert any("gunzip -t" in c for c in calls)
    assert not any("sqlite3" in c for c in calls)


def test_check_sqlite_backup_text_dump_bad_gzip_flagged(tmp_path):
    p = tmp_path / "baza_projects.sql.gz"
    p.write_bytes(b"not gzip at all")
    problem = bv._check_sqlite_backup(str(p))
    assert problem is not None
    assert "gunzip -t failed" in problem


# ── _check_pg_dump ───────────────────────────────────────────────────────────

def test_check_pg_dump_custom_format_ok(tmp_path, monkeypatch):
    p = tmp_path / "baza_agents.dump"
    p.write_bytes(b"fake custom-format dump")
    monkeypatch.setattr(bv, "_sh", lambda cmd, timeout=60: (0, "some pg_restore -l listing", ""))
    assert bv._check_pg_dump(str(p)) is None


def test_check_pg_dump_custom_format_fail(tmp_path, monkeypatch):
    p = tmp_path / "baza_agents.dump"
    p.write_bytes(b"corrupt")
    monkeypatch.setattr(
        bv, "_sh",
        lambda cmd, timeout=60: (1, "", "pg_restore: error: input file does not appear to be a valid archive"),
    )
    problem = bv._check_pg_dump(str(p))
    assert problem is not None
    assert "pg_restore -l failed" in problem


def test_check_pg_dump_text_ok(tmp_path):
    p = tmp_path / "baza_agents.sql"
    p.write_bytes(b"-- PostgreSQL database dump\n-- stuff")
    assert bv._check_pg_dump(str(p)) is None  # real `head -c 200`, no mocking needed


def test_check_pg_dump_text_missing_header_fail(tmp_path):
    p = tmp_path / "baza_agents.sql"
    p.write_bytes(b"just some random text with no banner at all")
    problem = bv._check_pg_dump(str(p))
    assert problem is not None
    assert "header" in problem.lower()


def test_check_pg_dump_text_gz_ok(tmp_path):
    p = tmp_path / "baza_agents.sql.gz"
    p.write_bytes(gzip.compress(b"-- PostgreSQL database dump\n-- stuff"))
    assert bv._check_pg_dump(str(p)) is None  # real `gunzip -c | head -c 200`


# ── verify() end-to-end ──────────────────────────────────────────────────────
# _check_pg_dump is monkeypatched to a no-op in most of these since these
# tests care about the freshness/size/dir-discovery logic, not pg_restore
# (which is covered directly above) and there's no live postgres archive to
# restore against here.

def test_verify_clean_backup_no_problems(tmp_path, monkeypatch):
    root = str(tmp_path)
    _fresh_backup(root, "2026-06-25T03-19-16")
    _fresh_backup(root, "2026-06-26T03-19-45")
    monkeypatch.setattr(bv, "_check_pg_dump", lambda path: None)

    assert bv.verify(root) == []


def test_verify_no_backups_at_all(tmp_path):
    problems = bv.verify(str(tmp_path))
    assert len(problems) == 1
    assert "no dated backup" in problems[0]


def test_verify_stale_backup_flagged(tmp_path, monkeypatch):
    root = str(tmp_path)
    newest = _fresh_backup(root, "2026-06-20T03-16-25")
    old_time = time.time() - 30 * 3600  # 30h ago -- past the 26h threshold
    os.utime(newest, (old_time, old_time))
    monkeypatch.setattr(bv, "_check_pg_dump", lambda path: None)

    problems = bv.verify(root)
    assert any(">26h" in p for p in problems)


def test_verify_fresh_backup_not_flagged(tmp_path, monkeypatch):
    root = str(tmp_path)
    _fresh_backup(root, "2026-07-02T03-15-52")
    monkeypatch.setattr(bv, "_check_pg_dump", lambda path: None)

    problems = bv.verify(root)
    assert not any("h old" in p for p in problems)


def test_verify_size_shrink_flagged(tmp_path, monkeypatch):
    root = str(tmp_path)
    _fresh_backup(root, "2026-06-25T03-19-16", extra={"big.bin": b"x" * 1_000_000})
    _fresh_backup(root, "2026-06-26T03-19-45", extra={"tiny.bin": b"x" * 10})
    monkeypatch.setattr(bv, "_check_pg_dump", lambda path: None)

    problems = bv.verify(root)
    assert any("<50%" in p for p in problems)


def test_verify_size_ok_not_flagged(tmp_path, monkeypatch):
    root = str(tmp_path)
    _fresh_backup(root, "2026-06-25T03-19-16", extra={"big.bin": b"x" * 1000})
    _fresh_backup(root, "2026-06-26T03-19-45", extra={"big.bin": b"x" * 950})
    monkeypatch.setattr(bv, "_check_pg_dump", lambda path: None)

    problems = bv.verify(root)
    assert not any("<50%" in p for p in problems)


def test_verify_missing_sqlite_file_flagged(tmp_path, monkeypatch):
    root = str(tmp_path)
    path = os.path.join(root, "daily", "2026-06-26T03-19-45")
    os.makedirs(path)
    with open(os.path.join(path, "baza_agents.dump"), "wb") as f:
        f.write(b"fake")
    monkeypatch.setattr(bv, "_check_pg_dump", lambda p: None)

    problems = bv.verify(root)
    assert any("no baza_projects" in p for p in problems)


def test_verify_missing_pg_dump_flagged(tmp_path):
    root = str(tmp_path)
    path = os.path.join(root, "daily", "2026-06-26T03-19-45")
    os.makedirs(path)
    with open(os.path.join(path, "baza_projects.db.gz"), "wb") as f:
        f.write(_valid_sqlite_gz_bytes())

    problems = bv.verify(root)
    assert any("no baza_agents" in p for p in problems)


def test_verify_corrupt_sqlite_flagged(tmp_path, monkeypatch):
    root = str(tmp_path)
    _fresh_backup(root, "2026-06-26T03-19-45", sqlite_gz=_corrupt_sqlite_gz_bytes())
    monkeypatch.setattr(bv, "_check_pg_dump", lambda p: None)

    problems = bv.verify(root)
    assert any("integrity_check failed" in p for p in problems)


def test_verify_bad_pg_dump_flagged(tmp_path, monkeypatch):
    root = str(tmp_path)
    _fresh_backup(root, "2026-06-26T03-19-45")
    monkeypatch.setattr(bv, "_check_pg_dump", lambda p: "pg_restore -l failed for baza_agents.dump (rc=1): boom")

    problems = bv.verify(root)
    assert any("pg_restore -l failed" in p for p in problems)


def test_verify_picks_newest_and_previous_by_stamp(tmp_path, monkeypatch):
    """Three backups present -- verify() must compare the newest against the
    second-newest, not against the oldest."""
    root = str(tmp_path)
    _fresh_backup(root, "2026-06-24T03-19-16", extra={"big.bin": b"x" * 1000})
    _fresh_backup(root, "2026-06-25T03-19-16", extra={"big.bin": b"x" * 10})  # shrunk vs. oldest
    _fresh_backup(root, "2026-06-26T03-19-45", extra={"big.bin": b"x" * 12})  # ~same size as immediate prev
    monkeypatch.setattr(bv, "_check_pg_dump", lambda p: None)

    problems = bv.verify(root)
    assert not any("<50%" in p for p in problems)


# ── main() wiring (alert vs. fyi routing, via a tmp cron_health.db) ─────────

@pytest.fixture()
def ch_bv(monkeypatch, tmp_path):
    """Fresh core.cron_health_db + agents.cron_helpers + backup_verify, all
    bound to a tmp cron_health.db, mirroring tests/test_cron_helpers_routing.py's
    `ch` fixture. Deleting the cached modules forces DB_PATH (baked in at
    core.cron_health_db import time) to pick up the tmp path."""
    db_path = str(tmp_path / "cron_health.db")
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", db_path)
    for mod in ("core.cron_health_db", "agents.cron_helpers", "agents.claw_batto.crons.backup_verify"):
        if mod in sys.modules:
            del sys.modules[mod]

    chdb = importlib.import_module("core.cron_health_db")
    chdb.init()
    importlib.import_module("agents.cron_helpers")
    mod = importlib.import_module("agents.claw_batto.crons.backup_verify")
    return mod


@pytest.fixture()
def posted(monkeypatch):
    calls = []

    def fake_post_html(token, chat_id, text, *args, **kwargs):
        calls.append({"token": token, "chat_id": chat_id, "text": text})
        return True

    import core.telegram_fmt as telegram_fmt
    monkeypatch.setattr(telegram_fmt, "post_html", fake_post_html)
    return calls


def test_main_sends_alert_when_problems_found(ch_bv, posted, monkeypatch):
    monkeypatch.setattr(ch_bv, "verify", lambda root: ["fake problem one"])
    ch_bv.main()
    assert len(posted) == 1
    assert "fake problem one" in posted[0]["text"]


def test_main_reports_fyi_when_clean(ch_bv, posted, monkeypatch):
    # in_quiet_hours is called by send_report(), which is defined in
    # agents.cron_helpers -- its global lookup resolves against that
    # module's own namespace, not backup_verify's `import *`-copied name,
    # so it must be patched there.
    import agents.cron_helpers as helpers
    monkeypatch.setattr(ch_bv, "verify", lambda root: [])
    monkeypatch.setattr(helpers, "in_quiet_hours", lambda *a, **k: False)
    ch_bv.main()
    assert len(posted) == 1
    assert "OK" in posted[0]["text"]


def test_main_dedupes_repeat_alert(ch_bv, posted, monkeypatch):
    monkeypatch.setattr(ch_bv, "verify", lambda root: ["same problem"])
    ch_bv.main()
    ch_bv.main()
    assert len(posted) == 1  # second call deduped by should_alert's renotify window
