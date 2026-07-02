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


def get_declared_with_health(now: datetime | None = None) -> list[dict]:
    """Declared crons joined with core.cron_health_db.last_runs_by_cron() + next-fire."""
    declared = load_declared_crons()
    last_runs = cron_health_db.last_runs_by_cron()

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
    declared = get_declared_with_health()
    timers = get_systemd_timers()

    recent = []
    for r in cron_health_db.recent_runs(limit=_RECENT_RUNS_LIMIT):
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

    return {
        "declared": declared,
        "systemd_timers": timers,
        "recent_runs": recent,
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
    app.register_blueprint(crons_panel_bp)
