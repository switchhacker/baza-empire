"""Tests for agents/claw_batto/crons/zfs_health.py — Task 12 of the
cron-improvements plan (weekly ZFS pool health: status, capacity, scrub age,
per-device SMART).

Canned `zpool status`/`zpool status -x`/`zpool status -P`/`zpool list` and
`smartctl` outputs (captured from the real pool on this box, plus synthetic
DEGRADED / old-scrub / >85% variants) drive _devices_from_status(),
_scrub_age_days(), and check() via a monkeypatched _sh(). main()'s
Telegram/cron_health_db wiring is covered separately with the same
fresh-reimport-against-a-tmp-DB fixture pattern as
tests/test_cron_helpers_routing.py.
"""
import importlib
import os
import sys
from datetime import datetime, timedelta

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import agents.claw_batto.crons.zfs_health as zh


# ── canned fixtures (captured from the real `empirepool` + synthetic variants) ──

HEALTHY_STATUS_X = "all pools are healthy\n"

HEALTHY_STATUS_FULL = """  pool: empirepool
 state: ONLINE
  scan: scrub repaired 0B in 00:19:16 with 0 errors on Sun Jun 14 00:43:17 2026
config:

\tNAME        STATE     READ WRITE CKSUM
\tempirepool  ONLINE       0     0     0
\t  raidz2-0  ONLINE       0     0     0
\t    sda     ONLINE       0     0     0
\t    sdb     ONLINE       0     0     0
\t    sdc     ONLINE       0     0     0
\t    sdd     ONLINE       0     0     0
\t    sde     ONLINE       0     0     0

errors: No known data errors
"""

HEALTHY_STATUS_P = """  pool: empirepool
 state: ONLINE
  scan: scrub repaired 0B in 00:19:16 with 0 errors on Sun Jun 14 00:43:17 2026
config:

\tNAME           STATE     READ WRITE CKSUM
\tempirepool     ONLINE       0     0     0
\t  raidz2-0     ONLINE       0     0     0
\t    /dev/sda1  ONLINE       0     0     0
\t    /dev/sdb1  ONLINE       0     0     0
\t    /dev/sdc1  ONLINE       0     0     0
\t    /dev/sdd1  ONLINE       0     0     0
\t    /dev/sde1  ONLINE       0     0     0

errors: No known data errors
"""

DEGRADED_STATUS_X = """  pool: empirepool
 state: DEGRADED
status: One or more devices are faulted in response to persistent errors.
\tSufficient replicas exist for the pool to continue functioning in a
\tdegraded state.
action: Replace the faulted device, or use 'zpool clear' to mark the device
\trepaired.
  scan: scrub repaired 0B in 00:19:16 with 0 errors on Sun Jun 14 00:43:17 2026
config:

\tNAME        STATE     READ WRITE CKSUM
\tempirepool  DEGRADED     0     0     0
\t  raidz2-0  DEGRADED     0     0     0
\t    sda     ONLINE       0     0     0
\t    sdb     FAULTED      0     0     0  too many errors
\t    sdc     ONLINE       0     0     0
\t    sdd     ONLINE       0     0     0
\t    sde     ONLINE       0     0     0

errors: No known data errors
"""

OLD_SCRUB_STATUS_FULL = """  pool: empirepool
 state: ONLINE
  scan: scrub repaired 0B in 00:19:16 with 0 errors on Wed Mar 4 00:43:17 2026
config:

\tNAME        STATE     READ WRITE CKSUM
\tempirepool  ONLINE       0     0     0

errors: No known data errors
"""

NEVER_SCRUBBED_STATUS_FULL = """  pool: empirepool
 state: ONLINE
  scan: none requested
config:

\tNAME        STATE     READ WRITE CKSUM
\tempirepool  ONLINE       0     0     0

errors: No known data errors
"""

CAPACITY_HIGH = "empirepool\t92%\n"
CAPACITY_OK = "empirepool\t1%\n"

SMARTCTL_PASSED = """smartctl 7.4 2023-08-01 r5530 [x86_64-linux-6.8.0] (local build)
=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED
"""

