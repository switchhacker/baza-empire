# Skills Page Rebuild — Design

**Date:** 2026-06-26
**Status:** Approved (design)
**Surface:** `dashboard/templates/skills.html`, `dashboard/app.py` skill routes
**Depends on:** `core/skill_registry.py` (existing — used, not modified)

## Problem

The current `/skills` page is a sidebar list (name, scope badge, 50-char
description) plus a raw Python textarea with Save/Run/Delete. It has real gaps:

1. The description field exists in the UI but `api_skill_save()` **silently
   discards it** — only the code is written to disk.
2. Rich metadata (`SKILL_META`: `category`, `when_to_use`, `args`) is never
   shown or editable, even though the agent registry/selector reads it.
3. Creating a skill means typing Python into a textarea — no guidance.
4. `api_skill_read()` / `api_skill_save()` only handle `skills/shared/`; the
   list shows per-agent skills but they cannot be opened or edited.

Goal: make the page (a) easy to scan / understand what each skill does, (b)
offer a simple guided way to create a skill, (c) let you customize any skill
including its metadata, (d) clearly label what's what.

## Scope

Rebuild the existing `/skills` page **in place**. No relocation, no new page.
`core/skill_registry.py` is used via its existing helpers (`categories()`,
`infer_category()`, `extract_meta()`, `build()`) and is **not modified**.

Categories are the fixed set `skill_registry` already defines:
`financial, materials, project, client, marketing, infrastructure, data, code,
ai, web, document, general`.

## Architecture

Three layers change:

- **`dashboard/templates/skills.html`** — new layout: category-grouped list
  (left) + a detail/edit panel and a guided "New Skill" form (right).
- **`dashboard/app.py`** skill routes — fix metadata persistence, support
  per-agent read/save/run/delete, trigger a manifest rebuild on save.
- **`core/skill_registry.py`** — unchanged; consumed only.

### Component boundaries

| Unit | Responsibility | Interface |
|------|----------------|-----------|
| `skills.html` list pane | Render category-grouped, searchable list | Consumes `/api/skills/list` JSON |
| `skills.html` detail/edit pane | Show/edit labeled metadata fields + code | Consumes `/api/skills/read`, posts `/api/skills/save` |
| `skills.html` new-skill form | Guided create + skeleton generation | Posts `/api/skills/save` with new name+scope |
| `/api/skills/list` | List all skills with metadata | Returns array of `{name, scope, category, summary, when_to_use, size, modified}` |
| `/api/skills/read` | Read one skill's metadata + code | Returns `{name, scope, summary, when_to_use, category, args, code, path}` |
| `/api/skills/save` | Compose + write `.py`, rebuild manifest | Accepts `{scope, name, summary, when_to_use, category, args, code}` |
| `/api/skills/run` | Execute a skill (scope-aware) | Accepts `{scope, name, args}` |
| `/api/skills/delete` | Delete a skill file (scope-aware, protected list) | Accepts `{scope, name}` |

## UI

### List (left pane) — "label what's what"

- Grouped by category, each group a collapsible header with a count, e.g.
  `FINANCE (6)`.
- Each row: **name** (monospace) · one-line summary · scope badge
  (`shared` = green, agent-name = purple).
- Search box at top filters across all groups client-side over the loaded list
  (same mechanism as today).
- Prominent **`+ New Skill`** button at the top.

### Detail / edit panel (right pane)

Clicking a skill shows **labeled fields** mapping to `SKILL_META`, not raw code
first:

- **Name** — read-only when editing an existing skill.
- **Scope** — shared / agent name; read-only when editing an existing skill.
- **What it does** — `summary`.
- **When to use** — `when_to_use`.
- **Category** — dropdown of the fixed categories; default = inferred.
- **Arguments** — repeatable `name` + `hint` rows (`args`).
- **Code** — Python in a collapsible "Advanced / code" section, always
  available for power-editing.
- Buttons: **Save**, **Run**, **Delete** (protected list still enforced).

### New Skill — guided form + code ("Both")

