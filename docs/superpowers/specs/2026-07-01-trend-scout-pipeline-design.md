# Trend-Scout Pipeline — Design Spec

**Date:** 2026-07-01
**Status:** Approved by Serge (brainstorming session)
**Goal:** Make the agents better at researching trends — both AI/tech for the empire and AHBCO business trends — and proposing concrete ideas Serge can approve into real tasks. One pipeline, minimal dashboard changes, local-first (all LLM work on local Ollama; sources fetched via plain HTTP).

## Why (context from audit)

- 272 skills exist and the event bus has 9 channels, but only 2/9 agents consume events; research capability is ad-hoc (Scout nominally researches, Specter has a 4h insight loop on a cloud model).
- Task runner parses `TASK_COMPLETE` as a substring anywhere in LLM output (false positives), has no lease/lock (double-run risk), and skills run with no arg validation or output caps.
- This project builds the trend-research capability on existing rails and hardens only the plumbing it depends on.

## Architecture Overview

```
config/trend_sources.yaml ──> core/trend_scout.py (timer, 4h)
                                 fetch → dedupe → local-LLM score/tag
                                 └─> dashboard/trend_scout.db (trend_items)
                                 └─> empire_knowledge (best items)
                                 └─> event: trend_scan_complete

core/idea_engine.py (timer, daily ~07:30)
    per-beat: top items → owning agent persona → 0–3 proposals
    └─> trend_scout.db (idea_proposals)  + event: idea_proposed
    └─> Telegram digest via Scout's bot (top ~5, inline ✅/❌ buttons)

BaseAgent callback-query hook (Scout registers trend handler)
    ✅ Approve → task in baza_projects.db ("Trend Ideas" project, suggested assignee)
                 + event: idea_approved  → existing task_runner executes
    ❌ Dismiss → proposal marked dismissed (stays searchable, ages out)
```

## Components

### 1. Source registry — `config/trend_sources.yaml`
Each entry: `name`, `type` (`rss` | `hn` | `reddit_json` | `http_json`), `url`, `beats` (list of tags), `enabled`. Seed ~15–20 sources:
- **AI/tech beats** (`local-ai`, `agent-tech`): r/LocalLLaMA JSON, HN Algolia API (queried per keyword set), HuggingFace trending-models feed, Ollama GitHub releases, key AI blogs' RSS.
- **AHBCO beats** (`remodeling-market`, `materials-pricing`, `marketing-seo`): remodeling/construction trade RSS (e.g. Remodeling Magazine, JLC, NAHB), materials/lumber price news, marketing & local-SEO feeds.
Adding a source = one YAML entry, no code change. Exact seed list finalized at implementation (feeds verified reachable).

### 2. Scanner — `core/trend_scout.py` + `baza-trend-scout.{service,timer}` (4h)
1. Load registry; fetch each enabled source with a per-source timeout (15s) and error isolation — one failing source never aborts the scan; failures increment a `fail_count` on the source row.
2. Parse entries → normalize to `{url, title, published_at, source, snippet}`.
3. Dedupe by `sha256(url)` against `seen_items`.
4. Batch-score new items with a local Ollama model via existing `OllamaClient`/GPU pool: relevance 0–10 per beat, tags, one-line summary. Strict JSON output; unparseable batch → skipped, retried next tick.
5. Items ≥ threshold (default 6) → `trend_items`; items ≥ 8 also published to `empire_knowledge` (existing insert path) so all agents see them in normal context.
6. Publish `trend_scan_complete` event with counts.

### 3. Storage — `dashboard/trend_scout.db` (new SQLite, WAL, same pattern as claw_reviews.db)
- `sources(name PK, fail_count, last_ok_at, last_error)`
- `seen_items(url_hash PK, url, first_seen_at)`
- `trend_items(id PK, url, title, source, beat, score, summary, published_at, scanned_at, used_in_round INT DEFAULT 0)`
- `idea_proposals(id PK, beat, agent_id, title, rationale, impact, effort, suggested_assignee, cited_item_ids JSON, status TEXT CHECK(status IN ('proposed','digested','approved','dismissed','expired')), task_id NULL, created_at)`
- FTS5 table over `idea_proposals(title, rationale)` for dedup of repeat ideas.

