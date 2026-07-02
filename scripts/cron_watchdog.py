#!/usr/bin/env python3
"""Claw Batto — cron-level watchdog: missed-schedule + error-streak detection,
plus crontab drift check.

Runs every 30 min (registered under claw_batto's scheduled_tasks). Reads
config/agents.yaml `scheduled_tasks` (enabled only), compares each cron's
recent run history in dashboard/cron_health.db (written by every retrofitted
cron via agents.cron_helpers.cron_run()/record_run_*) against its expected
fire schedule via croniter, and alerts through send_alert() when:

  - "missed": the cron hasn't run since its 2nd-previous scheduled fire time
    (minus a 15-minute grace window). A cron that has never run at all is
    treated the same way (its "last run" is effectively -infinity), but the
    alert-key dedup (renotify_hours=6) caps how often that nags.
  - "errors": the last 3 recorded runs all finished with status != "ok".

Also shells out to `sync-agent-crons.py --check`; a nonzero exit means the
crontab has drifted from what agents.yaml declares, alerted via a single
`cronwd:drift` key (renotify_hours=24).

Standalone `venv/bin/python` executable — imports agents/cron_helpers.py via
the same FRAMEWORK_DIR sys.path pattern used by every other cron script
(agents/claw_batto/crons/infra_health.py).
"""
import os
import sys
import logging
import subprocess
from contextlib import contextmanager
from datetime import datetime, timedelta

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK_DIR)

import yaml
from croniter import croniter

from agents.cron_helpers import send_telegram
from core import cron_health_db as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CRON-WATCHDOG] %(message)s")
log = logging.getLogger("cron_watchdog")

AGENTS_YAML = os.path.join(FRAMEWORK_DIR, "config", "agents.yaml")
SYNC_SCRIPT = os.path.join(FRAMEWORK_DIR, "scripts", "sync-agent-crons.py")

GRACE_MINUTES = 15
ERROR_STREAK_LEN = 3
MISSED_RENOTIFY_HOURS = 6
DRIFT_RENOTIFY_HOURS = 24


def expected_prev_fire(schedule: str, now: datetime) -> datetime:
    """Most recent scheduled fire time strictly before `now`."""
    return croniter(schedule, now).get_prev(datetime)


def load_declared_crons(agents_yaml_path: str = AGENTS_YAML) -> list[dict]:
    """Parse agents.yaml `scheduled_tasks` (enabled only).

    Returns [{"agent": agent_id, "name": task_name, "schedule": cron_expr}, ...].
    `name` is the *bare* task name (e.g. "infra_health") — the same name each
    cron script passes to `cron_run()`/`record_run_start()` — not the
    agent-prefixed name sync-agent-crons.py uses for crontab entry markers.
    """
    if not os.path.isfile(agents_yaml_path):
        return []
    with open(agents_yaml_path) as f:
        data = yaml.safe_load(f) or {}
    declared = []
    agents = data.get("agents", data) or {}
    for agent_id, cfg in agents.items():
        if not isinstance(cfg, dict):
            continue
        for t in cfg.get("scheduled_tasks", []) or []:
            if not t.get("enabled", True):
                continue
            declared.append({
                "agent": agent_id,
                "name": t["name"],
                "schedule": t["schedule"],
            })
    return declared


