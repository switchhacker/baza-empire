# Sub-project #1 — Task → Data Hub Visibility Pipeline

**Date:** 2026-05-04
**Parent meta-spec:** `2026-05-04-baza-empire-platform-meta-spec.md`
**Status:** Draft (auto-mode authored)

## Problem

Agents complete tasks but their chain of events — what tools they called, what skills they ran, which artifacts they wrote, when they dispatched to other agents, when they got blocked — is invisible. The user has to dig through systemd journals, Telegram threads, and the artifacts directory to reconstruct what happened. Two existing layers help (`task_journal` PostgreSQL = high-level start/end summaries; Redis `event_bus` = ephemeral fan-out for inter-agent), but neither produces a per-task time-ordered chain of fine-grained steps that the Data Hub can render for review.

## Goal

For every agent task, every meaningful action it produces is recorded as a structured event with:
- Stable identity (`task_id`, `project_id`, `agent_id`, monotonic `id`)
- Time (`ts` ISO8601 UTC)
- Kind (enum, see below)
- Payload (JSON: tool name, args, output snippet, artifact path, error msg, etc.)
- Optional `parent_event_id` for chains (e.g., `tool_result` parents to `tool_call`)

The Data Hub renders these chains so the user can audit every task in one screen.

## Non-Goals

- Replacing `task_journal` or `event_bus`. Both stay. `task_events` is the **per-task fine-grained spine**; `task_journal` keeps being the high-level summary; `event_bus` keeps being the ephemeral inter-agent channel.
- Capturing LLM token-stream content. Only structured boundaries (call/result/artifact/dispatch).
- Building the Baza Projects UI itself. That's #4. We only render activity here.

## Architecture

### Storage — `task_events` table in `dashboard/baza_projects.db`

```sql
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
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, ts);
CREATE INDEX IF NOT EXISTS idx_task_events_project ON task_events(project_id, ts);
CREATE INDEX IF NOT EXISTS idx_task_events_agent ON task_events(agent_id, ts);
CREATE INDEX IF NOT EXISTS idx_task_events_kind ON task_events(kind, ts);
```

WAL mode enabled on this DB (already used by AHB pages).

### Event kinds (v1)