SMARTCTL_FAILED = """smartctl 7.4 2023-08-01 r5530 [x86_64-linux-6.8.0] (local build)
=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: FAILED!
"""

SUDO_DENIED_STDERR = "sudo: a password is required\n"
SMARTCTL_NOT_FOUND_STDERR = "sudo: smartctl: command not found\n"


def _sh_dispatch(table):
    """Build a fake _sh(cmd, timeout=...) that dispatches on substring match
    against `table`, a list of (substring, (rc, stdout, stderr)) pairs
    checked in order. Raises if a command doesn't match anything, so a test
    gap shows up as a failure instead of a silent empty-string fallthrough."""
    def fake(cmd, timeout=30):
        for needle, result in table:
            if needle in cmd:
                return result
        raise AssertionError(f"unexpected command in test: {cmd!r}")
    return fake


# ── _devices_from_status ─────────────────────────────────────────────────────

def test_devices_from_status_extracts_dev_paths():
    devices = zh._devices_from_status(HEALTHY_STATUS_P)
    assert devices == ["/dev/sda1", "/dev/sdb1", "/dev/sdc1", "/dev/sdd1", "/dev/sde1"]


def test_devices_from_status_skips_header_lines():
    devices = zh._devices_from_status(HEALTHY_STATUS_FULL)  # bare "sda" names, no -P
    assert devices == []


def test_devices_from_status_empty_input():
    assert zh._devices_from_status("") == []


# ── _scrub_age_days ──────────────────────────────────────────────────────────

def test_scrub_age_days_recent(monkeypatch):
    fixed_now = datetime(2026, 6, 14, 12, 0, 0)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(zh, "datetime", FakeDateTime)
    age = zh._scrub_age_days(HEALTHY_STATUS_FULL)
    assert age is not None
    assert 0 <= age < 1  # scrub finished ~11h before fixed_now on the same day


def test_scrub_age_days_old(monkeypatch):
    fixed_now = datetime(2026, 7, 2, 12, 0, 0)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(zh, "datetime", FakeDateTime)
    age = zh._scrub_age_days(OLD_SCRUB_STATUS_FULL)
    assert age is not None
    assert age > 45


def test_scrub_age_days_never_scrubbed_is_none():
    assert zh._scrub_age_days(NEVER_SCRUBBED_STATUS_FULL) is None


def test_scrub_age_days_no_scan_line_is_none():
    assert zh._scrub_age_days("pool: empirepool\nstate: ONLINE\n") is None


# ── check() ──────────────────────────────────────────────────────────────────

def test_check_all_healthy_no_problems(monkeypatch):
    # Uses the real clock (no datetime monkeypatch): HEALTHY_STATUS_FULL's
    # scrub date (Jun 14 2026) is well within 45 days of "today" (Jul 2 2026).
    table = [
        ("smartctl", (1, "", SMARTCTL_NOT_FOUND_STDERR)),  # smartctl not installed -> unavailable, not a problem
        ("zpool status -x", (0, HEALTHY_STATUS_X, "")),
        ("zpool list -H -o name,capacity", (0, CAPACITY_OK, "")),
        ("zpool status -P", (0, HEALTHY_STATUS_P, "")),
        ("zpool status", (0, HEALTHY_STATUS_FULL, "")),
    ]
    monkeypatch.setattr(zh, "_sh", _sh_dispatch(table))

    problems, info = zh.check()
    assert problems == []
    assert info == [zh.SMART_UNAVAILABLE_INFO]


def test_check_degraded_pool_flagged(monkeypatch):
    table = [
        ("smartctl", (1, "", SMARTCTL_NOT_FOUND_STDERR)),
        ("zpool status -x", (0, DEGRADED_STATUS_X, "")),
        ("zpool list -H -o name,capacity", (0, CAPACITY_OK, "")),
        ("zpool status -P", (0, HEALTHY_STATUS_P, "")),
        ("zpool status", (0, DEGRADED_STATUS_X, "")),
    ]
    monkeypatch.setattr(zh, "_sh", _sh_dispatch(table))

    problems, info = zh.check()
    assert any("DEGRADED" in p for p in problems)


