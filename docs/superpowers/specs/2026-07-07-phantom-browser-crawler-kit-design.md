# Phantom Browser — AI Web Crawler Kit

**Date:** 2026-07-07
**Status:** Approved by Serge (design); implementation plan next
**Owner:** Phantom Browser service on baza; skills for all 9 agents

## Problem

Agents have no real web capability. Today's stack is HTTP-only:
`web_search` scrapes DuckDuckGo HTML with regex (brittle), `web_fetch` is dead
(requires an `OLLAMA_API_KEY` that is not set anywhere), and `scrape_page` is
urllib + regex tag-stripping — no JS rendering, no interaction, no crawling.
Specter's OpenClaw config declares `browser: engine: browser-use` but the
package was never installed on phantom; "Phantom browser" exists only as
aspiration.

Serge wants a full AI web crawler kit — Firecrawl-style verbs plus multistep
interactive browsing — usable by all 9 agents through the existing skill
scaffold and plan→act→observe loop.

## Decisions (made with Serge, 2026-07-07)

1. **Placement:** central service on baza. Phantom/Specter calls it over
   Tailscale; no browser stack on the NUC.
2. **Scope:** full kit — scrape/crawl/map/extract/search **and** interactive
   sessions **and** logged-in persistent profiles.
3. **Guardrail:** read free, write gated. In logged-in profile sessions, any
   state-changing action pauses for Telegram approval; silence (5 min) = no.
4. **Search:** self-hosted SearXNG in docker on baza (:8181; 8080 is
   Nextcloud). Local-first, no API keys.
5. **Approach:** custom FastAPI + Playwright service (Approach A). Rejected:
   `browser-use` library (runs its own competing agent loop, second-class
   local-model support) and Firecrawl OSS self-host (heavy Node/Redis stack,
   cloud-leaning extract, still needs a second Playwright layer for sessions).

## Architecture

New directory: `baza-empire/agent-framework-v3/browser/`

| File | Responsibility |
|---|---|
| `server.py` | FastAPI app, all routes, request validation |
| `engine.py` | Playwright lifecycle: browser launch/relaunch, context pool (max 4 concurrent), per-domain politeness delay |
| `page_to_md.py` | Rendered HTML → clean markdown + title/meta/links; size caps |
| `crawler.py` | BFS frontier, include/exclude patterns, robots.txt (bulk crawl only), async jobs |
| `extractor.py` | scrape → local Ollama model + JSON schema → validated JSON |
| `sessions.py` | Stateful interactive sessions, numbered-element reads, idle reaper |
| `gate.py` | Write-action detection + Telegram approval flow |
| `login_helper.py` | Headed CLI Serge runs on baza's desktop to seed profiles |

- **Service:** `baza-phantom-browser.service`, port **:8100**, runs as
  `switchhacker` (no root needed; profiles stay private).
- **Runtime deps:** `playwright` in the framework venv + Chromium
  (`playwright install chromium`); HTML→markdown via `trafilatura`
  (article/main-content extraction) with `markdownify` fallback for pages
  trafilatura can't parse.
- **SearXNG:** docker container on **:8181**, JSON API enabled. The kit's
  `/search` proxies it.
- **State:** `dashboard/phantom_browser.db` (SQLite, WAL): crawl jobs,
  pending approvals, page-cache index. Jobs survive restarts; in-flight
  pages re-queued.
- **Specter:** phantom's OpenClaw `browser:` config repointed to
  `http://100.127.118.103:8100` (baza-1 Tailscale IP).

## Stateless verbs (Firecrawl-style)

| Route | Behavior |
|---|---|
| `POST /scrape` | `{url, formats?, wait_ms?, screenshot?}` → render in Chromium → markdown, title, meta, links, optional screenshot path. Short-TTL page cache. |
| `POST /search` | `{query, n?, fetch_content?}` → SearXNG JSON; `fetch_content: true` also scrapes top N results in one call. |
| `POST /map` | `{url, limit?}` → sitemap.xml + shallow link sweep → URL list. |
| `POST /crawl` | `{url, max_pages?=50, max_depth?, include_paths?, exclude_paths?, same_domain?=true}` → `job_id`. `GET /crawl/{id}` → status + accumulated pages. Respects robots.txt unless `ignore_robots: true`. |
| `POST /extract` | `{url \| urls \| content, schema, prompt?}` → scrape → local Ollama (configurable model; **local only**, per hard rule) → JSON validated against schema, retry on validation failure. |

