# Agent Situational Awareness System — Design

**Date:** 2026-05-11
**Author:** Claude (Opus 4.7, 1M ctx) for Serge
**Status:** Approved by Serge for implementation

## Problem

Agents in `baza-empire/agent-framework-v3` boot with their persona from `config/agents.yaml` and never learn anything else about the empire. Concretely:

- Simon still references mining when generating briefings — mining was killed 2026-05-10.
- Agents talk about "migrating ahb123.com" — the migration completed 2026-04-23.
- The Pulse claim-verifier landed today; no agent knows it exists or that fabrications are now flagged.
- Agents do not know their own directory, what skills they have available, or which siblings are online.

Each agent runs on a sub-10B Ollama model with an 8–32k context window. We cannot dump unbounded state into every prompt, but the current behavior — zero situational awareness — is causing visible drift (see Pulse: Simon = 5 fabrications, 0 ships in 30 days).

## Solution Overview

Three pieces:

1. **`EMPIRE_STATE.md`** — hand-curated source-of-truth file at the framework root. Three default sections (LIVE / KILLED / RECENT) plus `## TOPIC: <slug>` blocks Serge maintains.
2. **Boot-time auto-injection** — `core/base_agent.py` and `core/agent.py` read `EMPIRE_STATE.md` at process start, build a compact `<EMPIRE_STATE>` header, and prepend it to `system_prompt`. 4 KB hard cap.
3. **`skills/shared/self_orient.py`** — topic-scoped refresh skill. Agent emits `##SKILL:self_orient{"topic":"X"}##` mid-conversation; SkillsEngine subprocess returns the matching `## TOPIC: X` block + the most recent session-log lines mentioning the topic.

Plus a nightly RECENT-sync script that grafts new session-log dates into `EMPIRE_STATE.md` automatically.

## Components

### EMPIRE_STATE.md (framework root)

Plain markdown, structured, 1.5 KB target / 4 KB hard cap. Schema:

```
## LIVE
- <bullet> — <one-line context>
...

## KILLED
- <bullet> — <reason> — <date killed>
...

## RECENT
- YYYY-MM-DD <one-line change>
- ... (rolling 7 days, newest first; older lines pruned by sync script)

## TOPIC: <slug>
<2-6 line authoritative answer to "what is the current state of X?">

## TOPIC: <slug2>
...
```

Owner: Serge (LIVE, KILLED, TOPIC). System (RECENT via nightly sync).

### Boot-time injection

In both `core/base_agent.py` (new agents) and `core/agent.py` (legacy, Simon):

```python
def _build_empire_state_header(self) -> str:
    """Return a <EMPIRE_STATE> block to prepend to system_prompt. 4 KB cap."""
    ...
```

Behavior:
1. Read `EMPIRE_STATE.md` (LIVE + KILLED + RECENT sections; TOPIC blocks excluded from boot injection — those are skill-only to keep boot tight).
2. Add `YOU ARE: <agent_id> — <role>` line.
3. Add `YOUR SKILLS:` line — comma-separated list from `agents/<agent_id>/skills/` + `skills/shared/`.
4. Add `TEAM ONLINE:` line — `systemctl is-active baza-agent-<agent>` for each known agent → green/red dot.
5. Trailing rule: "If unsure about X, run `##SKILL:self_orient{\"topic\":\"X\"}##` before responding."

If total exceeds 4 KB, truncate RECENT bullets (oldest first) until within budget.

The block is wrapped in literal tags:
```
<EMPIRE_STATE>
... content ...
</EMPIRE_STATE>
```

So the LLM treats it as a system fact block, not user input.

### skills/shared/self_orient.py

Standard Baza skill — reads `os.environ.get("SKILL_ARGS")` JSON, prints result to stdout. Args:

- `{}` (no topic) → return the boot snapshot, freshly regenerated (LIVE/KILLED/RECENT + YOU ARE/SKILLS/TEAM ONLINE for the calling agent).
- `{"topic": "X"}` → look up `## TOPIC: X` in `EMPIRE_STATE.md` (case-insensitive). If found, return that block + the most recent 2 session-log entries that mention `X`. If not found, grep `EMPIRE_STATE.md` and `~/.claude/projects/-home-switchhacker/memory/baza-map.md` for the keyword, return top 5 hits, each ≤300 chars.
- `{"topic": "myself"}` → return calling agent's own directory tree (1 level deep), full skills list, last 5 `task_journal` rows.

Output capped at 1.5 KB per call to keep the subprocess result manageable.

Calling agent id resolution: `os.environ.get("BAZA_AGENT_ID")` — set by SkillsEngine when invoking a skill on behalf of an agent. Fallback to `os.environ.get("AGENT_ID")` or `"unknown"`.

### RECENT auto-sync

`scripts/empire_state_recent_sync.sh` — bash, idempotent, append-only-then-prune:

