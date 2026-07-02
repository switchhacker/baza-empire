# Cron Improvements — Design Spec (items 1–26)

**Date:** 2026-07-02
**Approved by:** Serge ("build all up to 26")
**Scope:** Four workstreams over the agent framework's scheduled-job system:
A) Weather Watch for active jobsites, B) cron reliability, C) Telegram volume/signal, D) six new business-value crons.

Recon inventory that motivated this spec: 14 agent crons (declared in `config/agents.yaml`,
synced to user crontab by `scripts/sync-agent-crons.py`) + 12 systemd timers. Existing
weather skill (`skills/shared/weather.py`, wttr.in) is hardcoded to Philadelphia in Simon's
briefing. `ahb_projects` already has `address`, `latitude`, `longitude`, `geocoded_at`
(18/43 geocoded; the one In-Progress project has an empty address).

## Hard constraints (house rules)

- **Local-first:** all LLM calls via local Ollama (`cron_helpers.ollama_generate`). External
  HTTP is allowed only for *data* that cannot exist locally (weather, DNS, routing, BLS
  indices) — same class as today's wttr.in. Free, keyless, government/open APIs preferred.
- **No manual git commits** — `claw-auto-git` owns the repo.
- **All outbound Telegram through `core/telegram_fmt.py`** (`post_html` /
  `cron_helpers.send_telegram`). Never re-add markdown stripping; escape interpolations.
- **TDD** — each task lands with pytest tests under `tests/`; mock all external HTTP.
- **Receipt totals / invoice amounts are read-only** to every new cron.
- **Dashboard runs as `switchhacker`, debug=False** — template edits need a dashboard
  restart; privileged ops use `sudo -n` and degrade gracefully when not permitted.

## Data layer

New infra DB `dashboard/cron_health.db` (keep business data out): tables
- `cron_runs(id, cron_name, started_at, finished_at, status TEXT ok|error|timeout, duration_s REAL, error TEXT, host TEXT)`
- `report_hashes(cron_name PRIMARY KEY, last_hash, last_sent_at)` — delta-only sends
- `fyi_queue(id, cron_name, priority, message, created_at, release_after, consumed_at)` — quiet-hours + FYI batching
- `cron_alert_state(id, key UNIQUE, first_seen, last_seen, acked_at, snoozed_until, meta JSON)` — generic alert dedup/ack/snooze (used by weather + watchdog + new crons)

In `dashboard/baza_projects.db` (business-adjacent):
- `weather_observations(id, project_id, obs_date, lat, lon, temp_high_f, temp_low_f, precip_in, wind_max_mph, gust_max_mph, conditions, source, created_at)` — rain-day ledger, one row per active site per day
- `material_price_points(id, series_id, series_label, period, value, fetched_at)` — BLS PPI snapshots
- `ahb_projects.weather_profile TEXT` (new column, JSON: `{"exterior": bool, "trades": [...], "rules": [...]}`) — idempotent `ALTER TABLE` migration

Schema init follows the `core/claw_review_db.py` precedent: a `core/cron_health_db.py`
module with `init()`, WAL mode, 5s timeout.

## A) Weather Watch (items 1–8)

**`core/weather_sources.py`** — pure client module, no LLM:
- `nws_forecast(lat, lon)` → hourly + daily via api.weather.gov (points → gridpoint), and
  `nws_alerts(lat, lon)` → active official watches/warnings. Primary source.
- `open_meteo_forecast(lat, lon)` → fallback (hourly temp/precip prob/wind/gusts, 7 days).
- `wttr_current(location)` — last-resort wrapper around the existing skill's source.
- Normalized dict shape shared by all sources; 10s timeouts; `User-Agent: baza-empire (contactahbco@gmail.com)` (NWS requires one).

**Thresholds** (`core/weather_rules.py`, pure functions, unit-tested):
- Heat: heat-index ≥ 90°F → warn; ≥ 103°F → strong warn (OSHA-aligned tiers, computed via NWS heat index formula).
- Rain: work-hours (7:00–18:00 local) precip probability ≥ 50% or QPF ≥ 0.1 in.
- Wind: sustained ≥ 20 mph or gusts ≥ 35 mph.
- Cold: low < 40°F (concrete/masonry curing), < 50°F (paint/adhesives) — only when profile has those trades.
- Secondary: lightning mention in NWS alert, AQI-type NWS air-quality alerts, snow/ice.
- Rule evaluation takes a `weather_profile` so interior-only jobs skip rain/wind/cold rules.

**Trade profile classifier** — one local-LLM call (qwen2.5:14b) per project, triggered when
`weather_profile` is NULL or `scope/description` changed; result cached in the column.
Prompt: classify scope text → exterior?, trades (roofing/concrete/paint/framing/interior/...).
Fallback when LLM unavailable: `{"exterior": true}` (conservative — more alerts, never fewer).

