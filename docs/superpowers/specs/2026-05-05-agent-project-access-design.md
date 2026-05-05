# Sub-project #5 — Agent Project Access

**Date:** 2026-05-05
**Parent meta-spec:** `2026-05-04-baza-empire-platform-meta-spec.md`
**Status:** Iteration 1 implemented (skill wrapper + system-prompt wire-up)

## Problem

After #4, Baza Projects exist as user-facing workspaces — but the user is the only one who can populate them. Agents need read/write/test/deploy access so they can complete tasks like "develop a lead-capture web app, test it, ship it for review."

## Solution

Add `skills/shared/baza_proj.py` — a single parameterized skill that proxies the dashboard's `/api/baza/projects` HTTP API. Same shape as `ahb_api` (see #3 design). Agents now treat Baza Projects as a first-class action surface: list, create, get manifest, file_read, file_write, run, delete.

### Action catalog

| Action | Endpoint | Privileged |
|---|---|---|
| `list` | `GET  /api/baza/projects` | no |
| `get` | `GET  /api/baza/projects/{id}` | no |
| `create` | `POST /api/baza/projects` | no |
| `update` | `PUT  /api/baza/projects/{id}` | no |
| `delete` | `DELETE /api/baza/projects/{id}` | **yes** |
| `files` | `GET  /api/baza/projects/{id}/files` | no |
| `file_read` | `GET  /api/baza/projects/{id}/file?path=` | no |
| `file_write` | `POST /api/baza/projects/{id}/file` | no (sandboxed by `_safe_join`) |
| `run` | `POST /api/baza/projects/{id}/run` | depends on slot |
| `raw` | any `/api/baza/...` | depends |

`run` slot=`deploy` and slot=`flash` are **always** privileged — gated even if the action itself isn't, matching meta-spec D5.

### Permissions in practice

- Read everywhere is fine.
- Writes go through the sandbox check on the dashboard side; agent attempts to escape return 403.
- Privileged actions emit `approval_requested` with the project_id and refuse to run. The agent (or Serge via `/chains`) re-issues the call with `args.approved=true` to proceed.

### Visibility

Each call emits `tool_call` (with `project_id`) before the HTTP request and `tool_result` after. The events render in the project's chain view in `/chains`, filterable by project_id.

### System prompt

Added a "BAZA PROJECTS" block to the shared agents prompt so every inheriting agent sees the workflow with copy-pasteable examples.

## Tests

`tests/test_baza_proj_skill.py` — 8 tests pass:
- help_lists_actions, missing_required_args_create, unknown_action
- delete_blocked_without_approval, run_deploy_slot_blocked_without_approval
- run_test_slot_does_not_require_approval (does NOT trip the gate)
- raw_path_must_start_with_api_baza, file_read_uses_query_string

## Acceptance criteria — iteration 1

- An agent can:
  1. Receive a task like "build a small Flask app that exposes /healthz."
  2. Call `baza_proj.create` to scaffold the project.
  3. Call `baza_proj.file_write` to add `app.py`, `requirements.txt`, and tests.
  4. Call `baza_proj.run` slot=test to validate.
  5. Save artifacts back to Data Hub via the existing artifact_save skill.
  6. Tell Serge to review at `/projects/<id>` — every step visible in `/chains`.
- Privileged actions never run without explicit approval.
- File writes can't escape the project sandbox.

## Closes the loop with #2

The directive `/develop <id> <goal>` (recognized in #2 as pending) becomes
implementable here: `task_runner` can pass the goal to an agent that already
knows how to use `baza_proj.create/file_write/run` to deliver it. That last-mile
wiring (assigning a `develop` directive to a specific agent and tracking the
outcome) is left as a follow-up because it requires choosing the right agent
per project type and is more about agent routing than the toolbelt itself.

## Non-goals for iteration 1

- Pre-flight git status/diff inspection from the skill (would be a nice "show me what changed before I commit" affordance — useful but not blocking).
- Multipart upload for binary files. The `raw` action can be used; typed wrapper later.
- Per-agent project assignment / locking. Right now any agent can write to any project; if two agents collide that's a coordination bug we'd address with project ownership.
- Long-running run/preview from inside a skill (same constraint as #4 — needs supervisor + port allocator).
