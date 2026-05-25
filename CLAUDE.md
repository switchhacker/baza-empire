# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

Baza Empire Agent Framework v3 is a multi-agent autonomous system for business operations (All Home Building Co LLC) plus family cloud, edge IoT, and automation. 9 AI agents (Simon, Claw, Phil, Sam, Rex, Duke, Scout, Nova, Specter) run as Telegram bots powered by local Ollama LLMs (Specter uses Ollama cloud models), sharing persistent PostgreSQL memory, a skill execution system, and a Flask dashboard.

Specter Voss is Serge's general / super-agent with sudo access to every tool, skill, and dataset across the empire. He runs on the phantom NUC via a separate **OpenClaw** runtime (not BaseAgent), with confirm-before-act as a hard rule — silence is not consent.

## Running the System

```bash
# Activate virtual environment first
source venv/bin/activate

# Launch all agents
python main.py

# Launch specific agents
python main.py claw simon

# Run the dashboard (port 8888)
cd dashboard && python app.py

# Run autonomous task runner
python core/task_runner.py
python core/task_runner.py --agent claw_batto
python core/task_runner.py --dry-run

# Systemd services
systemctl start baza-task-runner.timer
systemctl status baza-agents.service
```

## Background Reviewer (Claw Batto, always-on)

**Heads up: anything you save in this tree gets reviewed.** Two user systemd units watch this repo:

- `baza-claw-review.service` — `scripts/claw_continuous_review.py`. Four cadences: fast 60s (systemd/processes), medium 5m (git commits → LLM diff review), slow 15m (journal errors + FIXME scan), hourly (digest to `~/Desktop/baza-session-log.md` + infra-delta snapshot).
- `baza-claw-fs-watcher.service` — `scripts/claw_fs_watcher.py`. inotify per-file review on save (8s debounce, 12s LLM floor, file truncated >800 lines).

Findings: `dashboard/claw_reviews.db` (SQLite — separate from `baza_projects.db`). Severity ∈ `{info,warn,bug,regression,security}`; free-form labels (`security`, `regression-risk`, `complexity-high`, `commit-review`, `fs-event`, `infra-map`, …).

```bash
venv/bin/python -c "from core.claw_review_db import recent; [print(r['severity'], r['target'], r['title']) for r in recent(20)]"
journalctl --user -u baza-claw-review.service -u baza-claw-fs-watcher.service -f
```

Full subsystem map: `~/.claude/projects/-home-switchhacker/memory/project_claw_continuous_review.md`.

## Required Services

All must be running before agents start:
- **PostgreSQL** `localhost:5432` db=`baza_agents` — context, memory, skills, task journal
- **Ollama AMD (primary)** `localhost:11434` — AMD RX 6700 XT (Vulkan) — `ollama.service`
- **Ollama CPU** `localhost:11436` — big-model fallback — `ollama-cpu.service`
- **Ollama AMD (secondary)** `localhost:11437` — AMD Vulkan — `ollama-amd.service`
- **Redis** `localhost:6379` — chat history
- **Stable Diffusion WebUI Forge** `baza-sd-webui.service` — NVIDIA RTX 3070 (CUDA), used by Sam for imaging

`scripts/ollama_watchdog.sh` (every 5 min via systemd timer) only kills *stuck* runners (>200% CPU, >8 min). Do NOT re-add a "duplicate serve" kill block — the 3 Ollama instances are intentional and each is owned by its own systemd unit. A prior zombie-kill block broke briefings (fixed in cb0004a).

Key env vars: `TELEGRAM_SIMON_BATELY`, `TELEGRAM_CLAW_BATTO`, `TELEGRAM_PHIL_HASS`, `TELEGRAM_SAM_AXE`, `DB_PASSWORD`

## Architecture

### Agent Hierarchy

```
main.py
 └── per-agent class (agents/[name]/agent.py)
      └── BaseAgent (core/base_agent.py)
           ├── ContextMixin (core/context_mixin.py) ← memory, skills, journal
           ├── SkillsEngine (core/skills_engine.py) ← parse ##SKILL:## blocks
           ├── OllamaClient (core/ollama_client.py) ← GPU pool inference
           └── TaskUpdater (core/task_updater.py)   ← SQLite task queue
```

### Two Agent Architectures

There are **two parallel agent systems**:
1. **`core/base_agent.py`** — newer architecture used by per-agent classes in `agents/*/agent.py`. Uses `ContextMixin` for PostgreSQL memory, skill patterns, auto-summarization every 15 messages.
2. **`core/agent.py`** — legacy/active architecture with Simon's DISPATCH system, direct tool calls (mining, crypto, docker), group chat handling, and more inline features.

`config/agents.yaml` is the single source of truth for agent names, models, system prompts, and roles — **except for Specter on phantom**, which loads persona from the OpenClaw files below.

### Specter's OpenClaw Runtime (phantom-only)

Specter on phantom (NUC) does NOT run `agents/specter_voss/agent.py`. He runs `agents/specter_voss/openclaw/telegram_bridge.py`, which shells out to the `/usr/bin/openclaw` binary. OpenClaw loads its persona from:

- `agents/specter_voss/openclaw/IDENTITY.md` — who he is
- `agents/specter_voss/openclaw/MISSION.md` — mandate
- `agents/specter_voss/openclaw/SOUL.md` — personality/tone
- `agents/specter_voss/openclaw/USER.md` — who Serge is
- `agents/specter_voss/openclaw/config.yaml` — model + runtime config