**`agents/duke_harmon/crons/weather_watch.py`** — every 2h (`0 7-19/2 * * *` working hours
+ one 5:00 run for the day-ahead; NWS alert check runs every cycle):
1. Select projects `status='In Progress'` OR `start_date` within 7 days.
2. Projects missing lat/lon: try geocode; still missing → one dedup'd nag alert per project per 3 days ("Project X has no address — weather watch can't cover it").
3. Fetch forecast + NWS alerts per site (dedupe sites by rounded lat/lon so two jobs on one street share a fetch).
4. Evaluate rules against profile → candidate alerts keyed `weather:<project_id>:<hazard>:<date>`.
5. Dedup via `cron_alert_state` (one Telegram per key; respect snooze/ack). NWS official warnings are always `alert` priority; threshold-derived ones are `alert` if within 24h, else `fyi`.
6. "All clear" message when a previously-alerted NWS warning expires.
7. Append/update today's `weather_observations` row per site (rain-day ledger).

**7-day lookahead** — part of Duke's daily 7:00 deadline_enforcer output (existing cron):
per active site a compact week table + suggestions ("Best pour days: Tue/Wed; Fri 90% rain")
cross-referenced with `start_date`/`end_date`.

**Rain-day ledger report** — `skills/shared/weather_history.py` skill: given project_id +
date range, produce the documented weather-delay summary (for Phil/client disputes).

**Simon briefing fix** — `briefing_cron.py` weather section iterates distinct active-site
locations (fallback Philadelphia when none), reusing `weather_sources`.

**Geocoding (item 8)** — `core/geocode.py`: Nominatim (1 req/s, proper UA, cached results
into `latitude/longitude/geocoded_at`). Hook into the dashboard project save/update route
(auto-geocode when address set and lat/lon empty). Backfill script
`scripts/backfill_geocode.py` for the 22 non-geocoded rows.

## B) Cron reliability (items 9–15)

**Heartbeat registry (9)** — `cron_helpers.cron_run(name)` context manager: writes a
`cron_runs` row at start, finalizes status/duration on exit (catches + records exceptions,
re-raises). All 14 existing cron scripts + all new ones wrap their `main()` in it (mechanical
edit). Registry doubles as the data source for the dashboard panel.