All outputs are markdown-clean and size-capped to fit agent context windows.

## Interactive sessions

- `POST /session` `{profile?}` → `session_id`. Idle timeout ~10 min
  (reaper closes context). `DELETE /session/{id}` explicit close.
- `POST /session/{id}/goto|click|type|press|scroll|back` — act by element
  index or selector.
- `POST /session/{id}/read` → page markdown **plus numbered interactive
  elements** (`[3] button "Next page"`, `[7] input "Search"`), so the LLM
  acts by index (browser-use pattern, composed with our own agent loop).
- `POST /session/{id}/screenshot` → PNG saved to artifacts, path returned.
- Structured error JSON on every failure (timeout, detached element,
  nav error) so agents can recover mid-loop.

## Logged-in profiles

- Named persistent Chromium profiles at `browser/profiles/<name>/`,
  mode 0700, excluded from git and the Claw reviewer.
- Seeded by Serge only: `python -m browser.login_helper <name>` opens a
  headed browser on baza's desktop; he logs in and closes; agents then pass
  `profile: "<name>"` when creating a session.
- Agents can never create or modify profiles.

## Write gate (server-side, not prompt-side)

- Applies **only** to profile (logged-in) sessions; anonymous sessions are
  ungated.
- Gated: `click`/`press Enter` on submit-ish elements — form submits and
  buttons/links whose text or attributes match post/send/submit/buy/pay/
  order/delete/confirm/publish patterns (heuristic list in `gate.py`,
  unit-tested).
- Flow: gated action → row in `pending_approvals` → Telegram message to
  Serge via the existing `core/telegram_fmt.py` outbound path → API returns
  `{status: "pending_approval", approval_id}` → approve executes the action,
  deny or **5-minute timeout = denied** (Specter rule: silence ≠ consent).

## Agent-facing skills (`skills/shared/`, all with `SKILL_META`)

| Skill | Role |
|---|---|
| `browse.py` | Interactive meta-skill: `{action: goto\|click\|type\|press\|scroll\|read\|screenshot\|close, session_id?, ...}` — one skill so the loop chains steps |
| `web_scrape.py` | `/scrape` client (successor to `scrape_page`) |
| `crawl_site.py` | `/crawl` start + poll |
| `web_map.py` | `/map` client |
| `web_extract.py` | `/extract` client |
| `web_search.py` | **rewired** to SearXNG via `/search`; DDG-regex kept as emergency fallback |

- `web_fetch.py` and `scrape_page.py` kept as thin compat shims that call
  `/scrape` with their historical output shapes (`core/base_agent.py` has a
  `web_fetch()` helper method and agent prompts reference both names).
- Tool-server `sam/scrape-web` and `sam/market-research` re-pointed at :8100.
- `scaffold.yaml` pinned_core: add `web_scrape`; keep `web_search`. Others
  FTS-retrieved. Rebuild: `python -m core.skill_registry --build`.

## Failure handling & ops

- Every Playwright op wrapped with timeout → structured error JSON.
- Chromium crash → engine relaunch, sessions marked dead with clear error.
- Resource caps: 4 contexts, 50-page default crawl cap, per-domain delay.
- Health: `GET /health`; wired into the existing baza watchdog timer.
- Dashboard UI: out of scope v1 (jobs inspectable via API/sqlite); candidate
  later phase.

## Testing

- TDD. Unit: page_to_md conversion, crawl frontier + include/exclude,
  robots handling, gate heuristics, extract with mocked LLM, session reaper.
- Live smoke script: scrape a JS-heavy page, search via SearXNG, 5-page
  crawl, one interactive session walk (goto → read → click by index).

## Out of scope (v1)

- Dashboard tab for browser jobs/sessions.
- Phantom-side second browser worker (revisit if baza load demands).
- Proxy rotation / anti-bot evasion beyond a normal desktop UA.
- Email engine (`inbox-zero`) in Specter's config — untouched.
