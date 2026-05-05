#!/usr/bin/env python3
"""
Duke Harmon's Roadmap Skill — autonomous next-action generator.

Reads the live task DB, recent agent activity, and Baza Projects state to
produce a clean numbered list of next assignments. Optional `mode=create`
also inserts pending tasks so the next task_runner tick picks them up.

SKILL_ARGS:
  count:       int (default 5) — how many next assignments to surface
  mode:        "report" | "create" (default "report")
  focus:       optional string — narrow to "ahb" / "baza-empire" / project_id
  agent:       optional string — bias assignments toward this agent
  notes:       optional string — Duke's own reasoning, included in output

Returns a markdown-style numbered list of executable directives, each with
- a clear title
- the assignee agent_id
- a one-line success criterion
- (when mode=create) the inserted task_id

This is what Duke prints when Serge says "what's next" / "give me my
assignments" / "nothing on my plate".
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")


# ── Active agent roster (for routing assignments) ────────────────────────────

ROUTING = {
    # Tech / infra / code
    "infra": "claw_batto",
    "code": "claw_batto",
    "deploy": "claw_batto",
    "ssl": "claw_batto",
    "backup": "claw_batto",
    "security": "claw_batto",
    # Imaging / design / blueprints
    "design": "sam_axe",
    "image": "sam_axe",
    "blueprint": "sam_axe",
    "render": "sam_axe",
    "logo": "sam_axe",
    "mock": "sam_axe",
    # Client / sales / leads / chat
    "client": "nova_sterling",
    "lead": "nova_sterling",
    "outreach": "nova_sterling",
    "follow-up": "nova_sterling",
    "chat": "nova_sterling",
    # Strategy / business / proposals / financial
    "proposal": "phil_hass",
    "estimate": "phil_hass",
    "invoice": "phil_hass",
    "financial": "phil_hass",
    "tax": "phil_hass",
    "budget": "phil_hass",
    # Permits / research
    "permit": "scout_reeves",
    "research": "scout_reeves",
    "license": "scout_reeves",
    "regulation": "scout_reeves",
    # Roadmap / project mgmt itself goes back to Duke
    "roadmap": "duke_harmon",
    "deadline": "duke_harmon",
    "schedule": "duke_harmon",
    # Strategy / company-wide / CEO-level — Simon
    "strategy": "simon_bately",
    "vision": "simon_bately",
    "branding": "simon_bately",
}

DEFAULT_AGENT = "claw_batto"


def route_for(text: str) -> str:
    t = (text or "").lower()
    for kw, agent in ROUTING.items():
        if kw in t:
            return agent
    return DEFAULT_AGENT


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def open_tasks() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, project_id, title, assigned_to, status, priority, "
            "       created_at, updated_at "
            "FROM tasks "
            "WHERE status IN ('pending','in_progress') "
            "ORDER BY priority DESC, created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def baza_projects() -> list[dict]:
    """Read Baza-dev projects via the same module the dashboard uses."""
    sys.path.insert(0, FRAMEWORK_DIR)
    try:
        from core import baza_projects as bp
        return bp.list_projects(kind="baza-dev")
    except Exception:
        return []


def ahb_open_projects() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, title, status, year, value, client_name "
            "FROM ahb_projects "
            "WHERE status NOT IN ('completed','archived','cancelled') "
            "ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def agent_load() -> dict[str, int]:
    """Count of in-flight tasks per agent (excl. completed/archived)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT assigned_to, COUNT(*) AS n FROM tasks "
            "WHERE status IN ('pending','in_progress') "
            "GROUP BY assigned_to"
        ).fetchall()
    return {r["assigned_to"] or "_unassigned": r["n"] for r in rows}


# ── Roadmap heuristics ──────────────────────────────────────────────────────

# Default roadmap pool: things Duke knows are valuable and Serge has
# implicitly endorsed. This is the seed inventory Duke draws from when
# nobody explicitly assigns work. Edit by adding entries here — Duke
# will route via ROUTING.
ROADMAP_POOL: list[dict] = [
    {
        "title": "ahb123.com — site SEO audit and structured data fixes",
        "owner_hint": "research",
        "criterion": "Audit report saved as artifact + 3 concrete fixes implemented",
        "project_kind": "ahb",
    },
    {
        "title": "ahb123.com — homepage hero copy + brand voice refresh",
        "owner_hint": "branding",
        "criterion": "New hero copy approved by Serge and live on production",
    },
    {
        "title": "ahb123.com — completed-work gallery refresh with current photos",
        "owner_hint": "image",
        "criterion": "5+ new project photos categorized and uploaded to gallery",
    },
    {
        "title": "AHB lead pipeline — review last 30 days, identify warm leads, draft outreach",
        "owner_hint": "lead",
        "criterion": "List of warm leads with personalized first-touch drafts saved as artifact",
    },
    {
        "title": "AHB Q2 financial snapshot — revenue, AR aging, top variance",
        "owner_hint": "financial",
        "criterion": "One-page snapshot artifact with three actionable numbers",
    },
    {
        "title": "Renew PA Home Improvement Contractor license — gather requirements + checklist",
        "owner_hint": "license",
        "criterion": "Checklist artifact with deadline, cost, and supporting docs list",
    },
    {
        "title": "Baza dashboard — performance audit (top 5 slow endpoints, fix one)",
        "owner_hint": "code",
        "criterion": "Artifact with timings + one PR-ready commit on the slowest endpoint",
    },
    {
        "title": "Empire backups — verify nightly Postgres + SQLite backups completed in the last 24h, restore-test one",
        "owner_hint": "backup",
        "criterion": "Backup verification artifact with restore test result",
    },
    {
        "title": "AHB123.com — add Google Analytics 4 (or alternative) and confirm event tracking",
        "owner_hint": "code",
        "criterion": "Analytics dashboard URL + 3 captured events documented",
    },
    {
        "title": "Baza Empire — agent skill catalog audit; remove dead skills, document the keepers",
        "owner_hint": "code",
        "criterion": "Skill catalog artifact + at least 3 stale skills removed or marked deprecated",
    },
]


