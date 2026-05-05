# Baza Empire Platform — Meta-Spec

**Date:** 2026-05-04
**Status:** Draft (auto-mode authored, pending user review)
**Purpose:** Define the architecture envelope and build order for the agent platform expansion, so the five sub-projects below fit together and each can be designed and implemented independently.

## The Five Sub-Projects

1. **Task → Data Hub visibility pipeline** — make the chain of events for every agent task reviewable.
2. **Directive command system** — slash intents like `/create new ahb project` and `/create new baza project` that route to creation flows from dashboard chat or agent Telegram.
3. **Agent AHB toolbelt** — expose every AHB123 action (clients, projects, invoices, blueprints, voice, etc.) as a tool agents can call, with the same authorization rules a logged-in user has.
4. **Baza Projects developer UI** — full SDLC tab: brainstorm → create → develop → render → preview → explore → test → debug → deploy → iterate. Sub-tabs for apps, web pages, dashboards, ESP/STM firmware, LoRa hardware testing.
5. **Agent project access** — agents can read/write/test/deploy inside Baza Projects subject to the same gates a user sees.

## Architectural Decisions

### D1. Hybrid execution model
- **Sandbox** for everything iterative: brainstorm, scaffold, develop, render, test, preview.
- **Gated promotion** for anything that affects shared state: deploy to runtime, register a systemd unit, push to remote, flash hardware.
- ESP/STM/LoRa flashing **always** requires explicit user approval — no exceptions.
- Promotion gates are first-class events in the visibility pipeline (#1) so the user reviews them in the Data Hub.

### D2. Project shape — `~/baza-empire/projects/<project_id>/`
Every Baza Project is a directory with:
- `.baza-project.yaml` — manifest with `id`, `name`, `type` (`web-app|dashboard|esp-firmware|stm-firmware|lora-test|library|other`), `commands` (`build|test|run|preview|deploy`), `deploy_targets`, `created_by`, `created_at`.
- `.git/` — every project is a git repo from creation. Agents commit their work; you can review via diff.
- `README.md` — brief description.
- `artifacts/` — symlinked or referenced by `dashboard/artifacts/<project_id>/` so the Data Hub sees them.
- `events.jsonl` — append-only event log mirrored into the central `task_events` table.

A row in `dashboard/baza_projects.db.projects` exists for every Baza Project (existing table, extend with `kind` column distinguishing `ahb` vs `baza-dev` vs `legacy-task`).

### D3. Unified `task_events` table
A new table in `dashboard/baza_projects.db`:
```sql
CREATE TABLE task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT,
  task_id TEXT,
  agent_id TEXT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,    -- task_started, tool_call, tool_result, artifact_saved,
                         -- skill_invoked, skill_result, dispatch_sent, dispatch_received,
                         -- approval_requested, approval_granted, approval_denied,
                         -- deploy_started, deploy_completed, error, task_completed,
                         -- task_blocked, task_progress, intent_parsed
  payload TEXT NOT NULL, -- JSON: tool name, args, output snippet, artifact path, error msg, etc.
  parent_event_id INTEGER -- chain-of-events linkage
);
CREATE INDEX idx_task_events_task ON task_events(task_id, ts);
CREATE INDEX idx_task_events_project ON task_events(project_id, ts);
CREATE INDEX idx_task_events_agent ON task_events(agent_id, ts);
```

This is the single spine. #1 renders it, #2 emits to it, #3/#5 emit to it, #4 reads from it.

### D4. Directive intent router
A small parser module `core/intent_router.py` recognizes phrases and emits structured intents:

```
/create new ahb project [from <chat_id>|with <name>]
/create new baza project <name> [type=<type>]
/develop <project_id> <natural language goal>
/render <project_id>
/preview <project_id>
/test <project_id>
/deploy <project_id> [target=<target>]
/iterate <project_id>
/flash <project_id> [device=<device>]   ← always gated
```

Recognition is loose (case-insensitive, leading slash optional) but emits a strict JSON intent envelope that downstream code dispatches on. Same router is used from:
- Dashboard chat boxes
- Agent LLM responses (the LLM emits an intent; the runner executes it)
- Telegram messages received by Specter / Simon

### D5. Permissions model — three levels
- **Read** — any agent can read any project, artifact, event, and AHB record.
- **Write** — agents can write inside a project sandbox they're assigned to. Cross-project write is denied.
- **Privileged** — deploy, flash, systemd changes, git push to remote, AHB destructive operations (delete client/invoice). Privileged actions emit `approval_requested` events and **block** until approved via Data Hub or Telegram. Specter is the only agent that can pre-authorize privileged actions for himself, matching existing project policy.

### D6. Tool server is the action surface
Every action — AHB CRUD, project create, build, test, deploy, flash — is exposed via the existing FastAPI tool server (`tools/server.py`, port 8000). Agents call the same endpoints the dashboard does. Authorization is enforced server-side by a single middleware that checks the permission level (D5) and emits the corresponding `task_events` rows.

## Build Order

1. **#1 — Task → Data Hub visibility pipeline.** Cannot ship anything else without this; everything emits to `task_events` and the Data Hub view of the chain is the user's review surface.
2. **#4 — Baza Projects developer UI envelope.** Project shape (D2), creation flow, listing, basic detail view with sub-tabs (Brainstorm/Develop/Render/Preview/Test/Debug/Deploy/Iterate). Implements the manifest + git-repo scaffolding without yet wiring agents to develop autonomously.
3. **#2 — Directive command system.** Wires `/create new baza project` and `/create new ahb project` end-to-end. At this point the user can say it from chat and a project actually appears.
4. **#3 — Agent AHB toolbelt.** Expand tool server to cover AHB CRUD; agents can now manipulate AHB hub. Each tool call is a `task_event` reviewed in Data Hub.
5. **#5 — Agent project access.** Agents can claim Baza Projects, develop inside the sandbox, request deploy approvals. Closes the loop.

Each sub-project gets its own design doc (`docs/superpowers/specs/YYYY-MM-DD-<name>-design.md`) and implementation plan (`docs/superpowers/plans/YYYY-MM-DD-<name>-plan.md`) before code.

## What This Meta-Spec Does NOT Decide

- Concrete UI layout of the Baza Projects tab (sub-tab names settled, but card vs. tree vs. canvas layout deferred to #4 design).
- Exact deploy target paths for each project type (deferred to #4).
- Hardware test rig wiring for ESP/STM/LoRa (deferred to #4 + a hardware-test sub-design).
- Specter privilege boundary refinements (existing project policy applies).

## Risks

- **Scope creep within #4.** "We can develop anything" must be bounded — first iteration ships web-app and dashboard kinds; ESP/STM/LoRa kinds ship in a later iteration.
- **Tool server becomes the single chokepoint.** Mitigated by D6 + structured events; if it fails, agents can't act, which is the right failure mode.
- **`task_events` write volume.** SQLite handles this for current scale; consider WAL mode and a 90-day retention/rotation policy from day one.
- **Approval fatigue.** Too many gates → user ignores them. D5 keeps the privileged set small and focused on real-world impact.

## Success Criteria

- Every agent task surfaces its full chain of events in the Data Hub within 1s of completion.
- User can issue `/create new baza project foo type=web-app` from a dashboard chat box and see the project, its manifest, and its sandbox dir within 5s.
- Agents can complete an AHB workflow (e.g., "create client + draft invoice + queue for review") with every step visible and the destructive parts blocked behind explicit approval.
- A web-app or dashboard built by an agent inside the Baza Projects tab can be previewed in the browser and deployed to a host runtime path with a single approved promotion.
