# Agent Skill Scaffold — Design Spec

**Date:** 2026-06-25
**Author:** Claude (with Serge)
**Status:** Approved for planning
**Project:** 1 of 2 (scaffold first, then AHB123 coverage)

---

## Problem

The framework has **270 skills** (242 shared + 28 per-agent) and **~100 tool-server HTTP
endpoints**, but agents can only reach a tiny fraction of them. Skills are advertised to the
LLM **statically** — each agent's `system_prompt` in `config/agents.yaml` hard-codes a short
list (`##SKILL:web_search…##`, `##SKILL:artifact_save…##`, ~5–10 skills). There is **no dynamic
discovery, no manifest the model reads, and no selection/routing step**. The other ~260 skills
and ~100 tools are invokable in theory but invisible in practice.

Consequence: the felt capability gap ("agents can't do what I can do with mouse + keyboard") is
**~80% a discovery/scaffold problem, ~20% a coverage problem**. Adding more skills onto the
current design makes it worse — more skills nobody can find. The scaffold must be fixed first.

### Current request→skill→tool flow (as built)
- `core/base_agent.py:build_system_prompt()` assembles context + persona + a **hard-coded** skill
  doc block (`base_agent.py:153-231`). Interactive agents do a **single LLM shot**.
- `core/skills_engine.py` parses `##SKILL:name{json}##` markers (regex `skills_engine.py:14`),
  runs each skill file as a subprocess with args via `SKILL_ARGS` env var, splices results back as
  `[SKILL RESULT: …]`. Supports chaining multiple markers; 90s default / 600s image timeout.
- `core/task_runner.py:190-254` does a **two-pass reground** (run skills → re-prompt LLM with real
  data) and iterates up to 3 times for autonomous tasks only.
- `core/tool_client.py` (`ToolClient.call(agent, tool, input)`) is the agent-side wrapper for the
  ~100 tool-server endpoints (`tools/server.py` + mounted `sam_imaging.py`, `edge_routes.py`,
  `nova_router.py`, `gate/routes.py`). **Tools and skills are two separate mechanisms today.**
- `skills/shared/skill_catalog.py` can discover skill files but is **not used at runtime** to shape
  prompts. PostgreSQL has an `agent_skills` table (largely unused for selection).

## Goals

1. Make **all 270 skills + ~100 tools discoverable** and reliably invocable by any agent.
2. Add a **plan → act → observe → finish** loop so agents complete multi-step tasks with tools,
   not just answer in one shot.
3. **Unify** skill and tool invocation behind the single `##SKILL##` path.
4. Ship **additively, behind a flag**, with the old behavior intact as fallback — safe for 9 live
   production bots and Claw's continuous reviewer.

## Non-Goals (this project)

- Building the ~125 missing AHB123 skills (that is **Project 2**, separate spec).
- Enriching metadata headers on the existing 270 skills (left on auto-described fallback; enriched
  opportunistically, YAGNI).
- Changing Specter's phantom OpenClaw runtime (separate runtime; not BaseAgent).
- Embedding/RAG retrieval (decided against — keyword/FTS is local, deterministic, zero model dep).

## Decisions (locked with Serge)

| Decision | Choice |
|---|---|
| Sequencing | Scaffold first, then AHB123 coverage |
| Selection mechanism | Category manifest + keyword/FTS retrieval (local, no embeddings) |
| AHB123 skill granularity (Project 2) | Grouped category skills with an `action` arg |
| Execution loop | Full plan→act→observe→finish, bounded by `max_steps` + timeout |
| Rollout | Additive / non-breaking, behind a config flag, static lists kept as fallback |
| 270-skill header backfill | Not now — leave on auto-described fallback |

---

## Architecture

Five units, all additive, gated by `config/scaffold.yaml → enabled` (per-agent overridable). Flag
off ⇒ every agent behaves exactly as today.

```
request
  │
  ▼
skill_selector ──reads── skill_registry (skills_manifest.json + SQLite FTS5)
  │   selects: pinned core skills + agent role-pins + top-K retrieved + category index
  ▼
agent_loop  ── plan → emit ##SKILL## → SkillsEngine runs → observe results → repeat ──▶ FINAL
  │                                          │
  │                                          └── call_tool skill → tool_client → tool-server (~100 endpoints)
  ▼
answer / artifact
```

### Unit 1 — `core/skill_registry.py` (build + query the manifest)

**Responsibility:** turn skill files + tool-server endpoints into a searchable manifest.