| Kind | When emitted | Required payload keys |
|---|---|---|
| `task_started` | task_runner picks up a task and marks in_progress | `title` |
| `task_progress` | LLM returned progress (no completion) | `notes_snippet` |
| `task_completed` | task_runner sees TASK_COMPLETE | `notes_snippet` |
| `task_blocked` | task_runner sees TASK_BLOCKED | `reason` |
| `task_error` | LLM call failed or unhandled exception | `error` |
| `skill_invoked` | skills_engine starts a skill subprocess | `name`, `args` |
| `skill_result` | skill subprocess returned | `name`, `ok`, `output_snippet` |
| `artifact_saved` | _save_artifact or skill artifact_save | `path`, `bytes`, `kind` |
| `dispatch_sent` | DISPATCH line forwarded to another agent | `to_agent`, `instruction_snippet` |
| `dispatch_received` | agent receives a dispatch (best-effort, non-MVP) | `from_agent`, `instruction_snippet` |
| `tool_call` | (post-#3) tool server invoked | `tool`, `args_snippet` |
| `tool_result` | (post-#3) tool server returned | `tool`, `ok`, `result_snippet` |
| `approval_requested` | (post-#5) privileged action gated | `action`, `details` |
| `approval_granted` / `approval_denied` | user response | `action`, `by`, `note` |
| `deploy_started` / `deploy_completed` | (post-#4) deploy gate | `target`, `version` |

Kinds beyond MVP are reserved now to prevent later schema churn.

### Write surface — `core/task_events.py`

A single module:

```python
def emit(task_id, project_id, agent_id, kind, payload=None, parent_event_id=None) -> int
```

- Idempotent failures (a logging error never breaks the caller).
- Returns the inserted `id` so callers can chain (`tool_result` parents to a `tool_call`).
- Always writes to SQLite. Optionally also publishes to Redis `task_events` channel for live SSE consumers (best-effort).
- Truncates payload string fields to 2 KB each.

### Read surface — Dashboard

Three new endpoints in `dashboard/app.py`:

1. `GET /api/datahub/events`
   Filters: `task_id`, `project_id`, `agent_id`, `kind`, `since` (ISO ts), `limit` (default 100, max 500). Returns reverse-chronological list.

2. `GET /api/datahub/chain/<task_id>`
   Returns all events for one task, time-ascending, with parent-child structure preserved (`children` list on each parent). Includes parent task metadata (title, status, project).

3. `GET /api/datahub/events/stream` (Server-Sent Events)
   On connect, replays last 50 events. Subscribes to Redis `task_events` channel and forwards new events as `data:` lines. Heartbeat every 15s.

### UI — Data Hub "Activity Chains" sub-tab

New sub-tab inside `templates/datahub.html` (or new template included from it):

- **Top:** filter row (agent dropdown, project dropdown, kind multi-select, since-when picker).
- **List:** task cards grouped by task_id, showing: agent, title, status badge, total events, last-event ts. Newest first.
- **Click a task card → drawer/modal** showing the chain top-to-bottom: each event as a row with kind icon, timestamp, agent, payload preview, and (where applicable) artifact link or dispatch link.
- **Live indicator:** a pulsing dot shows when SSE is connected. New events fade in at the top of the list.

### Wiring — Where emits go

| File | Add emits |
|---|---|
| `core/task_runner.py` `start_task` call site | `task_started` |
| `core/task_runner.py` after LLM result branches | `task_completed` / `task_blocked` / `task_progress` / `task_error` |
| `core/task_runner.py` `_save_artifact` | `artifact_saved` |
| `core/task_runner.py` `_execute_skill_saves` (if path was created) | `artifact_saved` per save |
| `core/task_runner.py` `process_dispatch_lines` | `dispatch_sent` per dispatch |
| `core/skills_engine.py` skill subprocess wrapper | `skill_invoked` (before) and `skill_result` (after) |

The base class `core/base_agent.py` gets a thin `self.emit_event(kind, payload, ...)` method that defaults `agent_id` to `self.agent_id` so per-agent code can emit without boilerplate. Not used heavily in v1 but available.

### Migration

A schema-init function `init_task_events_tables()` runs on dashboard startup (same pattern as existing `init_ahb_tables`, `init_cloud_tables`). Backfill is **not** attempted — events start being captured at deploy time.

### Performance

- One INSERT per event. With current task volumes (<100 tasks/day, ~10 events/task = 1000 INSERTs/day), SQLite WAL handles this trivially.
- Indexes on `task_id`, `project_id`, `agent_id`, `kind` cover all read-side filters.
- 90-day retention via a nightly cron that deletes rows older than 90d. Cron is added in this sub-project so disk doesn't grow unbounded.

### Error handling

- Emit failures **never** propagate. Wrapped in try/except. Logged via `logger.warning`.
- Read endpoints return `{"events": [], "error": "..."}` with HTTP 200 on read failure (degraded but visible).
- SSE endpoint reconnects gracefully if Redis hiccups.

## Testing

- **Unit:** `core/task_events.py` insert + truncation + idempotent failure.
- **Integration:** spin up a fake task end-to-end (started → skill_invoked → skill_result → artifact_saved → completed); assert chain shape via `/api/datahub/chain/<id>`.
- **UI smoke:** load `/datahub` (chains tab), assert a chain renders for a known fixture task_id.
- **Retention cron:** stub `now()` 91 days ahead; assert old rows pruned.

## Acceptance Criteria

- After deploy, every task picked up by `core/task_runner.py` produces a complete chain visible in Data Hub within 1s of the underlying step.
- Filter by agent / project / kind works.
- SSE pushes new events live; tab can stay open across multiple tasks without refresh.
- 90d retention prunes old rows.
- Existing `task_journal` and `event_bus` paths remain untouched.

## Build Order Inside #1

1. Schema + `core/task_events.py` write helper, with unit tests.
2. Wire emits into `core/task_runner.py` and `core/skills_engine.py`.
3. Dashboard `/api/datahub/events` and `/api/datahub/chain/<task_id>`.
4. Dashboard SSE stream.
5. Data Hub UI sub-tab + live indicator.
6. Retention cron.
7. End-to-end smoke + commit.