### 4. Idea engine — `core/idea_engine.py` + `baza-idea-round.{service,timer}` (daily 07:30)
- Beat → owner mapping (config constant): broad scan → scout_reeves; tooling/AI → claw_batto; marketing → duke_harmon; web/SEO → nova_sterling; business-ops/pricing → phil_hass.
- Per beat: top N unprocessed `trend_items` → prompt built from the agent's existing `agents/<id>/persona/*.md` files → local Ollama → 0–3 proposals as strict JSON: `title, rationale (must cite item ids), impact, effort, suggested_assignee`.
- FTS-dedupe each proposal against prior proposals (any status) — similar existing proposal → skip.
- Store as `proposed`; publish `idea_proposed` per proposal; mark items `used_in_round`.

### 5. Digest & approval
- After the idea round, send one Telegram digest from **Scout's bot** to Serge: top ~5 proposals (ranked by beat coverage + item scores), each message with inline **✅ Approve / ❌ Dismiss** buttons (`callback_data = trend:<action>:<proposal_id>`). Digest footer flags sources with `fail_count ≥ 5`.
- **BaseAgent gains a generic callback-query hook** (registry of prefix → handler); Scout's agent registers the `trend:` handler. This is a platform improvement any BaseAgent bot can reuse.
- **Approve:** idempotent (guarded by `status`) — insert task into `baza_projects.db` under project "Trend Ideas", assignee = `suggested_assignee`, description = rationale + cited links; save `task_id`; status → `approved`; publish `idea_approved`; edit the Telegram message to show ✅. Existing `task_runner` executes it from there.
- **Dismiss:** status → `dismissed`; message edited to ❌.
- Proposals older than 14 days still `proposed`/`digested` → `expired` (never re-digested, remain searchable).

### 6. Event wiring
- New `core/event_names.py` — constants for all channel names (existing 9 + `trend_scan_complete`, `idea_proposed`, `idea_approved`), each with a documented payload shape. New code imports constants; ad-hoc strings deprecated.
- `event_bus.publish` failures are logged (journal) instead of silently dropped.
- BaseAgent subscribes to the three trend events (default handler: log + optional agent notification for `idea_approved` assignee).

### 7. Targeted plumbing hardening
1. **Task leases** — `lease_owner`, `lease_until` columns on tasks; `task_runner` atomically acquires a lease (UPDATE … WHERE lease expired/null) before running and skips leased tasks. Fixes double-run.
2. **Anchored completion signals** — `TASK_COMPLETE` / `TASK_IN_PROGRESS` / `TASK_BLOCKED` matched only as an anchored token at line start (regex `^TASK_COMPLETE\b`, checked from the end of the response), not substring-anywhere.
3. **Skills engine guards** — before spawn: `SKILL_ARGS` must parse as a JSON object (else structured error, no spawn). After: stdout capped at 32KB with `[truncated N bytes]` notice; error results prefixed `[SKILL ERROR:<kind>]` where kind ∈ `{not_found, bad_args, timeout, nonzero_exit}`.
4. These changes must not alter behavior for currently-passing paths (guarded by regression tests against existing fixtures).

## Error handling
- Per-source fetch isolation + fail counters (surfaced in digest footer at ≥5).
- LLM scoring/idea failures: skip batch, retry next tick; never crash the timer unit.
- Digest send failure: logged, proposals stay `proposed`, next day's digest includes them.
- Approval callback failure: Telegram gets an error toast; no partial task insert (single transaction).

## Testing (TDD, existing pytest setup)
- Feed parsing: canned RSS/HN-Algolia/Reddit-JSON fixtures → normalized items.
- Dedupe: same URL twice → one `trend_items` row.
- Score parsing: mocked Ollama responses incl. malformed JSON → skip-not-crash.
- Idea engine: mocked LLM → proposals stored, FTS dedup blocks near-duplicates, citation ids required.
- Approve→task: tmp SQLite for both DBs; approve inserts exactly one task; double-approve inserts zero more.
- Digest formatting: top-5 selection, callback_data shape, fail-count footer.
- Leases: two simulated runners, one task → exactly one executes.
- Signal anchoring: response containing "the TASK_COMPLETE marker" mid-prose → NOT complete; anchored line → complete.
- Skills guards: bad args / oversized output / timeout → correct structured error, existing good-path fixtures unchanged.

## Build order
1. `trend_scout.db` schema + source registry loader
2. Scanner (fetch/parse/dedupe/score) + timer units
3. Idea engine + persona prompts + FTS dedup
4. Digest + BaseAgent callback hook + approve→task
5. Hardening: leases → anchored signals → skills guards → event constants/logging

## Out of scope
- Dashboard UI for trends (Telegram + empire_knowledge only, per Serge).
- SearXNG or any web-search service (phase 2 candidate if feed coverage proves thin).
- Specter/phantom changes; coordinator rework; full event-bus overhaul beyond the items above.
