"""Read-only Cron Health panel — Task 10 of the cron-improvements plan.

GET /crons/health   — server-rendered page: declared crons (config/agents.yaml
                       scheduled_tasks) joined with heartbeat data from
                       core.cron_health_db, systemd baza-* timers, and the
                       last 50 recorded runs.
GET /api/crons/status — same data as JSON.

Deliberately mounted at /crons/health rather than the bare /crons the task
brief names literally: /crons (and dashboard/templates/crons.html) are
already an unrelated, pre-existing, actively-used manual crontab editor
("Cron Hub", dating to 2026-03-26 — see app.py's crons_page()/list_crons()).
Overwriting that URL/template would destroy working functionality with no
overlap in behavior (that page mutates the real crontab; this one is a
read-only reporting surface over the newer declarative agents.yaml +
cron_health.db + systemd-timer system). See task-10-report.md for details.

Everything here is read-only: no writes to config/agents.yaml, no crontab
mutation, no systemd unit changes.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

import yaml
from croniter import croniter
from flask import Blueprint, jsonify, render_template

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import cron_health_db  # noqa: E402

crons_panel_bp = Blueprint("crons_panel", __name__)

_ERROR_TAIL_LIMIT = 200
_RECENT_RUNS_LIMIT = 50
_SYSTEMCTL_TIMEOUT_S = 8

_STATUS_ICON = {"ok": "✅", "error": "❌", "timeout": "⏱️"}

_TIMER_TRAIL_RE = re.compile(r"(\S*\.timer)\s+(\S*\.service)\s*$")
_TIMER_DT_RE = re.compile(
    r"[A-Za-z]{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+|n/a"
)
_TIMERS_FOOTER_RE = re.compile(r"^\d+ timers? listed\.?$")


def _agents_yaml_path() -> str:
    return os.path.join(_ROOT, "config", "agents.yaml")


# ── Declared crons (config/agents.yaml, read-only) ─────────────────────────

def load_declared_crons() -> list[dict]:
    """Parse scheduled_tasks blocks out of config/agents.yaml. Never writes."""
    path = _agents_yaml_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return []

    agents = data.get("agents", data) if isinstance(data, dict) else {}
    if not isinstance(agents, dict):
        return []

    out = []
    for agent_id, cfg in agents.items():
        if not isinstance(cfg, dict):
            continue
        for t in cfg.get("scheduled_tasks", []) or []:
            if not isinstance(t, dict):
                continue
            out.append(
                {
                    "agent": agent_id,
                    "name": t.get("name") or "",
                    "schedule": t.get("schedule") or "",
                    "enabled": bool(t.get("enabled", True)),
                    "script": t.get("script") or "",
                    "log": t.get("log") or "",
                }
            )
    out.sort(key=lambda d: (d["agent"], d["name"]))
    return out


def _next_fire_iso(schedule: str, now: datetime | None = None) -> str | None:
    if not schedule:
        return None
    try:
        base = now or datetime.now()
        return croniter(schedule, base).get_next(datetime).isoformat(timespec="seconds")
    except (ValueError, KeyError, TypeError):
        return None


def _error_tail(error, limit: int = _ERROR_TAIL_LIMIT) -> str | None:
    if not error:
        return None
    text = str(error)
    return text[-limit:]


def _status_icon(status) -> str:
    return _STATUS_ICON.get(status, "—")  # em dash = "never run / unknown"


def _safe_last_runs_by_cron() -> tuple[dict, str | None]:
    """cron_health_db.last_runs_by_cron(), degrading to ({}, error) on
    sqlite3.Error instead of raising.

    Covers the fresh-deploy/restore case where dashboard/cron_health.db
    exists on disk but core.cron_health_db.init() never ran against it (no
    ``cron_runs`` table yet) -- register() below now calls init() itself,
    but this read path must degrade independently too rather than trust
    that init() always succeeded first.
    """
    try:
        return cron_health_db.last_runs_by_cron(), None
    except sqlite3.Error as e:
        return {}, str(e)


def _safe_recent_runs(limit: int) -> tuple[list, str | None]:
    """cron_health_db.recent_runs(), degrading to ([], error) on sqlite3.Error."""
    try:
        return list(cron_health_db.recent_runs(limit=limit)), None
    except sqlite3.Error as e:
        return [], str(e)


def _declared_with_health_and_error(
    now: datetime | None = None,
) -> tuple[list[dict], str | None]:
    declared = load_declared_crons()
    last_runs, err = _safe_last_runs_by_cron()

    rows = []
    for d in declared:
        row = dict(d)
        row["next_fire"] = _next_fire_iso(d["schedule"], now=now)
        last = last_runs.get(d["name"])
        if last is not None:
            row["last_run"] = {
                "status": last["status"],
                "status_icon": _status_icon(last["status"]),
                "started_at": last["started_at"],
                "finished_at": last["finished_at"],
                "duration_s": last["duration_s"],
                "error_tail": _error_tail(last["error"]),
                "host": last["host"],
            }
        else:
            row["last_run"] = None
        rows.append(row)
    return rows, err


def get_declared_with_health(now: datetime | None = None) -> list[dict]:
    """Declared crons joined with core.cron_health_db.last_runs_by_cron() + next-fire.

    Degrades to last_run=None for every row (never raises) if the health DB
    can't be read -- see _safe_last_runs_by_cron().
    """
    rows, _err = _declared_with_health_and_error(now=now)
    return rows


# ── systemd baza-* timers ───────────────────────────────────────────────────

def _parse_list_timers(output: str) -> list[dict]:
    """Defensively parse `systemctl list-timers 'baza-*' --all --no-pager`.

    Column widths in systemctl's tabular output vary (right-aligned NEXT/LEFT/
    LAST/PASSED columns can be separated by a single space), so we anchor on
    the two reliable tokens instead: the trailing `<name>.timer  <name>.service`
    pair, and the two `Www YYYY-MM-DD HH:MM:SS TZ` (or `n/a`) datetime tokens
    that precede it. Anything we can't confidently parse is skipped rather
    than raising — this must degrade gracefully, never 500 the page.
    """
    rows = []
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("NEXT"):
            continue
        if _TIMERS_FOOTER_RE.match(stripped):
            continue

        m_trail = _TIMER_TRAIL_RE.search(line)
        if not m_trail:
            continue
        unit, activates = m_trail.group(1), m_trail.group(2)
        head = line[: m_trail.start()].rstrip()

        dt_matches = list(_TIMER_DT_RE.finditer(head))
        next_fire, left, last_ran, passed = "", "", "", ""
        if len(dt_matches) >= 1:
            next_fire = dt_matches[0].group(0)
        if len(dt_matches) >= 2:
            left = head[dt_matches[0].end():dt_matches[1].start()].strip()
            last_ran = dt_matches[1].group(0)
            passed = head[dt_matches[1].end():].strip()
        elif len(dt_matches) == 1:
            left = head[dt_matches[0].end():].strip()

        rows.append(
            {
                "unit": unit,
                "activates": activates,
                "next": next_fire,
                "left": left,
                "last": last_ran,
                "passed": passed,
            }
        )
    return rows


def get_systemd_timers() -> dict:
    """`systemctl list-timers 'baza-*' --all --no-pager`, parsed defensively.

    Subprocess failure (missing binary, timeout, non-zero exit) degrades to
    {"available": False, "timers": [], "error": "..."} — never raises, so the
    page still renders.
    """
    try:
        proc = subprocess.run(
            ["systemctl", "list-timers", "baza-*", "--all", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"available": False, "timers": [], "error": f"unavailable: {e}"[:300]}

    if proc.returncode != 0:
        err = (proc.stderr or "unavailable").strip()[:300]
        return {"available": False, "timers": [], "error": err}

    return {"available": True, "timers": _parse_list_timers(proc.stdout), "error": None}


# ── Combined payload ─────────────────────────────────────────────────────────

def get_status_payload() -> dict:
    declared, last_runs_err = _declared_with_health_and_error()
    timers = get_systemd_timers()
    recent_rows, recent_err = _safe_recent_runs(_RECENT_RUNS_LIMIT)

    recent = []
    for r in recent_rows:
        recent.append(
            {
                "id": r["id"],
                "cron_name": r["cron_name"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "status": r["status"],
                "status_icon": _status_icon(r["status"]),
                "duration_s": r["duration_s"],
                "error_tail": _error_tail(r["error"]),
                "host": r["host"],
            }
        )

    # Same "degrade with a visible marker" shape as systemd_timers above --
    # lets the JSON API (and any future template update) tell "no runs yet"
    # apart from "couldn't read cron_health.db".
    db_error = last_runs_err or recent_err
    health_db = {"available": db_error is None, "error": db_error}

    return {
        "declared": declared,
        "systemd_timers": timers,
        "recent_runs": recent,
        "cron_health_db": health_db,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@crons_panel_bp.route("/crons/health")
def crons_health_page():
    payload = get_status_payload()
    return render_template("crons_health.html", nav_active="cronhealth", **payload)


@crons_panel_bp.route("/api/crons/status")
def crons_status_api():
    return jsonify(get_status_payload())


def register(app) -> None:
    # Mirrors the try/except init() pattern sibling blueprints use at
    # register() time (e.g. dashboard/app.py's bin_store.init_bin_db()
    # around bin_routes' registration): a fresh deploy/restore can leave
    # dashboard/cron_health.db present on disk without its schema, and
    # init() failing here (permissions, disk full, corrupt file, ...) must
    # never take down the rest of the dashboard. The read paths above
    # degrade gracefully on their own too, so a still-missing schema after
    # this just means "no heartbeat data" instead of a 500.
    try:
        cron_health_db.init()
    except Exception as e:
        print(f"[crons_panel] cron_health_db.init() failed: {e}")
    app.register_blueprint(crons_panel_bp)
