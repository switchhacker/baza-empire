# Sub-project #3 — Agent AHB Toolbelt

**Date:** 2026-05-05
**Parent meta-spec:** `2026-05-04-baza-empire-platform-meta-spec.md`
**Status:** Iteration 1 implemented (single AHB API skill + system-prompt wire-up)

## Problem

Existing skill `ahb123_query.py` lets agents read/write AHB tables directly via SQLite. That covers basic CRUD but misses the dashboard's HTTP-layer logic — quote PDF generation, receipt OCR, voice synthesis, blueprint rendering, architect img2img, project status sync, geocoding, validation. Agents need that surface so they can do anything the user does through the AHB123 UI.

## Solution

Add `skills/shared/ahb_api.py` — a single parameterized skill that proxies the dashboard HTTP API.

### Action catalog

The skill supports ~50 typed actions covering:
- Clients, Projects, Quotes, Invoices, Receipts (CRUD + workflow ops)
- Payroll, Employees, Events
- Estimates (list, create, generate)
- Voice (voices list, configs CRUD, synthesize, logs, stats)
- Blueprints (CRUD + render + from-description + from-photo)
- Architect (analyze, generate, img2img, transform)
- Chats (list, messages, history, stats, export, escalate)
- Activity feed
- Plus `raw` for any `/api/...` path the user wants to hit directly.

Full list with `##SKILL:ahb_api{"action":"help"}##`.

### Permissions

- Read actions: open.
- Create / update actions: open (the dashboard endpoints already enforce shape).
- Delete actions and anything tagged privileged: gated. The skill emits an `approval_requested` event and returns `approval_required: true` instead of executing. The agent (or the user) must re-issue the same call with `args.approved: true` to proceed.

### Visibility

Each call emits a `tool_call` event before the HTTP request and a `tool_result` event after, with `parent_event_id` linking them. So the chain in `/chains` shows e.g.:
- skill_invoked: ahb_api {action: "blueprints_render", args: {...}}
- tool_call: ahb.blueprints_render
- tool_result: ahb.blueprints_render ok=True status=200
- skill_result: ahb_api ok

### System prompt

Added a "FULL AHB HUB API" block to the shared agents prompt section in `config/agents.yaml` so every agent inheriting from it sees the new skill with examples. Discovery also continues to work via `skill_catalog` since the skill has a clear docstring.

## Tests

`tests/test_ahb_api_skill.py` — 6 tests pass:
- help_lists_actions, missing_required_args, unknown_action
- privileged_blocks_without_approval
- raw_path_must_start_with_api, raw_succeeds_against_test_endpoint (graceful fail when dashboard unreachable)

## Acceptance criteria — iteration 1

- Any agent can list AHB resources, create new ones, and trigger workflow ops (PDF, OCR, render, synth) via a single skill call.
- Destructive ops require an explicit second call with `approved=true`.
- Every call surfaces in `/chains` as a tool_call → tool_result pair.
- Iteration 1 covers ~50 endpoints. Adding new endpoints later is one line in the ACTIONS table.

## Non-goals for iteration 1

- File upload through the skill (receipt image upload, blueprint photo upload). The `raw` action can be used for now; multipart upload deserves a typed wrapper in iteration 2.
- Per-agent permission scoping (right now any agent can call any action). Per-agent role mapping ships when #5 lands and we know which agents need what.
- Streaming long-running operations. Blueprint photo jobs etc. return immediately with a job id — agents poll via the job-status action that already exists in dashboard.
