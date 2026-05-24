# Baza Projects — Live Build Tree

**Date:** 2026-05-24
**Status:** Approved (brainstorming sign-off 2026-05-24)
**Scope:** Baza dev projects only (does NOT extend AHB123 contracting projects)
**Phasing:** Phase 1 + Phase 2 ship together; Phase 3 follows
**Related:** `2026-05-05-baza-projects-envelope-design.md` (envelope), `2026-05-05-agent-project-access-design.md` (agent access), `2026-05-05-intent-router-design.md` (intent dispatch)

## Goal

Transform Baza Projects from a static workspace into a **live, agent-driven build tree** that handles software, hardware, and hybrid hardware+software projects end-to-end. The user types a name + description; an orchestrator agent unfolds a visual scaffold of typed nodes (research → decisions → hardware → firmware → software → integration → test → deploy → result). The tree grows in real time, agents make decisions autonomously, parts are auto-researched into a BOM, code is auto-drafted, and a continuous worker drives unblocked nodes forward until the project is deployed.

**Reference scenario** (Rubbish Taxi): User creates a project with the description *"use an old hoverboard and reprogram/flash and add hardware components needed to develop an automated obstacle-avoidance trashcan transport system."* The system auto-decomposes into research on hoverboard motor controllers, decisions between sensor options (LiDAR vs ultrasonic vs ToF), a BOM of cheap parts, firmware skeletons for the chosen MCU, obstacle-avoidance software modules, integration steps, test plans, and a deploy node. Hardware-blocked branches park while firmware proceeds in parallel. A progress bar fills yellow→green as nodes complete; a ⭐ replaces the percent on full deploy.

## What ships

### Phase 1 + 2 (bundled, this spec)

1. New SQLite schema (5 tables) on `dashboard/baza_projects.db`
2. New `🌳 Scaffold` sub-tab on `project_detail.html` with D3 tidy-tree, side panel, BOM table, progress bar, modals
3. Full REST + SSE API surface for the scaffold, BOM, inventory, equipment
4. Scaffolder agent flow — orchestrated by Claw, drawing on Rex (hardware research), Phil (software design), and existing `web_search` skill
5. `core/scaffold_runner.py` — continuous worker (systemd timer) that drives unblocked nodes forward
6. Manual node CRUD (add/edit/delete/re-run/override decision) and BOM checkbox flow
7. Global Baza Inventory + Baza Equipment modals (cross-project, persist between projects)
8. "Promote BOM to Inventory" flow when a part arrives

### Phase 3 (follow-up, scoped here but built later)

- `<model-viewer>` for `.glb`/`.gltf` hardware previews on hardware nodes
- Cross-project supplies roll-up ("everything I need to buy across all projects")
- Decision override UI with side-by-side alternatives view
- Finish-line ⭐ animation + "test it" CTA on result nodes

## Non-goals

- **No CAD authoring.** We display uploaded `.glb`/`.stl`/`.png`/`.pdf`; we do not edit them.
- **No vendor API integration.** `web_search` returns purchase links; the user buys through them.
- **No physical robotics control loop.** Firmware is generated; flashing is the existing `/flash` intent path.
- **No multi-user permissions.** Baza is single-user.
- **No decision-gating.** Per user decision, agents auto-decide all forks; the user can override via the UI but is not prompted.
- **No extension to AHB123 contracting projects.** Per user decision, this is dev-projects only.
- **No external 3rd-party dependencies beyond what's already in `requirements.txt` plus `model-viewer` (Phase 3, a static-served Web Component).**

## Architecture

### Layered view