`+ New Skill` opens the same labeled-fields form, blank, with a **scope
selector** (shared, or pick an agent). "Generate skeleton" scaffolds the
Python: shebang, docstring (= summary), `SKILL_META = {...}`, `SKILL_ARGS`
JSON parsing, a `# your logic here` stub, and `print(result)`, then drops the
user into the code section to fill in logic. The raw editor stays available
throughout.

## Data flow — Save (the risky part)

On Save the backend composes the `.py` file deterministically:

```
#!/usr/bin/env python3
"""<summary>"""

SKILL_META = {
    "category": "<category>",
    "summary": "<summary>",
    "when_to_use": "<when_to_use>",
    "args": { "<arg>": "<hint>", ... },
}

<code body>
```

Rules:

- The **leading region** = the docstring + the `SKILL_META` assignment. Save
  regenerates ONLY this leading region from the form fields.
- The **code body** = everything after `SKILL_META` in the existing file (or
  the generated skeleton for a new skill). Save **never rewrites logic below
  `SKILL_META`** — it preserves the body verbatim. This protects real skill
  logic from form round-trips.
- If an existing file has no `SKILL_META`, the body = everything after the
  module docstring (or the whole file minus shebang if no docstring); the new
  `SKILL_META` block is inserted ahead of it.
- After writing, `chmod +x`, then call `skill_registry.build()` (best-effort).
  Save still returns success if the rebuild raises — the rebuild error is
  logged, not fatal.

Round-trip guarantee: a skill saved through the form must parse back via
`skill_registry.extract_meta()` to the same `category / summary / when_to_use /
args` values.

## Routes

- `GET /api/skills/list` — return, per skill: `name, scope, category, summary,
  when_to_use, size, modified`. Source metadata from the manifest where present
  (`skill_registry`), else live file scan via `describe_skill`.
- `GET /api/skills/read/<scope>/<name>` — extend to read **per-agent** paths
  (`agents/<id>/skills/<name>.py`) as well as `shared`. Return parsed
  `SKILL_META` fields + code.
- `POST /api/skills/save` — accept `{scope, name, summary, when_to_use,
  category, args, code}`; compose file per the Save rules; write to the correct
  shared/agent path; `chmod +x`; rebuild manifest (best-effort).
- `POST /api/skills/run` — accept `scope` so per-agent skills run; otherwise
  unchanged (`SKILL_ARGS` env, `AGENT_ID`, 30s timeout, output caps).
- `POST /api/skills/delete` — accept `scope`; per-agent aware; protected list
  (`create_skill`, `save_artifact`, `artifact_save`, `update_task`) still
  blocks deletion.

## Error handling & edge cases

- Name validation keeps existing regex `^[a-z][a-z0-9_]{1,49}$`.
- Per-agent scope is path-traversal guarded: the agent id must match an
  existing `agents/<id>` directory; reject otherwise.
- Files that aren't a clean `docstring + SKILL_META + body` shape: the code
  section shows the raw file verbatim; Save replaces/inserts only the leading
  docstring + `SKILL_META` and preserves the body below.
- Hand-written `SKILL_META` is parsed into the fields on read, so a save
  round-trip preserves it.
- Manifest rebuild failure is logged and non-fatal to Save.

## Testing

Pytest against the Flask routes:

- `list` returns the grouped fields (`category`, `summary`, `when_to_use`).
- `save` writes a `SKILL_META` block that `skill_registry.extract_meta()`
  reads back identically (round-trip).
- per-agent `save` lands in `agents/<id>/skills/` and per-agent `read` opens it.
- metadata round-trip does not clobber the code body (body preserved verbatim).
- inserting `SKILL_META` into a file that lacks it preserves existing logic.
- `delete` still blocks protected skills.
- path-traversal scope is rejected.
- manifest rebuild is triggered on save (and save survives a rebuild failure).

Frontend verified manually after `sudo systemctl restart baza-dashboard`
(Jinja template cache, `debug=False`).

## Out of scope

- Relocating Skills into the Agents page as a sub-tab.
- Editing `core/skill_registry.py` or the manifest schema.
- Versioning / history of skill edits.
- Editing tool-server endpoints (type `tool`) — those are not files.