- **Metadata convention:** a skill file may declare a module-level literal:
  ```python
  SKILL_META = {
      "category": "financial",
      "summary": "Calculate an invoice total from line items.",
      "when_to_use": "User asks to total/price an invoice or quote.",
      "args": {"line_items": "list of {desc, qty, unit_price}", "tax_rate": "float, optional"},
  }
  ```
  Parsed **statically with `ast.literal_eval`** on the assigned node — **never executed** (skills
  run as subprocess; importing them is unsafe). 
- **Legacy fallback:** skills without `SKILL_META` are auto-described from filename + first
  docstring/comment line + category inferred from name prefix (e.g. `invoice_*`→financial,
  `*_calculator`→materials). All 270 usable immediately; enrich later.
- **Tool ingestion:** ingest the tool-server registry (`GET /tools`) and the mounted routers so the
  ~100 endpoints appear as `type:"tool"` manifest entries (with their `agent/tool` path).
- **Outputs:**
  - `dashboard/skills_manifest.json` — full descriptors (`name, type, category, summary,
    when_to_use, args, source_path`).
  - SQLite FTS5 table `skills_fts` (in a dedicated `dashboard/skills_manifest.db`, **separate** from
    `baza_projects.db` and `claw_reviews.db`) over `name + summary + when_to_use + category`.
- **CLI:** `python -m core.skill_registry --build` regenerates both. Kept fresh either by the
  existing scaffold-runner (30s timer) detecting skill-dir mtime changes, or a small inotify hook.
- **Query API:** `search(query, top_k, agent_id=None) -> list[descriptor]`, `categories() ->
  list[str]`, `get(name) -> descriptor`.
- **Excluded from indexing** (same spirit as Claw's exclusions): `__pycache__`, `*.pyc`, test
  fixtures, the registry's own files.

### Unit 2 — `core/skill_selector.py` (retrieval per request)

**Responsibility:** given a request, decide which skills/tools to put in front of the LLM.

- `select(message, agent_id) -> SelectionResult` returns:
  - **pinned core** — always-on (`artifact_save`, `web_search`, `ahb123_query`, `skill_search`,
    `call_tool`, …), defined in `scaffold.yaml`.
  - **role pins** — the agent's existing `agents.yaml` skill list (nothing it relies on today
    disappears).
  - **retrieved** — top-`retrieval_top_k` FTS matches for `message`, grouped by category.
  - **category index** — compact list of all categories with counts, e.g. *"You also have skills in:
    Financial(29), Materials(22), Marketing(7)… — call `skill_search{"query":…}` for more."*
- Renders to a compact prompt block injected by `base_agent.build_system_prompt()` (and the
  task_runner prompt) **when the flag is on**. When off, the old hard-coded block is used.
- **Meta-skill `skill_search`** (new shared skill): `skill_search{"query":…,"top_k":…}` returns
  matching skill descriptors from the registry so an agent can pull more skills **mid-loop** — the
  self-discovery unlock that makes the full 270 reachable without bloating any single prompt.

### Unit 3 — `core/agent_loop.py` (plan→act→observe→finish)

**Responsibility:** generalize the existing two-pass reground into a bounded N-step loop.

- Steps:
  1. Build prompt (persona + context + selected skills) and ask the LLM for a **short plan** plus
     any first `##SKILL##` calls.
  2. Run skills via `SkillsEngine` (reused as-is); splice `[SKILL RESULT: …]`.
  3. **Observe:** re-prompt the LLM with the spliced real data ("do not invent values").
  4. The LLM either emits more `##SKILL##` calls (→ back to step 2) or a terminal
     `FINAL:` / `TASK_COMPLETE` marker (→ done).
  5. Hard stops: `max_steps` (default 6) and a wall-clock timeout → return best-so-far with a
     truncation note.
- Reuses `SkillsEngine`, `gpu_pool` (`chat_stream_pooled`), and the existing reground prompt text.
  Not a rewrite — a generalization of `task_runner._run_skills_and_reformat` to N iterations.
- Both `base_agent` (interactive) and `task_runner` (autonomous) route through `agent_loop` when the
  flag is on; off ⇒ their current single-shot / 2-pass paths run unchanged.

### Unit 4 — `skills/shared/call_tool.py` (skill↔tool bridge)

**Responsibility:** make every tool-server endpoint reachable through the `##SKILL##` path.

- `call_tool{"agent":"sam_axe","tool":"generate-image","input":{…}}` → proxies through
  `core/tool_client.py` → POSTs the tool-server endpoint → prints the JSON result to stdout (so it
  splices back like any skill).
- Net effect: **one invocation path reaches every skill *and* every tool**, all discoverable via the
  registry. Serves "a proper tool for every single task / enact the proper tools."
- Carries a `SKILL_META` header so it shows up in retrieval; validates `agent`/`tool` against the
  registry's `type:"tool"` entries before calling.

### Unit 5 — `config/scaffold.yaml` + integration + tests