1. Read `/home/switchhacker/Desktop/baza-session-log.md`.
2. Extract `### YYYY-MM-DD HH:MM | <topic>` headers from the last 24h.
3. Distill each to a one-line bullet (the heading text after the timestamp).
4. Prepend new bullets to `## RECENT` in `EMPIRE_STATE.md`, deduped against existing bullets.
5. Trim `## RECENT` to most-recent 15 lines.
6. Commit the change (or just write — no git required since the framework dir isn't a repo).

Wired to a systemd timer or cron, daily at 00:05 local time.

## Data Flow

**Boot path:**
```
systemd starts baza-agent-<id>.service
  → main.py loads agent class
    → BaseAgent.__init__()
      → _build_empire_state_header() reads EMPIRE_STATE.md + systemctl + skills/
      → self.system_prompt = header + "\n\n" + persona_prompt
  → agent runs with header in every LLM call (system_prompt is sticky)
```

**Refresh path:**
```
Agent LLM response contains: ##SKILL:self_orient{"topic":"mining"}##
  → SkillsEngine intercepts the pattern
    → subprocess.run(python skills/shared/self_orient.py, env={SKILL_ARGS: ..., BAZA_AGENT_ID: ...})
      → script reads EMPIRE_STATE.md → finds ## TOPIC: mining → returns block + matching session-log lines
  → SkillsEngine replaces pattern with [SKILL RESULT: <output>]
  → agent's next turn includes the SKILL RESULT in conversation history
```

**Nightly path:**
```
systemd timer fires empire-state-sync.service at 00:05
  → scripts/empire_state_recent_sync.sh reads session-log tail
    → distills + dedupes into ## RECENT
    → writes EMPIRE_STATE.md
  → agents pick up the change at next process restart (or via self_orient at any time)
```

## Error Handling

| Failure | Behavior |
|---|---|
| `EMPIRE_STATE.md` missing | Boot header reduces to `YOU ARE / SKILLS / TEAM ONLINE` only. No crash. |
| `EMPIRE_STATE.md` over 8 KB | Header truncates RECENT then KILLED; LIVE always survives. |
| `systemctl` not available | TEAM ONLINE line omitted from header. |
| `self_orient` skill subprocess timeout (>5s) | SkillsEngine reports failure; agent sees `[SKILL ERROR: timeout]`. |
| `## TOPIC: <unknown>` requested | Skill falls back to grep across EMPIRE_STATE.md + baza-map.md. If still nothing, returns "no information on topic '<X>'; try a broader keyword". |
| RECENT sync script run on day with empty session-log | No-op. Existing RECENT untouched. |

## Testing

1. **Unit:** `tests/test_self_orient.py` — mock EMPIRE_STATE.md tmpfile + session-log tmpfile, assert topic lookup returns expected block; assert unknown-topic fallback grep works; assert byte-cap enforced.
2. **Integration:** Boot Simon's agent in test mode, capture the system_prompt, assert it contains `<EMPIRE_STATE>` block and Simon's identity.
3. **End-to-end:** Restart `baza-agent-simon-bately.service`, send Simon a Telegram message asking about mining. Assert response references KILLED status (not "mining is running"). Manual verification.

## Files Changed

| File | Action |
|---|---|
| `EMPIRE_STATE.md` (framework root) | NEW — seed by Serge/Claude with LIVE/KILLED/RECENT + 3-5 TOPIC blocks |
| `skills/shared/self_orient.py` | NEW |
| `core/base_agent.py` | EDIT — add `_build_empire_state_header()`, modify `__init__` to prepend |
| `core/agent.py` | EDIT — same as base_agent (legacy path for Simon) |
| `scripts/empire_state_recent_sync.sh` | NEW |
| `scripts/empire-state-sync.service` + `.timer` (systemd) | NEW (optional in iter 1) |
| `tests/test_self_orient.py` | NEW |

## Out of Scope

- Per-agent customization of the boot header (every agent gets the same LIVE/KILLED/RECENT; personalization is in YOU ARE/SKILLS/TEAM ONLINE).
- Real-time push notification of state changes mid-conversation. Agent must invoke `self_orient` to refresh.
- Cross-empire state sync between baza and phantom. Phantom's Specter runs OpenClaw (separate runtime); a future iter can mirror EMPIRE_STATE.md.
- LLM-summarized state. Source of truth is human-curated markdown; no model rewrites it.

## Risks

- **Stale TOPIC blocks** — if Serge stops curating, agents will believe outdated facts about ahb123 or mining. Mitigation: nightly sync only owns RECENT, which is the most volatile section. Topic blocks stay until Serge updates them. The on-demand `self_orient` also includes session-log mentions, so stale topic blocks get a fresh side-by-side context.
- **Token budget creep** — as Serge adds more LIVE bullets or TOPIC blocks, EMPIRE_STATE.md could blow the 4 KB cap. Mitigation: hard truncate at write time, and the boot header only injects LIVE/KILLED/RECENT — TOPIC blocks are skill-only.
- **Two-codepath duplication** — adding the header to both `core/base_agent.py` and `core/agent.py` means future schema changes touch both files. Mitigation: extract the helper into `core/empire_state.py` so both files just call `from core.empire_state import build_header`.

## Success Criteria

- Simon's next briefing references ahb123 as LIVE and mining as KILLED, not "we should migrate" / "mining earnings".
- Pulse tab drift score for Simon trends down over the following week as fabrication count drops.
- Any agent can answer "what's the current state of X" with a single `self_orient` call where X is one of the curated TOPIC slugs.
- Total token overhead per agent at boot ≤ 1200 tokens.