```
┌──────────────────────────────────────────────────────────┐
│ project_detail.html → 🌳 Scaffold sub-tab                │
│   • D3 tidy-tree (SVG, pan + zoom)                       │
│   • Right slide-in panel (node detail / artifacts)       │
│   • BOM table (checkboxes, status colors)                │
│   • Progress bar (yellow→green) + ⭐                      │
│   • Modals: Inventory / Equipment / Supplies-needed      │
│   • SSE listener for live updates                        │
└──────────────┬───────────────────────────────────────────┘
               │ REST + SSE
┌──────────────▼───────────────────────────────────────────┐
│ dashboard/app.py routes (or new dashboard/scaffold.py BP)│
│   • /api/baza/projects/<id>/scaffold/*                   │
│   • /api/baza/projects/<id>/bom/*                        │
│   • /api/baza/inventory/*  /api/baza/equipment/*         │
│   • SSE: /api/baza/projects/<id>/scaffold/stream         │
└──────────────┬───────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────┐
│ core/scaffold_engine.py                                  │
│   • Graph CRUD (nodes, edges, events)                    │
│   • Dependency satisfaction check                        │
│   • Progress math (weighted)                             │
│   • Event emission (for SSE fan-out)                     │
└──────────────┬───────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────┐
│ core/scaffold_runner.py  (systemd timer)                 │
│   • Poll for pending+unblocked nodes                     │
│   • Dispatch to assigned agent via intent_dispatcher     │
│   • Update node on completion/failure                    │
│   • Emit events                                          │
└──────────────┬───────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────┐
│ Agents: Claw (orchestrator), Rex (HW research),          │
│ Phil (SW design), Sam (renders), via existing            │
│ skills system + web_search skill                         │
└──────────────────────────────────────────────────────────┘
```

### Data model

All tables live in `dashboard/baza_projects.db` (existing SQLite file). Migrations are idempotent `CREATE TABLE IF NOT EXISTS` + try/except `ALTER TABLE`.

**`project_scaffold_nodes`**
```
id              INTEGER PK
project_id      TEXT NOT NULL              -- FK to baza projects.id
parent_id       INTEGER NULL               -- FK self
node_type       TEXT NOT NULL              -- enum (see below)
title           TEXT NOT NULL
description     TEXT
status          TEXT NOT NULL DEFAULT 'pending'
                -- pending | in_progress | done | blocked
                -- | awaiting_part | failed | overridden
agent_assigned  TEXT                       -- agent_id or NULL
payload_json    TEXT                       -- node-type-specific data
weight          INTEGER NOT NULL DEFAULT 1 -- for progress math
depth           INTEGER NOT NULL DEFAULT 0
x               REAL                       -- last laid-out tree x
y               REAL                       -- last laid-out tree y
auto_decided    INTEGER NOT NULL DEFAULT 0 -- bool, true if agent picked
chosen_option   TEXT                       -- decision result (decision nodes only)
started_at      TEXT
completed_at    TEXT
created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
updated_at      TEXT
```

**Node types (enum):**
- `root` — single per project, holds the description
- `research` — web search + summarize
- `decision` — agent picks between alternatives; `payload_json.options[]`
- `hardware_component` — a part, links to BOM row
- `firmware` — code for an MCU
- `software_module` — non-firmware code
- `integration` — wiring multiple branches together
- `test` — checklist or automated test
- `deploy` — final deployment step
- `result` — terminal node ("the fruit")
- `manual_step` — user must do something IRL (solder, mount, etc.)

**`project_scaffold_edges`**
```
id          INTEGER PK
project_id  TEXT NOT NULL
from_node   INTEGER NOT NULL
to_node     INTEGER NOT NULL
edge_type   TEXT NOT NULL                  -- depends_on | produces | decided_for
created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
```

Most tree relationships use `parent_id` on the node (a true tree). `edges` is for cross-tree dependencies like "firmware module B depends on hardware component C in a different branch."

**`project_scaffold_events`**
```
id          INTEGER PK
project_id  TEXT NOT NULL
node_id     INTEGER                        -- NULL for project-level events
event_type  TEXT NOT NULL
                -- created | started | progress | completed
                -- | failed | decided | overridden | note
                -- | bom_added | bom_in_hand | promoted_to_inventory
actor       TEXT                           -- agent_id or 'user' or 'system'
payload     TEXT                           -- JSON
created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
```

Append-only. Drives the SSE stream and the per-node activity log.