def test_check_high_capacity_flagged(monkeypatch):
    table = [
        ("smartctl", (1, "", SMARTCTL_NOT_FOUND_STDERR)),
        ("zpool status -x", (0, HEALTHY_STATUS_X, "")),
        ("zpool list -H -o name,capacity", (0, CAPACITY_HIGH, "")),
        ("zpool status -P", (0, HEALTHY_STATUS_P, "")),
        ("zpool status", (0, HEALTHY_STATUS_FULL, "")),
    ]
    monkeypatch.setattr(zh, "_sh", _sh_dispatch(table))

    problems, info = zh.check()
    assert any("capacity 92%" in p for p in problems)


def test_check_low_capacity_not_flagged(monkeypatch):
    table = [
        ("smartctl", (1, "", SMARTCTL_NOT_FOUND_STDERR)),
        ("zpool status -x", (0, HEALTHY_STATUS_X, "")),
        ("zpool list -H -o name,capacity", (0, CAPACITY_OK, "")),
        ("zpool status -P", (0, HEALTHY_STATUS_P, "")),
        ("zpool status", (0, HEALTHY_STATUS_FULL, "")),
    ]
    monkeypatch.setattr(zh, "_sh", _sh_dispatch(table))

    problems, info = zh.check()
    assert not any("capacity" in p for p in problems)


def test_check_old_scrub_flagged(monkeypatch):
    table = [
        ("smartctl", (1, "", SMARTCTL_NOT_FOUND_STDERR)),
        ("zpool status -x", (0, HEALTHY_STATUS_X, "")),
        ("zpool list -H -o name,capacity", (0, CAPACITY_OK, "")),
        ("zpool status -P", (0, HEALTHY_STATUS_P, "")),
        ("zpool status", (0, OLD_SCRUB_STATUS_FULL, "")),
    ]
    monkeypatch.setattr(zh, "_sh", _sh_dispatch(table))

    problems, info = zh.check()
    assert any("last scrub was" in p and ">45d" in p for p in problems)


def test_check_never_scrubbed_flagged(monkeypatch):
    table = [
        ("smartctl", (1, "", SMARTCTL_NOT_FOUND_STDERR)),
        ("zpool status -x", (0, HEALTHY_STATUS_X, "")),
        ("zpool list -H -o name,capacity", (0, CAPACITY_OK, "")),
        ("zpool status -P", (0, HEALTHY_STATUS_P, "")),
        ("zpool status", (0, NEVER_SCRUBBED_STATUS_FULL, "")),
    ]
    monkeypatch.setattr(zh, "_sh", _sh_dispatch(table))

    problems, info = zh.check()
    assert any("never scrubbed" in p for p in problems)


def test_check_smart_sudo_denied_is_info_not_problem(monkeypatch):
    table = [
        ("smartctl", (1, "", SUDO_DENIED_STDERR)),
        ("zpool status -x", (0, HEALTHY_STATUS_X, "")),
        ("zpool list -H -o name,capacity", (0, CAPACITY_OK, "")),
        ("zpool status -P", (0, HEALTHY_STATUS_P, "")),
        ("zpool status", (0, HEALTHY_STATUS_FULL, "")),
    ]
    monkeypatch.setattr(zh, "_sh", _sh_dispatch(table))

    problems, info = zh.check()
    assert not any("smart" in p.lower() for p in problems)
    assert zh.SMART_UNAVAILABLE_INFO in info


def test_check_smart_passed_not_flagged(monkeypatch):
    table = [
        ("smartctl", (0, SMARTCTL_PASSED, "")),
        ("zpool status -x", (0, HEALTHY_STATUS_X, "")),
        ("zpool list -H -o name,capacity", (0, CAPACITY_OK, "")),
        ("zpool status -P", (0, HEALTHY_STATUS_P, "")),
        ("zpool status", (0, HEALTHY_STATUS_FULL, "")),
    ]
    monkeypatch.setattr(zh, "_sh", _sh_dispatch(table))

    problems, info = zh.check()
    assert problems == []
    assert info == []