- **Config:**
  ```yaml
  scaffold:
    enabled: false          # master switch; flag-off = today's behavior
    max_steps: 6
    retrieval_top_k: 8
    pinned_core: [artifact_save, web_search, ahb123_query, skill_search, call_tool]
    per_agent:              # optional overrides
      claw_batto: { enabled: true }
  ```
- **Integration points (thin, additive):**
  - `base_agent.build_system_prompt()` — if flag on, swap the hard-coded skill block for the
    selector's block; else unchanged.
  - `base_agent` chat entry + `task_runner.run_task_with_llm` — if flag on, delegate to `agent_loop`;
    else unchanged.
- **Static `agents.yaml` lists stay** — used as role-pins (flag on) and as the full fallback block
  (flag off).

---

## Data flow

1. **Build time:** `skill_registry --build` scans `skills/shared/*`, `agents/*/skills/*`, and the
   tool-server registry → `skills_manifest.json` + `skills_manifest.db` (FTS5).
2. **Request time:** `skill_selector.select(message, agent_id)` → FTS query against the manifest →
   pinned + role-pins + top-K + category index → injected into the prompt.
3. **Execution:** `agent_loop` runs plan→act→observe until FINAL / max_steps; `##SKILL##` markers hit
   `SkillsEngine`; `call_tool` markers reach the tool-server via `tool_client`.
4. **Mid-loop discovery:** the LLM can emit `skill_search` to pull more skills from the registry.

## Error handling

- Registry build: a malformed `SKILL_META` (un-`literal_eval`-able) logs a warning and falls back to
  auto-description for that file — never aborts the build.
- Selector: empty FTS result ⇒ return pinned + role-pins + category index only (agent still works).
- Loop: a skill error splices `[SKILL ERROR: …]` (existing behavior); the LLM sees it and can adapt.
  `max_steps`/timeout ⇒ return best-so-far with an explicit truncation note (never hang).
- `call_tool`: unknown `agent/tool`, tool-server down, or non-2xx ⇒ structured error to stdout so the
  loop can react; never crashes the subprocess.
- Flag off path must be **behavior-identical** to today (explicit regression test).

## Testing (TDD — project standard, 589 existing tests)

- **registry:** parses `SKILL_META` via `ast`; legacy fallback produces a usable descriptor;
  malformed META degrades gracefully; tool ingestion yields `type:"tool"` entries; FTS returns
  expected hits for sample queries.
- **selector:** returns pinned + role-pins + top-K for representative messages; empty-result path;
  a **non-pinned** skill is selected for a matching query (the core proof).
- **agent_loop:** terminates on `FINAL`; terminates at `max_steps`; observes spliced results between
  steps; respects timeout.
- **call_tool:** reaches a mocked tool endpoint and returns its JSON; handles unknown tool + server
  error.
- **regression:** flag-off path identical to current single-shot / 2-pass output (guards live bots).
- **integration:** end-to-end request with flag on selects and runs a skill that is **not** in the
  agent's static `agents.yaml` list.

## Rollout

1. Land all units with `enabled: false` (zero behavior change; full test suite green).
2. Enable for one pilot agent via `per_agent` (Claw or Phil) — observe in live use + Claw review.
3. Roll to the remaining BaseAgent agents; flip master `enabled: true`.
4. Static `agents.yaml` lists remain as fallback/pins throughout; instant rollback = flip flag.

## File manifest

| File | New/changed | Purpose |
|---|---|---|
| `core/skill_registry.py` | new | Build/query manifest (skills + tools); `--build` CLI |
| `core/skill_selector.py` | new | Per-request skill/tool selection |
| `core/agent_loop.py` | new | Bounded plan→act→observe→finish loop |
| `skills/shared/call_tool.py` | new | Bridge `##SKILL##` → tool-server via `tool_client` |
| `skills/shared/skill_search.py` | new | Mid-loop registry query meta-skill |
| `config/scaffold.yaml` | new | Flag + loop/retrieval config |
| `core/base_agent.py` | changed (thin) | Flag-gated: selector block + delegate to agent_loop |
| `core/task_runner.py` | changed (thin) | Flag-gated: delegate to agent_loop |
| `dashboard/skills_manifest.json` / `.db` | generated | Manifest + FTS index (gitignored; add to Claw review exclusions like `claw_reviews.db`) |
| `tests/test_skill_registry.py` etc. | new | TDD coverage per unit |

## Open items deferred to Project 2

- The ~15–20 grouped AHB123 action-arg skills, each shipping a `SKILL_META` header (so they are
  discoverable on day one via this scaffold).
- Opportunistic `SKILL_META` backfill on high-value existing skills as we touch them.
