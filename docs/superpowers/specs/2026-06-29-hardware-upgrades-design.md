# Hardware & Upgrades — Settings tab feature

**Date:** 2026-06-29
**Status:** Approved, building
**Surface:** Baza dashboard, Settings tab → `/settings/hardware`

## Problem

`baza` runs the whole empire: dashboard, tool-server, litellm, sd-webui, 8 local
agents, 5 Ollama instances, the ZFS pool `empirepool`, Redis, Postgres, claw
review, and ~10 timers. A hardware upgrade (e.g. the planned 5950X/5900XT CPU
swap + BIOS-5021 flash) means powering the box off. The risk is not the swap —
it's the *restart*: does everything come back, do all services start, are both
GPUs detected, is the pool mounted? Today there's no single place to plan an
upgrade and no safety net that proves the box came back healthy.

## Goal

One integrated lifecycle, surfaced from the Settings tab:

```
Research/Plan → Snapshot baseline → Shutdown runbook → (reboot) → Verify (diff vs baseline)
```

## Core idea: one probe, used twice

A single **probe module** is the source of truth for "what the system looks like
right now." It auto-discovers state (does NOT hardcode the service list) across
five domains and returns a structured snapshot. We run it twice:

1. **Baseline** — captured before you power off (the known-good state).
2. **Verify** — re-run after reboot; the result is a *diff* of the two.

The probe + verify also ship as a **standalone CLI** (`scripts/hw_verify.py`) so
you can verify even if the dashboard itself didn't come back — you are never
blind.

### Probe domains

| Domain | What it checks | How |
|--------|----------------|-----|
| services | all `baza-*` system units + `baza-claw-*` user units, active/failed | `systemctl list-units 'baza-*' --all` (+ `--user`), auto-discovered |
| ollama+gpu | `:11434–:11438` `/api/tags` reachable; NVIDIA + AMD GPUs present | HTTP GET; `nvidia-smi -L`, `vulkaninfo` |
| datastores | `empirepool` imported + mounted; Redis PING; Postgres `:5432` | `zpool list`, redis, socket connect |
| network | `baza-*` timers active; Tailscale up; `phantom` reachable | `systemctl list-timers`, `tailscale status`, `ssh phantom` |
| firmware | BIOS version, CPU model, kernel — proves a CPU/BIOS swap took | `dmidecode`, `lscpu`, `uname` (graceful if no root) |

Each check is `{name, status: ok|fail|warn|unknown, detail}`. A **regression** =
something `ok` in the baseline that is no longer `ok` after reboot. Verify PASSES
when there are zero regressions; new failures and recovered items are surfaced
too.

## Components

### Backend — `dashboard/hardware_ops.py` (new blueprint)

Registered in `app.py` near the other blueprints (try/except dual-import +
`_ensure_hardware_tables()`). Routes:

- `GET/POST /api/baza/hw/plans`, `PATCH/DELETE /api/baza/hw/plans/<id>` — upgrade plans CRUD
- `GET/POST /api/baza/hw/plans/<id>/components`, `PATCH/DELETE .../components/<cid>` — components CRUD
- `POST /api/baza/hw/baseline` (optional `plan_id`) — snapshot now
- `GET /api/baza/hw/baseline/latest` — most recent snapshot
- `POST /api/baza/hw/verify` — re-probe, diff vs latest baseline, store run
- `GET /api/baza/hw/verify/latest` — last verify result
- `GET /api/baza/hw/runbook/<plan_id>` — generated ordered shutdown sequence
- `POST /api/baza/hw/research` — opt-in agent web lookup to draft a component

### Backend — `dashboard/hardware_probe.py`

Pure-ish module. Command-running separated from parsing so the parse + diff
logic is unit-testable with injected fixtures:

- `probe_system() -> dict` — runs all sub-probes, returns full snapshot
- `parse_systemctl_units(raw) -> list` — pure
- `diff_snapshots(baseline, current) -> dict` — pure; `{regressions, recovered, summary, pass}`
- `summarize(snapshot) -> dict` — counts per domain

### Standalone — `scripts/hw_verify.py`

Imports `hardware_probe`, loads latest baseline from `baza_projects.db`, prints a
colored PASS/FAIL report. Works with the dashboard down. `--json` for machine use.

### Data — 4 tables in `baza_projects.db` (idempotent DDL, WAL)

- `hw_upgrade_plans` (id, title, status, goal, bios_req, notes, created_at, updated_at)
- `hw_components` (id, plan_id, category, name, specs, socket, tdp, est_cost, vendor, url, compat_notes, source, created_at)
- `hw_baselines` (id, plan_id, captured_at, label, snapshot_json)
- `hw_verify_runs` (id, baseline_id, ran_at, passed, regressions, result_json)

### Frontend

`templates/settings.html` gets a `🖥️ Hardware & Upgrades` section linking to a
dedicated route `/settings/hardware` (keeps the 3-card settings page lean). The
panel has three sub-views:

- **Plans** — research tracker: plans + per-plan components (category, specs,
  socket, TDP, cost, vendor, compat notes), agent-lookup button.
- **Baseline & Verify** — "Snapshot now" button; latest verify diff rendered as
  green (ok) / red (regression) / grey (recovered) rows per domain.
- **Runbook** — generated ordered stop sequence with copy-paste commands.

## Shutdown runbook ordering

Dependency-aware, generated from the live inventory:

1. Pause timers (so the 5-min watchdog doesn't restart things mid-shutdown)
2. Stop agents (`baza-agent-*`)
3. Stop app services (dashboard, tool-server, litellm, claw user units)
4. Stop sd-webui
5. Stop Ollama ×5
6. Confirm quiesced → `sudo systemctl poweroff`

ZFS is left to systemd (never manually exported). Startup is automatic via
enabled units, so verify carries the weight. Runbook links to `~/bios-staging/`
for the BIOS flash step.

## Build phases

- **Phase 1 — Probe + Baseline + Verify** (safety net; foundation everything
  reuses). Probe module + pure parse/diff with tests, CLI script, baseline/verify
  routes, minimal UI diff.
- **Phase 2 — Plans + components tracker** + agent-assisted lookup.
- **Phase 3 — Runbook generator** + polish + optional boot-time auto-verify
  oneshot unit (`baza-hw-postboot.service`).

Each phase is independently shippable; Phase 1 first.

## Constraints honored

- **Local-first:** always-on system is fully local; web lookup is opt-in per
  request only.
- **Guide + verify:** dashboard never auto-powers-off; it generates the runbook
  and you pull the trigger. The standalone CLI means a dead dashboard ≠ blind.
- **Auto-discovery:** never hardcode the service list; `specter_voss` correctly
  shows as remote (phantom), not a missing local unit.
- **No manual framework commits:** `claw-auto-git` timer handles commits.
