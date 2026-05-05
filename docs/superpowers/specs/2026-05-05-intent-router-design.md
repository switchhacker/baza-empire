# Sub-project #2 — Directive Command System

**Date:** 2026-05-05
**Parent meta-spec:** `2026-05-04-baza-empire-platform-meta-spec.md`
**Status:** Iteration 1 implemented (parser + dashboard endpoint + directive bar UI)

## Problem

The user wants to drive the platform with sentences like
`/create new baza project foo type=web-app` or `/test smoke-app` instead of
hunting through buttons. Same grammar should work in dashboard chat boxes
today and in agent LLM responses / Telegram messages tomorrow.

## What ships in iteration 1

### Parser — `core/intent_router.py`

`parse_intent(text)` returns a structured envelope:

```python
{"intent": "create_baza_project", "args": {"name": "foo", "type": "web-app"},
 "errors": [], "raw": "/create new baza project foo type=web-app"}
```

- Loose recognition: leading `/` optional, case-insensitive, "create new" / "new" / "create" all accepted as the same prefix.
- Strict args: `key=value` tokens are extracted with regex (quoted values supported); leftover non-kv text becomes `name` (for create) or `goal` (for slot directives).
- 17 unit tests (`tests/test_intent_router.py`).

### Recognized directives

| Directive | Status |
|---|---|
| `/create new baza project <name> [type=...]` | **Wired** — calls `core.baza_projects.create_project`. |
| `/create new ahb project name=... [from=...]` | **Wired** — internally POSTs to existing `/api/ahb/projects`. |
| `/test <id>` | **Wired** — runs manifest test slot. |
| `/deploy <id> [target=...]` | **Wired with approval gate** — first call returns `202 approval_required` and emits `approval_requested` event; second call with `extra={"approved":true}` runs. |
| `/develop <id> <goal>` | **Recognized, pending #5** — returns 202 with follow-up reference. |
| `/iterate <id> <goal>` | Same — pending #5. |
| `/render <id>` | Pending #4.6. |
| `/preview <id>` | Pending #4.5. |
| `/debug <id>` | Pending #4.x. |
| `/flash <id> [device=...]` | Pending #4.8 (privileged). |
| `/help` | Returns the directive list. |

### Dashboard endpoints

- `POST /api/intents/parse` — parse only, no side effects (handy for UI suggestion / preview).
- `POST /api/intents` — parse + dispatch + emit `intent_parsed` event into `task_events`.
- `GET /api/intents/help` — returns the printed help text.

### UI — Directive bar on `/projects`

A monospace input row at the top of the projects list:
- Slash prefix shown statically; user types the rest.
- Enter dispatches. `?` button shows help inline.
- Special-cased renderings: create → toast + redirect to detail; deploy → confirm dialog → re-dispatch with `approved=true`; pending intents → message naming the follow-up sub-project.
- Live result panel below the input, color-coded ok/err.

## Non-goals for iteration 1

- Telegram-side pickup of directives (needs the Telegram bot integration to call `/api/intents`).
- Agent LLM directive emission (needs a system-prompt change + a parser that pulls intents out of LLM output — ships with #5 because it's tied to agent project access).
- Auto-completion / fuzzy match for project ids (would need a chip-style picker; not iteration 1).
- Rate limiting / per-user authorization on `/api/intents`. The dashboard is already on a private network; revisit when exposing externally.

## Acceptance criteria — iteration 1

- All 17 parser tests pass.
- From `/projects`, typing `create new baza project demo type=library` and pressing Enter creates the project and redirects to `/projects/demo-…`.
- Typing `test demo-…` runs the test command and renders stdout/exit code.
- Typing `deploy demo-…` shows an approval prompt before running.
- Every dispatched intent produces an `intent_parsed` event in `/chains`.

## Follow-ups

- **#2.x Telegram intent listener** — add a hook in `core/agent.py` Telegram handler so `/create new …` from Specter's bot works the same way.
- **#2.y LLM intent emission** — train system prompts to emit `INTENT: /develop <id> <goal>` lines that `task_runner` parses and POSTs to `/api/intents`.
