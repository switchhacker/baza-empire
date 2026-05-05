"""
Baza Empire — Task Events (visibility pipeline #1)

Per-task fine-grained chain-of-events log written to dashboard/baza_projects.db.

Augments (does not replace):
- task_journal (PostgreSQL): high-level start/end summaries
- event_bus (Redis pub/sub): ephemeral inter-agent fan-out

This module owns:
- task_events SQLite table (init_schema)
- emit() write helper used by task_runner, skills_engine, base_agent, tool server
- list_events / chain_for_task read helpers consumed by dashboard endpoints

emit() failures NEVER propagate to the caller. A logging miss must not break
agent work. Payload string fields are truncated to 2 KB to keep DB size bounded.
Events are also published to Redis channel `baza:task_events` for live SSE.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any, Iterable

logger = logging.getLogger("baza.task_events")

# Resolve DB path the same way the dashboard does so this module is import-safe
# from anywhere in the repo (agents, skills, tool server, dashboard, tests).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_FRAMEWORK_DIR = os.path.dirname(_THIS_DIR)
DB_PATH = os.environ.get(
    "BAZA_TASK_EVENTS_DB",
    os.path.join(_FRAMEWORK_DIR, "dashboard", "baza_projects.db"),
)

REDIS_URL = os.environ.get("BAZA_REDIS_URL", "redis://localhost:6379/1")
REDIS_CHANNEL = "baza:task_events"

PAYLOAD_FIELD_MAX = 2048  # truncate any single string value in payload to 2KB


# ── Known kinds (kept in sync with design doc) ────────────────────────────────
KINDS = frozenset({
    "task_started", "task_progress", "task_completed", "task_blocked", "task_error",
    "skill_invoked", "skill_result",
    "artifact_saved",
    "dispatch_sent", "dispatch_received",
    "tool_call", "tool_result",
    "approval_requested", "approval_granted", "approval_denied",
    "deploy_started", "deploy_completed",
    "intent_parsed",
})


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema() -> None:
    """Create task_events table + indexes if they don't exist. Idempotent."""
    try:
        with _connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts TEXT NOT NULL DEFAULT (datetime('now')),
                  task_id TEXT,
                  project_id TEXT,
                  agent_id TEXT,
                  kind TEXT NOT NULL,
                  payload TEXT NOT NULL DEFAULT '{}',
                  parent_event_id INTEGER,
                  FOREIGN KEY(parent_event_id) REFERENCES task_events(id)
                );
                CREATE INDEX IF NOT EXISTS idx_task_events_task     ON task_events(task_id, ts);
                CREATE INDEX IF NOT EXISTS idx_task_events_project  ON task_events(project_id, ts);
                CREATE INDEX IF NOT EXISTS idx_task_events_agent    ON task_events(agent_id, ts);
                CREATE INDEX IF NOT EXISTS idx_task_events_kind     ON task_events(kind, ts);
                CREATE INDEX IF NOT EXISTS idx_task_events_ts       ON task_events(ts);
                """
            )
    except Exception as e:
        logger.warning(f"task_events init_schema failed: {e}")


def _truncate_payload(payload: dict | None) -> dict:
    if not payload:
        return {}
    out = {}
    for k, v in payload.items():
        if isinstance(v, str) and len(v) > PAYLOAD_FIELD_MAX:
            out[k] = v[:PAYLOAD_FIELD_MAX] + "…[truncated]"
        else:
            out[k] = v
    return out


def emit(
    kind: str,
    *,
    task_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    payload: dict | None = None,
    parent_event_id: int | None = None,
) -> int | None:
    """
    Insert a task_event row and best-effort publish to Redis.

    Returns the new row id, or None if write failed (failure never propagates).
    """
    if kind not in KINDS:
        # Don't reject — just log. Schema is a hint, not a hard constraint.
        logger.debug(f"task_events.emit unknown kind={kind!r}")

    safe_payload = _truncate_payload(payload)
    payload_json = json.dumps(safe_payload, default=str, ensure_ascii=False)

    new_id: int | None = None
    try:
        with _connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO task_events
                  (task_id, project_id, agent_id, kind, payload, parent_event_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, project_id, agent_id, kind, payload_json, parent_event_id),
            )
            new_id = cur.lastrowid
    except Exception as e:
        logger.warning(f"task_events.emit insert failed kind={kind}: {e}")
        return None

    # Best-effort live publish for SSE consumers; never block on this.
    try:
        import redis  # local import keeps module importable without redis

        r = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=0.5)
        r.publish(
            REDIS_CHANNEL,
            json.dumps(
                {
                    "id": new_id,
                    "kind": kind,
                    "task_id": task_id,
                    "project_id": project_id,
                    "agent_id": agent_id,
                    "payload": safe_payload,
                    "parent_event_id": parent_event_id,
                },
                default=str,
                ensure_ascii=False,
            ),
        )
        try:
            r.close()
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"task_events.emit redis publish skipped: {e}")

    return new_id


# ── Read helpers used by dashboard endpoints ──────────────────────────────────

def list_events(
    task_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    kinds: Iterable[str] | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Reverse-chronological filtered list. limit clamped to 500."""
    limit = max(1, min(int(limit or 100), 500))
    where: list[str] = []
    args: list[Any] = []
    if task_id:
        where.append("task_id = ?")
        args.append(task_id)
    if project_id:
        where.append("project_id = ?")
        args.append(project_id)
    if agent_id:
        where.append("agent_id = ?")
        args.append(agent_id)
    if kinds:
        kinds_list = list(kinds)
        if kinds_list:
            placeholders = ",".join("?" * len(kinds_list))
            where.append(f"kind IN ({placeholders})")
            args.extend(kinds_list)
    if since:
        where.append("ts >= ?")
        args.append(since)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT id, ts, task_id, project_id, agent_id, kind, payload, parent_event_id
        FROM task_events
        {clause}
        ORDER BY id DESC
        LIMIT ?
    """
    args.append(limit)
    try:
        with _connect() as conn:
            rows = conn.execute(sql, args).fetchall()
            return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"task_events.list_events failed: {e}")
        return []


def chain_for_task(task_id: str) -> list[dict[str, Any]]:
    """Time-ascending events for one task. Children get nested under parents."""
    if not task_id:
        return []
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, ts, task_id, project_id, agent_id, kind, payload, parent_event_id
                FROM task_events
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (task_id,),
            ).fetchall()
    except Exception as e:
        logger.warning(f"task_events.chain_for_task failed: {e}")
        return []

    events = [_row_to_dict(r) for r in rows]
    by_id = {e["id"]: e for e in events}
    for e in events:
        e["children"] = []
    roots: list[dict[str, Any]] = []
    for e in events:
        parent = by_id.get(e.get("parent_event_id")) if e.get("parent_event_id") else None
        if parent:
            parent["children"].append(e)
        else:
            roots.append(e)
    return roots


def recent_task_summaries(limit: int = 50) -> list[dict[str, Any]]:
    """For the chains list view: one row per task_id with stats and last event."""
    limit = max(1, min(int(limit or 50), 200))
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  task_id,
                  COALESCE(MAX(project_id), '') AS project_id,
                  COALESCE(MAX(agent_id), '')   AS agent_id,
                  COUNT(*)        AS event_count,
                  MIN(ts)         AS first_ts,
                  MAX(ts)         AS last_ts,
                  MAX(CASE WHEN kind='task_completed' THEN 1 ELSE 0 END) AS has_completed,
                  MAX(CASE WHEN kind='task_blocked'   THEN 1 ELSE 0 END) AS has_blocked,
                  MAX(CASE WHEN kind='task_error'     THEN 1 ELSE 0 END) AS has_error
                FROM task_events
                WHERE task_id IS NOT NULL AND task_id != ''
                GROUP BY task_id
                ORDER BY last_ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"task_events.recent_task_summaries failed: {e}")
        return []


def prune_older_than(days: int = 90) -> int:
    """Delete events older than `days`. Returns count deleted."""
    try:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM task_events WHERE ts < datetime('now', ? )",
                (f'-{int(days)} days',),
            )
            return cur.rowcount or 0
    except Exception as e:
        logger.warning(f"task_events.prune_older_than failed: {e}")
        return 0


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    raw = d.get("payload") or "{}"
    try:
        d["payload"] = json.loads(raw)
    except Exception:
        d["payload"] = {"_raw": raw}
    return d