**`project_bom`**
```
id            INTEGER PK
project_id    TEXT NOT NULL
node_id       INTEGER                      -- linked scaffold node (optional)
name          TEXT NOT NULL
part_number   TEXT
vendor        TEXT
url           TEXT
qty           INTEGER NOT NULL DEFAULT 1
unit_price    REAL                         -- in USD
status        TEXT NOT NULL DEFAULT 'researched'
              -- researched | ordered | received | installed
              -- | substituted | cancelled
in_hand       INTEGER NOT NULL DEFAULT 0   -- bool (the persistent checkbox)
in_hand_at    TEXT
notes         TEXT
inventory_id  INTEGER                      -- if promoted to global inventory
created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
updated_at    TEXT
```

**`baza_inventory`** (global, cross-project)
```
id          INTEGER PK
category    TEXT                           -- "MCU" | "sensor" | "wire" | "fastener" ...
name        TEXT NOT NULL
part_number TEXT
quantity    INTEGER NOT NULL DEFAULT 1
location    TEXT                           -- "garage bin 3", "desk drawer", ...
condition   TEXT DEFAULT 'good'            -- good | used | broken
unit_price  REAL
vendor      TEXT
url         TEXT
notes       TEXT
created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
updated_at  TEXT
```

**`baza_equipment`** (global, cross-project)
```
id         INTEGER PK
name       TEXT NOT NULL                   -- "Bambu X1C", "Hakko FX-888D", ...
type       TEXT                            -- "3d_printer" | "soldering" | "multimeter" | "oscilloscope" | ...
location   TEXT
status     TEXT NOT NULL DEFAULT 'available'  -- available | in_use | broken | loaned
in_use_by  TEXT                            -- project_id if in_use
notes      TEXT
created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
updated_at TEXT
```

### Dependency satisfaction

A node is **runnable** iff:
- `status = 'pending'`
- All `edges WHERE to_node = me AND edge_type = 'depends_on'` reference `from_node` rows where `status = 'done'`
- Its `parent_id` (if any) is in `{in_progress, done}`
- If `node_type = 'firmware'` or `'integration'`, all linked `hardware_component` siblings have BOM rows where `in_hand = 1` OR the node has `payload_json.allow_parallel_drafting = true` (default true for firmware skeletons)

Hardware nodes whose BOM rows have `in_hand = 0` and `status != 'received'` are auto-marked `awaiting_part`.

### Progress math

Per project: `100 * sum(weight where status='done') / sum(weight)`.

