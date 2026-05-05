# Sub-project #4 — Baza Projects Developer UI Envelope

**Date:** 2026-05-05
**Parent meta-spec:** `2026-05-04-baza-empire-platform-meta-spec.md`
**Status:** Iteration 1 implemented (envelope + first sub-tabs functional)

## Goal

A developer workspace inside the Baza dashboard where the user (and, in #5, agents) can scaffold, develop, test, deploy, and iterate on real projects — apps, dashboards, libraries, and eventually firmware. This spec defines the **envelope**: project shape on disk, manifest, dashboard listing/detail UI, the sub-tab structure, and the first functional sub-tabs (Overview, Brainstorm, Develop, Test, Deploy). The remaining sub-tabs (Render, Preview, Explore, Debug, Iterate) ship placeholders that pin the layout for follow-ups.

## What ships in iteration 1

### Project shape on disk

Every Baza project is a directory at `~/baza-empire/projects/<project_id>/` containing:
- `.baza-project.yaml` — manifest (id, name, type, kind, commands, deploy_targets, created_by, created_at, description, schema_version=1).
- `.git/` — auto-init'd repo with one bootstrap commit so any subsequent change is reviewable.
- `README.md` — auto-generated; user can edit.
- `artifacts/` — symlinked into `dashboard/artifacts/<project_id>/` so the existing Data Hub artifact UI sees them automatically.
- `events.jsonl` — placeholder for future per-project event mirror.

### Project types (v1)

`web-app | dashboard | library | esp-firmware | stm-firmware | lora-test | other`

`web-app`, `dashboard`, and `library` types come with default commands that actually execute in iteration 1. ESP/STM/LoRa types accept manifests and command edits but their runtime support (build/test/flash) ships in a follow-up — flashing in particular is a privileged action and needs the approval pipeline (#5) before it goes auto.

### Permissions

- **Read** is open.
- **Write** is sandboxed via `_safe_join` — file paths that escape the project dir return `None` and the API answers 403.
- `run_command` for slot=`deploy` is **gated**: refuses unless the caller passes `approved=True`. Long-running slots (`run`, `preview`) intentionally return an error in iteration 1 — they need a port-allocator + lifecycle manager that arrives in a follow-up.

### Dashboard surface

| Route | Purpose |
|---|---|
| `GET /projects` | List page with create modal. |
| `GET /projects/<id>` | Detail page with sub-tab UI. |
| `GET /api/baza/projects` | List Baza projects (default kind `baza-dev`). |
| `POST /api/baza/projects` | Create. Body `{name, type, description, id?}`. |
| `GET /api/baza/projects/<id>` | Detail (manifest + git summary). |
| `PUT /api/baza/projects/<id>` | Update manifest (id and created_at locked). |
| `DELETE /api/baza/projects/<id>` | Soft delete (renames `<dir>.deleted-<ts>`). `?hard=1` to wipe. |
| `GET /api/baza/projects/<id>/files?path=` | Directory listing within the sandbox. |
| `GET /api/baza/projects/<id>/file?path=` | Read file content (256 KB cap; truncate marker if exceeded). |
| `POST /api/baza/projects/<id>/file` | Write file (sandboxed). |
| `POST /api/baza/projects/<id>/run` | Run a manifest command slot (build/test/deploy). |

### Sub-tab status

| Tab | iteration 1 |
|---|---|
| Overview | Functional — manifest cards, command editor + run buttons, git summary. |
| Brainstorm | Functional — read/write `BRAINSTORM.md` in project root. |
| Develop | Functional — file tree + textarea editor with sandboxed save. |
| Render | Placeholder. |
| Preview | Placeholder. |
| Explore | Placeholder. |
| Test | Functional — runs `test` slot. |
| Debug | Placeholder. |
| Deploy | Functional — gated "Approve & Deploy" button calls `run_command(deploy, approved=True)`. |
| Iterate | Placeholder for #5 (agent project access). |

### Visibility wiring

- Project create emits `intent_parsed` event (`create_baza_project`) into `task_events`.
- Project run emits `tool_call` + `tool_result` events with parent linkage. Visible in `/chains` immediately.

## Non-goals for iteration 1

- Long-running `run`/`preview` lifecycle (port allocator, process manager).
- Render sub-tab (needs SD WebUI integration — uses Sam's existing imaging endpoints).
- Explore terminal/REPL (needs a websocket pty endpoint).
- Hardware test rig wiring for ESP/STM/LoRa.
- Agent autonomy inside projects — that's #5.
- File upload / drag-drop into project dir.
- Diff view for git changes.

## Tests

`tests/test_baza_projects.py` — 10 tests pass:
- create_and_list, get_includes_git_summary, update_manifest_preserves_id
- safe_join_blocks_escape, read_write_file
- run_command_test_slot, deploy_requires_approval
- invalid_kind_fallback, delete_soft, create_duplicate_raises

## Acceptance criteria — iteration 1

- User can hit `/projects`, click "+ New Project", and a project appears on disk + in the listing within 5 seconds.
- Detail page renders manifest, allows editing manifest commands, and runs the `test` slot end-to-end.
- "Approve & Deploy" button only runs the deploy command when explicitly clicked; un-approved API calls return 400.
- Every project create/run shows up live in `/chains` because `task_events` emits are wired.

## Follow-ups (named for later specs)

- **#4.5 Long-running preview/run** — port allocator + supervisor.
- **#4.6 Render sub-tab** — calls Sam's SD endpoints to produce mockups/diagrams.
- **#4.7 Explore terminal** — websocket pty restricted to the project sandbox.
- **#4.8 ESP/STM/LoRa runtime** — flashing pipeline behind approval gate.
- **#4.9 Diff + commit UI** — view dirty changes, commit from UI, push to remote (privileged).