**Cron watchdog (9)** — extend the existing watchdog path with
`scripts/cron_watchdog.py` (runs every 30 min via the managed schedule): for each cron
declared in `agents.yaml`, compute expected-last-run from its cron expression (croniter or a
small parser); alert (dedup'd via `cron_alert_state`, cooldown 6h) when a cron missed 2
consecutive schedules or has a 3-error streak in `cron_runs`.

**Dashboard Crons panel (10)** — read-only route `/crons` + nav link: table of every
declared cron (name, agent, schedule, last run, status, duration, error tail, log link) from
`agents.yaml` + `cron_runs`, plus systemd timers via `systemctl list-timers 'baza-*' --output=json`.
Body-level modals rule applies. Dashboard restart required after template add.

**Log rotation (11)** — user-level: `configs/logrotate-baza.conf` (logs/*.log, weekly, rotate 8,
compress, copytruncate) run by a daily managed cron `scripts/rotate_logs.sh` using
`/usr/sbin/logrotate --state logs/.logrotate.state` — no root needed.

**Timeouts (12)** — `sync-agent-crons.py` prepends `timeout <n>m` to generated crontab
commands; per-task optional `timeout_min` in agents.yaml (default 30).

**systemd migration (14)** — `sync-agent-crons.py --target systemd` generates **user** units
`~/.config/systemd/user/baza-cron-<name>.{timer,service}` with `Persistent=true`,
`RuntimeMaxSec`, and `OnFailure=baza-cron-alert@%n.service` (alert unit sends dedup'd
Telegram via a tiny script). Crontab entries are removed by sync (`--apply`) only after the
timers are enabled and verified; `--target crontab` remains available as rollback. Deploy
task does the cutover and verifies next-elapse for every timer.

**Drift check (15)** — daily managed cron runs `sync-agent-crons.py --check`; non-zero →
dedup'd Telegram alert.

## C) Telegram volume & signal (items 16–20)

**Routing layer** — new `cron_helpers.send_report(cron_name, message, priority='fyi'|'alert', delta_key=None)`:
- `delta_key` set → hash body (normalized: dates/numbers kept), compare with `report_hashes`;
  unchanged → suppress (log only). Force-send at most every 72h so reports can't vanish forever.
- `priority='alert'` → immediate `send_telegram`.
- `priority='fyi'` → immediate during day hours; during **quiet hours** (default 21:00–06:30,
  env `BAZA_QUIET_HOURS`) enqueue in `fyi_queue` instead.
- Existing crons switch narrative/dump sends to `send_report` with sensible priorities
  (client_pulse, brand_monitor, market_watch, code_review → fyi + delta; infra_health,
  deadline_enforcer, financial_review → alert path preserved). "All quiet" messages become
  suppressed-by-delta.

**Morning flush + briefing reuse (20)** — Simon's briefing consumes and marks-consumed
pending `fyi_queue` rows (compact "overnight FYIs" section) and reads the latest artifacts
from Duke/Phil/Claw (by mtime, < 12h old) instead of recomputing their DB queries; falls
back to current behavior when artifacts are stale.

**Inline buttons (19)** — alerts sent via a new `send_alert(...)` include an inline keyboard:
`✓ Ack` / `😴 Snooze 24h` / `➕ Task`, callback data `cron|<action>|<alert_state_id>`
(< 64 bytes). `core/base_agent.py` gains a CallbackQueryHandler (or raw-update handling,
matching however BaseAgent polls) that updates `cron_alert_state` (ack/snooze) or inserts a
row into the `tasks` table, then edits the message to show the outcome. Only BaseAgent bots
handle callbacks; legacy claw/phil agents simply don't get buttons (documented limitation).

## D) New crons (items 21–26)

All follow the existing pattern: script in `agents/<agent>/crons/`, declared in
`agents.yaml` `scheduled_tasks`, log file, `cron_run()` wrapper, `send_report()` output,
artifact via `save_artifact()` where a full report exists.

- **21 backup_verify (Claw, weekly Sun 04:30):** newest backup under
  `/mnt/empirepool/backups/baza-empire` — age < 26h, manifest/size sanity, test-restore the
  SQLite dump to tmpdir + `PRAGMA integrity_check`, verify Postgres dump header. Failure → alert.
- **22 zfs_health (Claw, weekly Sun 05:00):** `zpool status -x` + capacity + last scrub age
  (> 45d → warn); `sudo -n smartctl -H` per pool disk, degrade to "smart unavailable" when
  sudo denied. Anomaly → alert; healthy → delta-suppressed fyi.
- **23 invoice_followup (Phil, daily 08:30):** overdue invoices (`ahb_invoices`, status
  unpaid past due date, respecting the finance-decoupling rule: read-only) → local-LLM
  drafts a polite client reminder email → delivered through `skills/shared/suggest_action.py`
  approval flow (one per invoice per 7 days, dedup'd). Never auto-sends.
- **24 material_prices (Scout, weekly Mon 06:30):** BLS public API v1 (keyless) PPI series —
  softwood lumber WPU0811, gypsum WPU137, ready-mix concrete WPU1332, steel mill WPU1017,
  copper wire WPU10250105, #2 diesel WPU057303 — store in `material_price_points`, alert on
  >5% month-over-month move, else delta-suppressed fyi trend table.
- **25 dns_cert_watch (Claw, every 2h):** for `ahb123.com`, `nova.ahb123.com`,
  `baza.ahb123.com`: resolve A records (public resolver) vs expectations (nova → current WAN
  IP via existing ddns logic), TLS cert expiry via `ssl.get_server_certificate` (< 14d →
  alert), HTTP 200 probe on nova. Drift → alert (dedup'd).
- **26 drive_context (Duke, weekdays 06:15, folded into morning output):** OSRM public
  server (`router.project-osrm.org`) driving ETA from `BAZA_HOME_ADDRESS` (env; geocoded
  once) to each active site; no-traffic baseline noted as such; unset home address → one-time
  setup nag. Merged into the same morning message as the 7-day weather lookahead.

## Error handling & testing

- Every external fetch: timeout, try/except, structured "source unavailable" degradation —
  a dead API never kills the whole cron run, and `cron_runs` still records ok-with-warnings.
- Tests mock HTTP (responses/monkeypatch) — no live API calls in CI; rule engine, hashing,
  quiet-hours windowing, croniter-expectation math, and alert-state transitions are pure and
  fully unit-tested. Target: each task ships green scoped tests + full-suite no-new-failures.

## Deploy & verification

1. `python -m core.cron_health_db` init; `ALTER TABLE` migrations idempotent.
2. agents.yaml additions → `sync-agent-crons.py --apply` (crontab first), then the systemd
   cutover task with per-timer verification, then drift-check green.
3. Restart `baza-dashboard` (template cache) and affected agent services.
4. Live smoke: run weather_watch + dns_cert_watch once by hand, confirm Telegram output,
   confirm `cron_runs` rows, confirm /crons panel renders.

## Out of scope

Specter/phantom rsync (phase 2 of telegram rich-text), retail SKU scraping (BLS indices
instead), live traffic data (no free/local source), PagerDuty/Slack (Telegram is the alert
channel here).