Default weights:
- `research`: 1
- `decision`: 1
- `hardware_component`: 3
- `firmware`: 5
- `software_module`: 4
- `integration`: 4
- `test`: 2
- `deploy`: 2
- `manual_step`: 2
- `result`: 0 (only flips the ⭐, doesn't add to denominator)

The ⭐ shows when the project's `result` node is `done` AND overall progress = 100%.

### Agent flow

The orchestrator is **Claw**. On `scaffold/start`:

1. Insert `root` node with the full description as `payload_json.description`.
2. Emit `scaffold_start` event (SSE).
3. Dispatch via existing `intent_dispatcher` with new intent `scaffold_decompose` → routes to Claw with a task in `tasks` table.
4. Claw's task prompt instructs him to:
   - Call `web_search` to understand the description.
   - Emit `##SKILL:scaffold_emit_nodes{...}##` patterns (new skill) that add children to the root with appropriate types, weights, and dependencies.
   - Decide which children need further decomposition (recursive — Claw can re-dispatch himself or delegate to Rex/Phil).
5. Rex handles `research` and `hardware_component` decomposition (parts research, BOM rows). Phil handles `firmware` and `software_module` skeletons.
6. Decisions are made inline by the assigned agent: it picks the best option, writes `chosen_option`, sets `auto_decided = 1`, status = `done`, emits `decided` event with alternatives in payload so the UI can show "why this".
7. Once Claw finishes top-level decomposition, the **scaffold runner** takes over and drives child nodes forward continuously.

### Scaffold runner

`core/scaffold_runner.py`, pattern mirrors `core/task_runner.py`. Polls every 30s:

```python
SELECT n.* FROM project_scaffold_nodes n
WHERE n.status = 'pending'
  AND <dep_satisfaction_query>
ORDER BY n.depth, n.id
LIMIT 20
```

For each runnable node:
1. If `agent_assigned` is NULL, assign by `node_type`:
   - `research` → Rex (or Claw if Rex unavailable)
   - `hardware_component` → Rex
   - `firmware` → Phil
   - `software_module` → Phil
   - `integration` → Claw
   - `test` → Phil
   - `deploy` → Claw
   - `manual_step` → no agent; sits as `pending` until user marks done
   - `decision` → owner of the parent node's domain
2. Mark `status='in_progress'`, `started_at=now`, emit `started` event.
3. Insert task into `tasks` table with `project_id`, a description that includes node id + payload, and an explicit completion contract (the agent must call a `##SKILL:scaffold_complete_node{node_id, result}##` skill at the end).
4. Move on. The agent does the work via existing task_runner cycle.
5. On `scaffold_complete_node` skill invocation, the engine updates the node (status=done, payload merged, completed_at set), emits `completed`, and the next poll picks up newly-runnable children.

The runner runs under a new systemd timer `baza-scaffold-runner.timer` (every 30s) — `baza-scaffold-runner.service` is a one-shot Python script.

A per-project pause toggle (`scaffold_paused` column on baza projects) skips the project entirely; the UI exposes a Pause/Resume button.

### New skills

Added under `skills/shared/`:

- `scaffold_emit_nodes.py` — args: `{project_id, parent_id, nodes: [{type, title, description, weight?, agent?, depends_on?, payload?}]}`. Inserts the nodes + emits `created` events. Returns the created node IDs.
- `scaffold_complete_node.py` — args: `{node_id, result?, artifacts?, decision?}`. Updates the node, optionally writes artifacts into the project dir, emits `completed`.
- `scaffold_add_bom.py` — args: `{project_id, node_id?, name, part_number?, vendor?, url?, qty?, unit_price?, notes?}`. Inserts BOM rows + emits `bom_added`.
- `scaffold_block_awaiting_part.py` — args: `{node_id, bom_id}`. Marks a node `awaiting_part` and links the BOM row.

### API surface

All routes namespaced under `/api/baza/...`. Implemented as a new `dashboard/scaffold.py` blueprint registered in `dashboard/app.py`.

**Scaffold graph**
```
POST   /api/baza/projects/<id>/scaffold/start
       body: { description?: str, regenerate?: bool }
       → 202, { task_id, root_node_id }

GET    /api/baza/projects/<id>/scaffold
       → { nodes: [...], edges: [...], progress_pct, has_star }

GET    /api/baza/projects/<id>/scaffold/stream          (SSE)
       events: node_created, node_started, node_progress,
               node_completed, node_failed, node_decided,
               bom_added, bom_in_hand, project_paused, project_resumed

POST   /api/baza/projects/<id>/scaffold/node            (manual add)
       body: { parent_id?, node_type, title, description?, weight?, agent? }

PATCH  /api/baza/projects/<id>/scaffold/node/<n>
       body: { title?, description?, status?, weight?, payload? }

DELETE /api/baza/projects/<id>/scaffold/node/<n>
       (cascades to descendants)

POST   /api/baza/projects/<id>/scaffold/node/<n>/run       (re-run)
POST   /api/baza/projects/<id>/scaffold/node/<n>/override
       body: { chosen_option: str, reason?: str }
POST   /api/baza/projects/<id>/scaffold/node/<n>/note
       body: { note: str }

POST   /api/baza/projects/<id>/scaffold/pause
POST   /api/baza/projects/<id>/scaffold/resume
```

**BOM**
```
GET    /api/baza/projects/<id>/bom
POST   /api/baza/projects/<id>/bom
PATCH  /api/baza/projects/<id>/bom/<b>
DELETE /api/baza/projects/<id>/bom/<b>
POST   /api/baza/projects/<id>/bom/<b>/toggle-hand
       → flips in_hand, sets in_hand_at, emits bom_in_hand
POST   /api/baza/projects/<id>/bom/<b>/promote-inventory
       → creates baza_inventory row, links via inventory_id
```

**Inventory + Equipment (global)**
```
GET    /api/baza/inventory
POST   /api/baza/inventory
PATCH  /api/baza/inventory/<i>
DELETE /api/baza/inventory/<i>

GET    /api/baza/equipment
POST   /api/baza/equipment
PATCH  /api/baza/equipment/<e>
DELETE /api/baza/equipment/<e>
```

**Cross-project supplies roll-up (Phase 3 endpoint, stubbed in Phase 1+2)**
```
GET    /api/baza/supplies/needed
       → aggregate of all BOM rows where in_hand=0 AND status != 'cancelled'
         grouped by name+part_number
```

### UI — Scaffold sub-tab in `project_detail.html`

Add a new sub-tab labeled **🌳 Scaffold** between Overview and Brainstorm. For projects with at least one scaffold node, this becomes the default landing tab.

**Layout (top to bottom):**
```
┌─────────────────────────────────────────────────────────┐
│ [▶ Pause/Resume]  Progress: ▰▰▰▰▰▰░░░░ 62%   [🧰 Inv] │
│                                              [🔧 Equip] │
│                                              [🛒 Supp.] │
├─────────────────────────────────────────────────────────┤
│                                                         │
│            ╔═══════════════╗                           │
│            ║   tree canvas  ║   ← D3 tidy-tree, SVG    │
│            ║   pan + zoom   ║      pan + zoom + node   │
│            ║                ║      click → side panel  │
│            ╚═══════════════╝                           │
│                                                         │
├─────────────────────────────────────────────────────────┤
│ BOM                                              [+ Add]│
│ ☐ Hoverboard motor controller   $24.99  vendor [link]  │
│ ☑ ESP32 dev board               $8.50   [vendor] [→inv]│
│ ☐ HC-SR04 ultrasonic ×4         $5.00   [vendor]       │
└─────────────────────────────────────────────────────────┘
```

**Tree canvas:**
- D3 v7 (already used elsewhere — verify; if not, vendored `d3.min.js` in `dashboard/static/vendor/d3.v7.min.js`)
- `d3.tree()` tidy-tree layout, vertical orientation
- Nodes are rounded-rectangle SVG with type icon, title, status-tinted background
- Status colors: `pending`=#9ca3af, `in_progress`=#3b82f6 (pulsing), `done`=#22c55e, `blocked`=#ef4444, `awaiting_part`=#f59e0b, `failed`=#dc2626, `overridden`=#a855f7
- Type icons: 🔬 research, ❓ decision, 🔩 hardware_component, ⚡ firmware, 💻 software_module, 🔗 integration, ✅ test, 🚀 deploy, 🍎 result, ✋ manual_step
- Pan + zoom via `d3.zoom()`, scale extent [0.2, 3]
- Click → opens right slide-in panel; double-click → centers + zooms
- Subtle edge animation on `in_progress` → child links pulse

**Right side panel (slide-in from right when a node is clicked):**
- Header: type icon, title, status pill, assigned agent badge
- Tabs inside the panel: Details | Activity | Artifacts
  - **Details**: description (editable), payload pretty-printed, decision alternatives (for decision nodes) with current pick highlighted + "override" affordance
  - **Activity**: event log filtered to `node_id = this`
  - **Artifacts**: file list under `~/baza-empire/projects/<id>/artifacts/scaffold/<node_id>/`; previewers — code (textarea/highlight), image (img tag), PDF (iframe), `.glb`/`.gltf` (Phase 3 `<model-viewer>`)
- Footer actions: Re-run, Override decision, Add note, Mark blocked, Mark done (manual_step), Delete

**BOM section:**
- HTML table with: ☑ in_hand, Name, Qty, Unit price, Vendor (link), Status pill, Linked node (link → tree), Actions (`→ Inventory`, edit, delete)
- Sort by status, then in_hand desc
- "+ Add" button → inline form
- Toggling ☑ POSTs to `/bom/<b>/toggle-hand`; on receive, if a node was `awaiting_part` because of this BOM row, it auto-flips to `pending` (server-side) and the SSE event re-paints it

**Progress bar:**
- Width 100%, height 16px, rounded
- Background: light gray
- Fill: linear-gradient(90deg, `#FFD700`, `#22c55e`), width = progress_pct
- Right-edge label: "62%" or ⭐ when complete
- Below: a thin secondary bar showing in_progress count (subtle pulse)

**Modals:**
- 🧰 Baza Inventory — searchable table (category filter), CRUD, "send to project BOM" action
- 🔧 Baza Equipment — same shape, with status filter (available / in_use / broken)
- 🛒 Supplies needed (Phase 3) — cross-project roll-up

**Live updates via SSE:**
- On mount, open `EventSource("/api/baza/projects/<id>/scaffold/stream")`
- Each event patches the local graph and re-renders the affected subtree (avoid full redraw)
- Reconnect with exponential backoff on disconnect
- Tear down on tab unmount

**Override flow:**
- Click a `done` decision node → side panel shows current pick + alternatives → click "Override" → modal with the same alternatives + free-text option
- On submit, POSTs `/scaffold/node/<n>/override`, server marks node `overridden`, emits event; downstream nodes that depended on the original pick are marked `pending` and re-queued

### Worker — `core/scaffold_runner.py`

Standalone Python script, run by systemd timer. Pseudocode:

```python
def tick():
    for pid in get_active_unpaused_projects():
        for node in get_runnable_nodes(pid, limit=20):
            assign_agent_if_unset(node)
            if node.node_type == "manual_step":
                continue  # waits for user
            mark_started(node)
            emit_event(pid, node.id, "started", actor=node.agent_assigned)
            create_task_for_agent(node)

def get_runnable_nodes(pid, limit):
    """Pending nodes whose dependencies are all satisfied."""
    ...
```

Systemd:
- `/etc/systemd/system/baza-scaffold-runner.service` — one-shot Python
- `/etc/systemd/system/baza-scaffold-runner.timer` — OnUnitActiveSec=30s
- Logged to `/var/log/baza/scaffold-runner.log` (existing log conventions)

### Task contract for agents

When the runner creates a task for a node, the description includes:

```
You are working on Baza scaffold node {node_id} in project {project_id}.

Node type: {node_type}
Title: {title}
Description: {description}
Payload: {payload_json}
Parent context: {parent_title} ({parent_type})

When finished, you MUST end your response with:
##SKILL:scaffold_complete_node{"node_id": {node_id}, "result": "...", "artifacts": [...]}##

For research nodes, summarize 3-5 sources and pick one.
For decision nodes, list alternatives, pick the best, and explain why in `payload.reason`.
For hardware_component nodes, call ##SKILL:scaffold_add_bom{...}## with the chosen part.
For firmware/software_module nodes, write the code to artifacts/scaffold/{node_id}/ and list it in artifacts.

If blocked, end with ##SKILL:scaffold_complete_node{"node_id": {node_id}, "result": "blocked", "reason": "..."}##
```

### Web search wiring

Existing `skills/shared/web_search.py` is invoked by agents during `research`, `hardware_component`, and `decision` nodes. No changes to the skill itself. Results are stored in the node's `payload_json.search_results = [{title, url, snippet}, ...]`.

If `OLLAMA_API_KEY` is unset or web search fails, the skill falls back to DuckDuckGo HTML (already implemented).

### Error handling

- **Node fails**: status = `failed`, payload has `error_message`. UI shows red node with a Retry button.
- **Agent times out** (>15 min in_progress): scaffold runner sets back to `pending` with `payload.timeout_count++`; after 3 timeouts, marks `failed`.
- **SSE disconnect**: client reconnects with exponential backoff (1s, 2s, 4s, 8s, cap 30s).
- **DB write contention**: SQLite WAL + 5s busy_timeout (already configured); writes wrapped in transactions.
- **Orphan nodes**: scheduled cleanup script removes nodes with non-existent `project_id` (rare; only after hard project delete).

### Testing

Tests live in `tests/test_baza_scaffold_*.py`, follow existing patterns. Required coverage:

- `test_baza_scaffold_schema.py` — migration idempotency, all tables created, ALTER columns added
- `test_baza_scaffold_engine.py` — node/edge CRUD, dep satisfaction, progress math, weight rollup, decision override cascade
- `test_baza_scaffold_api.py` — every REST endpoint, including SSE first-event delivery
- `test_baza_scaffold_bom.py` — checkbox toggle, awaiting_part auto-unblock, promote-to-inventory
- `test_baza_scaffold_runner.py` — runner picks correct nodes, assigns by type, creates tasks, respects pause flag, respects manual_step skip
- `test_baza_scaffold_skills.py` — `scaffold_emit_nodes`, `scaffold_complete_node`, `scaffold_add_bom`, `scaffold_block_awaiting_part` round-trips

Live smoke: create a real test project with a one-line description, let it run for 60s, assert ≥ 3 nodes created + ≥ 1 BOM row + ≥ 1 SSE event delivered.

### Migration / rollout

- All new tables; no destructive changes to `projects`, `tasks`, or `ahb_*`.
- New `scaffold_paused INTEGER DEFAULT 0` column ALTER'd onto baza projects (idempotent).
- New systemd timer enabled but starts in Paused for all existing projects (the column defaults to 0, meaning unpaused — but existing projects have no scaffold nodes, so the runner is a no-op for them).
- New scaffold tab is the default for projects that have scaffold nodes; existing projects without nodes keep Overview as default.

## Phase split — what ships when

### Phase 1+2 (bundled, this build)
Everything above EXCEPT:
- `<model-viewer>` 3D preview for hardware nodes
- Cross-project supplies roll-up endpoint actually computing (returns empty `[]` in P1+2)
- Override modal's full side-by-side alternatives view (P1+2 ships a simpler dropdown-of-alternatives)
- Finish-line ⭐ animation polish (P1+2 ships ⭐ as a static emoji)

### Phase 3 (follow-up)
- `<model-viewer>` web component (vendored or CDN, decision deferred to Phase 3 brainstorm)
- Real cross-project supplies aggregation
- Polished decision override side-by-side UI
- ⭐ celebration animation
- Per-node `.glb`/`.stl`/`.png` upload UI in artifacts panel
- Public share link for a scaffold (read-only)

## Open considerations (intentionally deferred, not blocking)

- **Tree size limits**: at >200 nodes, D3 tidy-tree gets wide. Consider collapsible branches (collapse on parent click) in Phase 3.
- **Concurrent runner instances**: only one timer should run. Enforced by systemd unit (single shot) + `Type=oneshot`. If we ever go multi-host, add a SQLite leader lock.
- **Cost / rate limits on web_search**: if Ollama search hits a quota, DDG fallback covers; no hard cap built in. Revisit if it bites.
- **LLM determinism for decompositions**: re-running `scaffold/start` with `regenerate=true` will produce a different tree. Document this; users can fork & compare manually.

## Success criteria

- [ ] User can create a Baza project, type a description, click "Start scaffold", and within 60s see ≥3 nodes appear live in the tree
- [ ] Hardware-type description (Rubbish Taxi) produces a tree with hardware branches AND software leaves AND a populated BOM
- [ ] Software-only description (e.g., "build me a CLI password manager in Go") produces a tree with only software branches and no BOM rows
- [ ] Toggling a BOM checkbox auto-unblocks an `awaiting_part` node within one runner tick
- [ ] Progress bar updates in real time as nodes flip to `done`
- [ ] ⭐ appears when the `result` node is marked `done` and progress = 100%
- [ ] User can override an agent decision; downstream nodes re-run
- [ ] Inventory + Equipment modals persist data across projects
- [ ] All test files pass (target: ≥60 tests for the scaffold subsystem)
- [ ] No regressions in existing Baza Projects, AHB123, or Social Studio tabs
