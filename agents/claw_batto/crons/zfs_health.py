#!/usr/bin/env python3
"""Claw Batto — weekly ZFS pool health check.

Checks:
  - `zpool status -x` reports "all pools are healthy" (anything else, e.g.
    a DEGRADED/FAULTED pool, is a problem -- the full status text is
    included)
  - `zpool list -H -o name,capacity` -- any pool over 85% capacity
  - last scrub, parsed from the `scan:` line of `zpool status` -- >45 days
    old is a problem, and so is never having scrubbed at all ("scan: none
    requested" or any other unparsable scan line)
  - `sudo -n smartctl -H <device>` for every device path in
    `zpool status -P` -- a sudo/tooling denial (no smartctl installed, no
    NOPASSWD entry, etc.) degrades to an info line ("smart: unavailable
    (sudo)"), NOT a problem, per the global privileged-shell rule
    (`sudo -n …`, degrade gracefully on denial); an actual FAILED SMART
    health readout IS a problem

check() is the pure check function -- no args, returns (problems,
info_lines), no Telegram/DB side effects. All subprocess access goes
through the module-level _sh() so tests monkeypatch it directly. main()
wraps check() in cron_run()'s heartbeat and routes the result through
send_alert() (problems found) or send_report() (clean, though info_lines
are still surfaced in that report).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *  # noqa: F401,F403 (cron_run, send_alert, send_report, log, now, ...)

import re
import shlex
import subprocess
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLAW-ZFS] %(message)s")

AGENT_TOKEN = os.getenv("TELEGRAM_CLAW_BATTO", TELEGRAM_TOKEN)
CAPACITY_MAX_PCT = 85
SCRUB_MAX_DAYS = 45
HEALTHY_TEXT = "all pools are healthy"
SMART_UNAVAILABLE_INFO = "smart: unavailable (sudo)"

_SCAN_LINE_RE = re.compile(r"^\s*scan:\s*(.+)$", re.MULTILINE)
_SCAN_DATE_RE = re.compile(r"on\s+(\w{3}\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})")


def _sh(cmd: str, timeout: int = 30):
    """Run a shell command, return (returncode, stdout, stderr) as text.
    Never raises -- launch failures/timeouts come back as (1, "", err).
    Module-level so tests monkeypatch it directly (zfs_health._sh)."""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)


def _devices_from_status(status_text: str) -> list[str]:
    """Pull device paths (e.g. /dev/sda1) out of a `zpool status -P` block.
    Only lines whose first token is a /dev/ path are devices; pool/vdev
    header lines (NAME, empirepool, raidz2-0, ...) are skipped."""
    devices = []
    for line in status_text.splitlines():
        tokens = line.split()
        if tokens and tokens[0].startswith("/dev/"):
            devices.append(tokens[0])
    return devices


def _scrub_age_days(status_text: str) -> float | None:
    """Days since the last completed scrub, parsed from the `scan:` line of
    `zpool status`. None if there's no parseable completion date -- covers
    'scan: none requested' and any other non-matching scan line. check()
    treats None as its own problem ('never scrubbed')."""
    m = _SCAN_LINE_RE.search(status_text)
    if not m:
        return None
    date_m = _SCAN_DATE_RE.search(m.group(1))
    if not date_m:
        return None
    try:
        dt = datetime.strptime(date_m.group(1), "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    return (datetime.now() - dt).total_seconds() / 86400


def check() -> tuple[list[str], list[str]]:
    """Pure check: no args, returns (problems, info_lines). No Telegram/DB
    side effects -- main() handles reporting."""
    problems: list[str] = []
    info: list[str] = []

    # -x: terse healthy/unhealthy summary (full status of unhealthy pools
    # only, or the literal "all pools are healthy" one-liner)
    rc, out, err = _sh("zpool status -x")
    summary = out.strip()
    if summary.lower() != HEALTHY_TEXT:
        if rc != 0 and not summary:
            problems.append(f"zpool status -x failed (rc={rc}): {err.strip()[:200]}")
        else:
            problems.append(f"zpool status -x: {summary or '(no output)'}")

    # capacity
    rc, out, err = _sh("zpool list -H -o name,capacity")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        pool, cap = parts
        try:
            pct_val = int(cap.rstrip("%"))
        except ValueError:
            continue
        if pct_val > CAPACITY_MAX_PCT:
            problems.append(f"zpool {pool} capacity {pct_val}% (>{CAPACITY_MAX_PCT}%)")

    # scrub age (needs the full, non -x/-P status for the scan: line)
    rc, full_status, err = _sh("zpool status")
    age_days = _scrub_age_days(full_status)
    if age_days is None:
        problems.append("zpool status: no completed scrub found (never scrubbed)")
    elif age_days > SCRUB_MAX_DAYS:
        problems.append(f"last scrub was {age_days:.1f} days ago (>{SCRUB_MAX_DAYS}d)")

    # SMART per device (full device paths need -P)
    rc, status_p, err = _sh("zpool status -P")
    devices = _devices_from_status(status_p)
    smart_unavailable = False
    for dev in devices:
        rc, out, err = _sh(f"sudo -n smartctl -H {shlex.quote(dev)}")
        text = out.lower()
        if "overall-health" not in text:
            smart_unavailable = True
            continue
        if "failed" in text:
            problems.append(f"smartctl reports FAILED health for {dev}")
    if smart_unavailable:
        info.append(SMART_UNAVAILABLE_INFO)

    return problems, info


def main():
    with cron_run("zfs_health"):
        log.info("Starting ZFS pool health check...")
        problems, info = check()
        if problems:
            msg = "ZFS HEALTH — problems found\n\n" + "\n".join(f"- {p}" for p in problems)
            if info:
                msg += "\n\n" + "\n".join(f"({i})" for i in info)
            log.warning(msg)
            send_alert("zfs_health", msg, alert_key="zfs_health:fail",
                        renotify_hours=24, token=AGENT_TOKEN)
        else:
            msg = f"ZFS health OK — {now()}"
            if info:
                msg += "\n" + "\n".join(f"({i})" for i in info)
            log.info(msg)
            send_report("zfs_health", msg, priority="fyi",
                        delta_key="zfs_health", token=AGENT_TOKEN)
        log.info("Done.")


if __name__ == "__main__":
    main()