def test_check_smart_failed_flagged(monkeypatch):
    table = [
        ("smartctl", (0, SMARTCTL_FAILED, "")),
        ("zpool status -x", (0, HEALTHY_STATUS_X, "")),
        ("zpool list -H -o name,capacity", (0, CAPACITY_OK, "")),
        ("zpool status -P", (0, HEALTHY_STATUS_P, "")),
        ("zpool status", (0, HEALTHY_STATUS_FULL, "")),
    ]
    monkeypatch.setattr(zh, "_sh", _sh_dispatch(table))

    problems, info = zh.check()
    assert any("FAILED" in p for p in problems)
    assert any(p.count("/dev/sd") for p in problems)  # names a device


def test_check_no_devices_no_smart_calls(monkeypatch):
    """zpool status -P with no /dev/ lines (e.g. -P unsupported/odd output)
    -> no smartctl calls at all, no crash, no spurious info line."""
    table = [
        ("smartctl", (1, "", "should not be called")),
        ("zpool status -x", (0, HEALTHY_STATUS_X, "")),
        ("zpool list -H -o name,capacity", (0, CAPACITY_OK, "")),
        ("zpool status -P", (0, "no devices here\n", "")),
        ("zpool status", (0, HEALTHY_STATUS_FULL, "")),
    ]
    monkeypatch.setattr(zh, "_sh", _sh_dispatch(table))

    problems, info = zh.check()
    assert info == []


# ── main() wiring (alert vs. fyi routing, via a tmp cron_health.db) ─────────

@pytest.fixture()
def ch_zh(monkeypatch, tmp_path):
    db_path = str(tmp_path / "cron_health.db")
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", db_path)
    for mod in ("core.cron_health_db", "agents.cron_helpers", "agents.claw_batto.crons.zfs_health"):
        if mod in sys.modules:
            del sys.modules[mod]

    chdb = importlib.import_module("core.cron_health_db")
    chdb.init()
    importlib.import_module("agents.cron_helpers")
    mod = importlib.import_module("agents.claw_batto.crons.zfs_health")
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


def test_main_sends_alert_when_problems_found(ch_zh, posted, monkeypatch):
    monkeypatch.setattr(ch_zh, "check", lambda: (["fake problem one"], []))
    ch_zh.main()
    assert len(posted) == 1
    assert "fake problem one" in posted[0]["text"]


def test_main_reports_fyi_when_clean(ch_zh, posted, monkeypatch):
    # in_quiet_hours is called by send_report(), which is defined in
    # agents.cron_helpers -- its global lookup resolves against that
    # module's own namespace, not zfs_health's `import *`-copied name, so
    # it must be patched there.
    import agents.cron_helpers as helpers
    monkeypatch.setattr(ch_zh, "check", lambda: ([], []))
    monkeypatch.setattr(helpers, "in_quiet_hours", lambda *a, **k: False)
    ch_zh.main()
    assert len(posted) == 1
    assert "OK" in posted[0]["text"]


def test_main_reports_fyi_with_info_lines(ch_zh, posted, monkeypatch):
    import agents.cron_helpers as helpers
    monkeypatch.setattr(ch_zh, "check", lambda: ([], [ch_zh.SMART_UNAVAILABLE_INFO]))
    monkeypatch.setattr(helpers, "in_quiet_hours", lambda *a, **k: False)
    ch_zh.main()
    assert len(posted) == 1
    assert ch_zh.SMART_UNAVAILABLE_INFO in posted[0]["text"]


def test_main_dedupes_repeat_alert(ch_zh, posted, monkeypatch):
    monkeypatch.setattr(ch_zh, "check", lambda: (["same problem"], []))
    ch_zh.main()
    ch_zh.main()
    assert len(posted) == 1  # second call deduped by should_alert's renotify window
