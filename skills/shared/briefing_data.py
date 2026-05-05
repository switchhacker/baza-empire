#!/usr/bin/env python3
"""
Real Briefing Data — anti-hallucination feed for status reports.

Returns ONLY facts that actually happened in the system. Use this as the
source of truth for "Recent Wins", "Completed", "Active Tasks", "Empire
Pulse" sections — agents must cite output from this skill rather than
inventing achievements.

SKILL_ARGS:
  hours:       int (default 24) — lookback window
  agent:       optional — filter to one agent
  format:      "json" | "markdown" (default "markdown")

Sources:
  - PostgreSQL task_journal (real agent actions)
  - dashboard/baza_projects.db tasks table (status changes)
  - filesystem: dashboard/artifacts/ files modified within window

Empty result = empty briefing. Do not fabricate.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FRAMEWORK_DIR)

DB_PATH = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
ARTIFACTS_DIR = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")


def journal_window(hours: int, agent: str | None) -> list[dict]:
    """Recent task_journal entries from PostgreSQL."""
    out: list[dict] = []
    try:
        from core.context_db import get_conn, release_conn
        conn = get_conn()
        cur = conn.cursor()
        q = (
            "SELECT agent_id, task_type, task_description, result, success, created_at "
            "FROM task_journal WHERE created_at > now() - interval %s "
        )
        params: list = [f"{int(hours)} hours"]
        if agent:
            q += "AND agent_id = %s "
            params.append(agent)
        q += "ORDER BY created_at DESC LIMIT 100"
        cur.execute(q, params)
        for r in cur.fetchall():
            out.append({
                "agent": r[0], "kind": r[1],
                "description": (r[2] or "")[:160],
                "result": (r[3] or "")[:200],
                "success": bool(r[4]),
                "ts": r[5].isoformat() if r[5] else "",
            })
        cur.close()
        release_conn(conn)
    except Exception as e:
        out.append({"_error": f"task_journal read failed: {e}"})
    return out


def task_state(agent: str | None) -> dict:
    """Current open / completed-today counts from baza_projects.db."""
    by_agent: dict[str, dict[str, int]] = {}
    if not os.path.isfile(DB_PATH):
        return {}
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        rows = conn.execute(
            "SELECT assigned_to, status, COUNT(*) FROM tasks "
            "WHERE status IN ('pending','in_progress','blocked') "
            "GROUP BY assigned_to, status"
        ).fetchall()
        for who, st, n in rows:
            who = who or "_unassigned"
            by_agent.setdefault(who, {})[st] = n
        # Completed in last 24h
        rows = conn.execute(
            "SELECT assigned_to, COUNT(*) FROM tasks "
            "WHERE status='completed' AND date(updated_at) >= date('now','-1 day') "
            "GROUP BY assigned_to"
        ).fetchall()
        for who, n in rows:
            who = who or "_unassigned"
            by_agent.setdefault(who, {})["completed_24h"] = n
        conn.close()
    except Exception as e:
        by_agent["_error"] = {"detail": str(e)}
    if agent:
        return {agent: by_agent.get(agent, {})}
    return by_agent


def recent_artifacts(hours: int, agent: str | None) -> list[dict]:
    """Artifact files written within the window."""
    if not os.path.isdir(ARTIFACTS_DIR):
        return []
    cutoff = datetime.now() - timedelta(hours=hours)
    out: list[dict] = []
    for root, dirs, files in os.walk(ARTIFACTS_DIR):
        # Skip nested cloud + private subdirs the user doesn't want surfaced here
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('private',)]
        for fname in files:
            if fname.endswith('.meta'):
                continue
            full = os.path.join(root, fname)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(full))
            except FileNotFoundError:
                continue
            if mtime < cutoff:
                continue
            project_id = os.path.basename(root)
            # Try meta sidecar to get the agent attribution
            ag = ""
            try:
                with open(full + ".meta") as mf:
                    ag = (json.load(mf) or {}).get("agent_id", "")
            except Exception:
                # Fall back to filename prefix (e.g., "claw_batto_2026...")
                head = fname.split("_", 2)
                if len(head) >= 2 and head[0] in (
                    "simon", "claw", "sam", "nova", "phil", "rex", "duke", "scout"
                ):
                    ag = "_".join(head[:2])
            if agent and ag and ag != agent:
                continue
            out.append({
                "agent": ag,
                "project_id": project_id,
                "name": fname,
                "size": os.path.getsize(full),
                "modified": mtime.isoformat(),
                "url": f"/api/artifacts/serve/{project_id}/{fname}",
            })
    out.sort(key=lambda r: r["modified"], reverse=True)
    return out[:60]


def render_markdown(payload: dict) -> str:
    hours = payload["hours"]
    arts = payload["artifacts"]
    state = payload["task_state"]
    journal = payload["journal"]

    lines = [f"# Real Briefing Data — last {hours}h", ""]
    lines.append(f"**Generated:** {payload['generated']}")
    lines.append("")
    lines.append(f"## Artifacts saved ({len(arts)})")
    if arts:
        for a in arts[:20]:
            ts = a["modified"][:16].replace("T", " ")
            lines.append(f"- `{a['project_id']}/{a['name']}` ({a['size']//1024} KB, {a['agent'] or '?'}, {ts})")
    else:
        lines.append("- _no artifacts saved in window — nothing to claim as a 'win'_")

    lines.append("")
    lines.append("## Task state (open + completed-24h)")
    if not state:
        lines.append("- _no task records_")
    else:
        for ag, st in sorted(state.items()):
            parts = []
            for k in ("pending", "in_progress", "blocked", "completed_24h"):
                if st.get(k):
                    parts.append(f"{k}={st[k]}")
            if parts:
                lines.append(f"- **{ag}**: " + ", ".join(parts))

    lines.append("")
    lines.append(f"## Activity journal (last {hours}h, top 30)")
    if not journal or (len(journal) == 1 and "_error" in journal[0]):
        lines.append("- _no activity journal entries — nothing to report_")
    else:
        for e in journal[:30]:
            ts = (e.get("ts") or "")[:16].replace("T", " ")
            ok = "✓" if e.get("success") else "✗"
            lines.append(f"- {ts} {ok} {e['agent']}/{e['kind']}: {e['description'][:80]}")

    lines.append("")
    lines.append(
        "## Anti-hallucination guardrail\n\n"
        "Only items above actually happened. If a section is empty, the "
        "right answer in your briefing is to **say it is empty** — never "
        "invent completed work. Cite specific filenames or task ids when "
        "claiming a win."
    )
    return "\n".join(lines)


def main() -> int:
    raw = os.environ.get("SKILL_ARGS", "{}")
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"bad JSON: {e}")
        return 1
    hours = max(1, min(int(args.get("hours", 24)), 168))
    agent = args.get("agent") or None
    fmt = (args.get("format") or "markdown").lower()

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "hours": hours,
        "agent_filter": agent,
        "artifacts": recent_artifacts(hours, agent),
        "task_state": task_state(agent),
        "journal": journal_window(hours, agent),
    }

    if fmt == "json":
        print(json.dumps(payload, default=str, indent=2))
    else:
        print(render_markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