def assignments_from_pool(count: int, focus: str | None,
                          existing_titles: set[str]) -> list[dict]:
    pool = list(ROADMAP_POOL)
    if focus:
        pool = [x for x in pool if focus.lower() in (x["title"].lower() + " "
                                                     + x.get("project_kind", ""))]
    out: list[dict] = []
    seen: set[str] = set()
    for item in pool:
        if len(out) >= count:
            break
        if item["title"] in existing_titles:
            continue
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        agent_id = route_for(item.get("owner_hint", "") or item["title"])
        out.append({
            "title": item["title"],
            "agent": agent_id,
            "criterion": item["criterion"],
        })
    return out


# ── Output rendering + optional task creation ────────────────────────────────

def render_report(assignments: list[dict], notes: str = "",
                  load: dict[str, int] | None = None) -> str:
    if not assignments:
        return ("Nothing on the queue and the roadmap pool is empty. Add items to "
                "ROADMAP_POOL in skills/shared/duke_roadmap.py or pass focus= to "
                "narrow scope.")
    out = ["Here's what's next on the roadmap:\n"]
    for i, a in enumerate(assignments, 1):
        out.append(f"{i}. **{a['title']}**")
        out.append(f"   • owner: {a['agent']}")
        out.append(f"   • done when: {a['criterion']}")
        if a.get("task_id"):
            out.append(f"   • task: `{a['task_id']}` — queued, will auto-run on next tick")
        out.append("")

    out.append("DISPATCH lines (the runner forwards these to each agent):")
    for a in assignments:
        out.append(f"DISPATCH:{a['agent']}:{a['title']}. Done when: {a['criterion']}")

    if load:
        out.append("\nCurrent agent load:")
        for k, v in sorted(load.items(), key=lambda kv: -kv[1]):
            out.append(f"  {k:18s}  {v} open task(s)")

    if notes:
        out.append("\nDuke's note: " + notes)

    return "\n".join(out)


def create_tasks(assignments: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with _conn() as c:
        for a in assignments:
            tid = str(uuid.uuid4())[:8]
            description = (
                f"Auto-queued by Duke's roadmap skill on {now}.\n\n"
                f"Goal: {a['title']}\n"
                f"Done when: {a['criterion']}\n\n"
                "Operate autonomously — fill in blanks with the most probable "
                "interpretation and continue. End with TASK_COMPLETE when the "
                "criterion is met. Save deliverables via ##SKILL:artifact_save##."
            )
            c.execute(
                """INSERT INTO tasks
                     (id, project_id, title, description, assigned_to, status,
                      priority, notes, created_at, updated_at)
                   VALUES (?, '', ?, ?, ?, 'pending', 'high', '', ?, ?)""",
                (tid, a["title"], description, a["agent"], now, now),
            )
            a["task_id"] = tid
        c.commit()
    return assignments


def main() -> int:
    raw = os.environ.get("SKILL_ARGS", "{}")
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"bad JSON: {e}")
        return 1

    count = max(1, min(int(args.get("count", 5)), 12))
    mode = (args.get("mode") or "report").lower()
    focus = args.get("focus")
    notes = args.get("notes") or ""

    open_t = open_tasks()
    existing_titles = {t["title"] for t in open_t}
    load = agent_load()

    assignments = assignments_from_pool(count, focus, existing_titles)
    if not assignments:
        # Fall back: surface in-flight work that needs a nudge
        assignments = []
        for t in open_t[:count]:
            assignments.append({
                "title": "[in-flight] " + (t["title"] or "(untitled)"),
                "agent": t["assigned_to"] or DEFAULT_AGENT,
                "criterion": "Already in progress — push to TASK_COMPLETE this iteration.",
            })

    if mode == "create" and assignments:
        # Only insert items that don't already exist as open tasks
        fresh = [a for a in assignments if a["title"] not in existing_titles]
        if fresh:
            create_tasks(fresh)
        for a in assignments:
            if a["title"] in existing_titles and "task_id" not in a:
                # Find existing id so the report links to it
                with _conn() as c:
                    row = c.execute("SELECT id FROM tasks WHERE title=? AND status IN ('pending','in_progress') ORDER BY created_at DESC LIMIT 1", (a["title"],)).fetchone()
                if row:
                    a["task_id"] = row["id"]

    print(render_report(assignments, notes=notes, load=load))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