When changing Specter's behavior on phantom, edit these files (NOT `agent.py` or `config/agents.yaml`). Phantom's `~/baza-empire/agent-framework-v3` was bootstrapped via scp and is not a git repo — sync via rsync/scp, then restart `baza-specter.service` on phantom.

### Skill System

Agents invoke skills by embedding this pattern in LLM responses:
```
##SKILL:skill_name{"arg": "value"}##
```
`SkillsEngine` finds the script at `agents/[agent_id]/skills/[name].py` or `skills/shared/[name].py`, runs it as a subprocess with JSON args via env var `SKILL_ARGS`, and replaces the pattern with `[SKILL RESULT: ...]`.

### GPU Pool

`core/gpu_pool.py` manages two Ollama instances as a pool with asyncio locks. Agents acquire a slot before inference and release after. If both GPUs are busy, agents wait up to 120s. Use `chat_stream_pooled()` (not `chat_stream()`) for production agents.

### Memory / Context DB

`core/context_db.py` — PostgreSQL tables:
- `agent_memory` — per-agent key/value facts (with category tags)
- `agent_summaries` — compressed session summaries (written every 15 msgs)
- `empire_knowledge` — shared facts readable by all agents
- `agent_skills` — registered skill metadata
- `task_journal` — append-only activity log for every agent action
- `agent_identity` — persona, role, system_prompt per agent (overrides yaml)

### Task Queue

`dashboard/baza_projects.db` (SQLite) holds tasks with projects, assignees, statuses, priorities, and deliverables. `core/task_runner.py` polls this, runs LLM against pending tasks, parses `TASK_COMPLETE` / `TASK_IN_PROGRESS` / `TASK_BLOCKED` signals from the LLM response, saves artifacts, and notifies Serge via Telegram.

### Simon's DISPATCH System

Simon (Co-CEO) delegates tasks using:
```
DISPATCH:claw_batto:Set up SSL on production
```
The commander module routes the instruction to the target agent's Telegram chat.

### Dashboard

Flask app at `dashboard/app.py` (port 8888). Manages agents (start/stop/restart via systemd), tasks, artifacts at `dashboard/artifacts/[project_id]/[filename]`, cron jobs, infrastructure metrics, and email pipeline. Separate SQLite DB at `dashboard/baza_projects.db`.

### Tool Server

FastAPI service at `tools/server.py` exposing agent tools as `POST /tools/{agent}/{tool}`. Runs under `baza-tool-server.service`. Sam's imaging endpoints (Stable Diffusion) are mounted via `sam_imaging.py` in the same process.

```bash
cd tools && uvicorn server:app --port 8000
```

### LiteLLM Cloud Proxy

`configs/litellm.yaml` defines a unified OpenAI-compatible API on `localhost:4000` routing to OpenAI, Anthropic, Google, xAI, Mistral, Groq, and local Ollama models. Agents that need cloud models call `http://localhost:4000/v1` with bearer `baza-litellm`. Budget cap: $50/30 days.

```bash
litellm --config configs/litellm.yaml --port 4000
```

### Email Pipeline

`email-pipeline/` — Gmail integration. `fetch_emails.py` pulls unread mail, `summarize.py` creates summaries, `send_reply.py` sends replies. State tracked in `sync_state.json`. Auth credentials live in `email-pipeline/credentials.json` and `token.json` (OAuth2). Run `scripts/gmail_auth.py` to re-authorize.

## Key Files

| File | Purpose |
|------|---------|
| `config/agents.yaml` | Agent names, models, prompts, capabilities |
| `core/base_agent.py` | Base class all new agents inherit |
| `core/agent.py` | Legacy active agent (Simon primarily uses this) |
| `core/context_db.py` | PostgreSQL schema + all DB operations |
| `core/skills_engine.py` | Skill pattern parser + subprocess executor |
| `core/gpu_pool.py` | Two-GPU async pool manager |
| `core/task_runner.py` | Autonomous background task executor |
| `dashboard/app.py` | Flask control center |
| `agents/*/agent.py` | Per-agent classes with persona overrides |
| `agents/specter_voss/openclaw/*.md` | Specter's phantom persona (OpenClaw runtime) |
| `skills/shared/` | Skills available to all agents |
| `scripts/ollama_watchdog.sh` | Kills stuck runners only — do not add "duplicate serve" logic |

## Adding a New Agent

1. Add entry to `config/agents.yaml` with `id`, `name`, `model`, `system_prompt`, `role`, `capabilities`
2. Create `agents/[agent_id]/agent.py` subclassing `BaseAgent`
3. Add to `AGENTS` dict in `main.py`
4. Set `TELEGRAM_[AGENT_NAME]` env var with bot token
5. Optionally add agent-specific skills in `agents/[agent_id]/skills/`

## Adding a New Skill

1. Create `skills/shared/[skill_name].py` (shared) or `agents/[id]/skills/[skill_name].py` (agent-specific)
2. Read args from `os.environ.get("SKILL_ARGS")` (JSON string)
3. Print result to stdout — this becomes the skill output
4. Agents invoke via `##SKILL:skill_name{"key":"val"}##` in LLM response

