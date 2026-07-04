#!/usr/bin/env python3
"""
Claw Batto — weekly log rotation for logs/*.log (Python wrapper, Blocker B2
of the cron-improvements final-review pass).

scripts/rotate_logs.sh does the real work (invokes /usr/sbin/logrotate with
a framework-local --state file against configs/logrotate-baza.conf) but,
being a bare shell script, never called agents.cron_helpers.cron_run() to
heartbeat a run into dashboard/cron_health.db. scripts/cron_watchdog.py's
missed-schedule check compares each declared cron's run history against its
expected fire schedule -- with zero history ever recorded, rotate_logs would
be flagged "missed" forever the moment it's declared in agents.yaml.

This wrapper ports the same 3-line logrotate invocation into Python and
wraps it in cron_run("rotate_logs") so it heartbeats like every other
retrofitted cron in this repo. config/agents.yaml's `rotate_logs`
scheduled_tasks entry should point its `script` field at this file
(scripts/rotate_logs.py), not scripts/rotate_logs.sh -- see this task's
final report. rotate_logs.sh itself is left untouched and still works
standalone (e.g. if invoked directly, or by anything outside the cron
registry).

Standalone-executable: `venv/bin/python scripts/rotate_logs.py`.
"""
import os
import shutil
import subprocess
import sys

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK_DIR)

from agents.cron_helpers import cron_run, log

STATE_FILE = os.path.join(FRAMEWORK_DIR, "logs", ".logrotate.state")
CONF_FILE = os.path.join(FRAMEWORK_DIR, "configs", "logrotate-baza.conf")


def _find_logrotate() -> str | None:
    """Same resolution as rotate_logs.sh: prefer /usr/sbin/logrotate,
    fall back to whatever's on PATH."""
    preferred = "/usr/sbin/logrotate"
    if os.path.isfile(preferred) and os.access(preferred, os.X_OK):
        return preferred
    return shutil.which("logrotate")


def run_rotate() -> int:
    """Run logrotate against configs/logrotate-baza.conf with a
    framework-local state file -- the exact behavior of rotate_logs.sh,
    ported to Python so it can be wrapped in cron_run(). Returns the
    logrotate exit code (0 == success). Raises RuntimeError if the
    logrotate binary can't be found at all (mirrors the .sh script's
    `exit 1` in that case)."""
    logrotate_bin = _find_logrotate()
    if not logrotate_bin:
        raise RuntimeError(
            "rotate_logs: logrotate not found (checked /usr/sbin/logrotate and PATH)"
        )

    os.makedirs(os.path.join(FRAMEWORK_DIR, "logs"), exist_ok=True)

    proc = subprocess.run(
        [logrotate_bin, "--state", STATE_FILE, CONF_FILE],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        log.error(f"rotate_logs: logrotate exited {proc.returncode}: {proc.stderr.strip()}")
    else:
        log.info("rotate_logs: logrotate completed OK")
    return proc.returncode


def main():
    # retrofit-exempt: pure log maintenance, nothing to send -- heartbeat only.
    with cron_run("rotate_logs"):
        rc = run_rotate()
        if rc != 0:
            raise SystemExit(rc)


if __name__ == "__main__":
    main()