def find_problems(declared: list[dict], runs: dict, now: datetime) -> list[dict]:
    """Compare declared crons against their recent run history.

    `runs` maps cron name -> list of run dicts/rows (newest-first; only the
    first ERROR_STREAK_LEN entries are consulted), each exposing
    "started_at" (ISO string or None/missing) and "status"
    ("ok"/"error"/"timeout"/None-for-in-flight).

    Returns a list of problem dicts, each with at least
    {"type": "missed"|"errors", "name", "agent", "schedule", "detail"}.
    """
    problems = []
    for cron in declared:
        name = cron["name"]
        schedule = cron["schedule"]
        history = runs.get(name) or []

        # -- missed: no run since the 2nd-previous scheduled fire (grace)
        prev1 = expected_prev_fire(schedule, now)
        prev2 = expected_prev_fire(schedule, prev1)
        deadline = prev2 - timedelta(minutes=GRACE_MINUTES)

        last_start = None
        if history:
            started_at = history[0].get("started_at") if hasattr(history[0], "get") else history[0]["started_at"]
            if started_at:
                try:
                    last_start = datetime.fromisoformat(started_at)
                except (TypeError, ValueError):
                    last_start = None

        if last_start is None or last_start < deadline:
            when = last_start.isoformat(timespec="minutes") if last_start else "never"
            problems.append({
                "type": "missed",
                "name": name,
                "agent": cron["agent"],
                "schedule": schedule,
                "detail": (
                    f"{name} ({cron['agent']}): missed 2 scheduled fires — "
                    f"last run {when}, expected by "
                    f"{deadline.isoformat(timespec='minutes')} (schedule '{schedule}')"
                ),
            })

        # -- error streak: last N runs all non-"ok"
        if len(history) >= ERROR_STREAK_LEN:
            last_n = history[:ERROR_STREAK_LEN]
            statuses = [r["status"] for r in last_n]
            if all(s != "ok" for s in statuses):
                problems.append({
                    "type": "errors",
                    "name": name,
                    "agent": cron["agent"],
                    "schedule": schedule,
                    "detail": (
                        f"{name} ({cron['agent']}): last {ERROR_STREAK_LEN} runs "
                        f"all failed ({', '.join(str(s) for s in statuses)})"
                    ),
                })

    return problems


def check_drift() -> tuple[bool, str]:
    """Run `sync-agent-crons.py --check`. Returns (drifted, combined_output).

    A nonzero return code (drift found, or the check itself couldn't run)
    counts as drifted.
    """
    try:
        proc = subprocess.run(
            [sys.executable, SYNC_SCRIPT, "--check"],
            cwd=FRAMEWORK_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        return True, f"drift check failed to run: {e}"
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode != 0, output


def _resolve_send_alert():
    """`agents.cron_helpers.send_alert` (Task 4) if it has landed; otherwise a
    local should_alert()+send_telegram fallback with a matching signature, so
    this module works regardless of task ordering."""
    try:
        from agents.cron_helpers import send_alert as _send_alert
        return _send_alert
    except ImportError:
        def _send_alert(cron_name, message, alert_key, renotify_hours=None, **kwargs):
            ok, _row_id = db.should_alert(alert_key, renotify_hours)
            if not ok:
                return False
            send_telegram(message)
            return True
        return _send_alert


def _resolve_cron_run():
    """`agents.cron_helpers.cron_run` (Task 4) if present; otherwise a no-op
    context manager so this watchdog still runs standalone."""
    try:
        from agents.cron_helpers import cron_run as _cron_run
        return _cron_run
    except ImportError:
        @contextmanager
        def _noop(name):
            yield
        return _noop


def main():
    db.init()
    now = datetime.now()
    declared = load_declared_crons()

    runs = {}
    for cron in declared:
        rows = db.recent_runs(cron_name=cron["name"], limit=ERROR_STREAK_LEN)
        runs[cron["name"]] = [dict(r) for r in rows]

    problems = find_problems(declared, runs, now)
    send_alert = _resolve_send_alert()

    for p in problems:
        key = f"cronwd:{p['name']}:{p['type']}"
        send_alert("cron_watchdog", p["detail"], key, renotify_hours=MISSED_RENOTIFY_HOURS)
        log.warning(f"[{p['type']}] {p['detail']}")

    drifted, output = check_drift()
    if drifted:
        detail = "crontab drift detected (sync-agent-crons.py --check):\n" + output[:1500]
        send_alert("cron_watchdog", detail, "cronwd:drift", renotify_hours=DRIFT_RENOTIFY_HOURS)
        log.warning(detail)

    if not problems and not drifted:
        log.info(f"OK — {len(declared)} declared cron(s) healthy, no drift")

    return {"problems": problems, "drifted": drifted}


if __name__ == "__main__":
    cron_run = _resolve_cron_run()
    with cron_run("cron_watchdog"):
        main()
