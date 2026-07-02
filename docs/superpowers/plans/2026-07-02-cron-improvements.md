# Cron Improvements (items 1–26) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship weather monitoring for active jobsites, cron reliability (heartbeats, watchdog, systemd migration), Telegram volume controls (delta/priority/quiet-hours/buttons), and six new business crons per spec `docs/superpowers/specs/2026-07-02-cron-improvements-design.md`.

**Architecture:** New infra DB `dashboard/cron_health.db` (module `core/cron_health_db.py`) underpins heartbeats, delta hashes, FYI queue, and alert state. Pure modules `core/weather_sources.py` / `core/weather_rules.py` / `core/geocode.py` feed a new Duke `weather_watch` cron. `agents/cron_helpers.py` grows `cron_run()`, `send_report()`, `send_alert()`. `scripts/sync-agent-crons.py` gains timeouts + `--target systemd`. New crons follow the existing `agents/<agent>/crons/` + `agents.yaml scheduled_tasks` pattern.

**Tech Stack:** Python 3 (`./venv/bin/python` ONLY), sqlite3, croniter (installed), python-telegram-bot 21.3 (BaseAgent), pytest, api.weather.gov + open-meteo.com + Nominatim + BLS v1 + OSRM (all keyless HTTP).

## Global Constraints

- **Never `git commit` or `git push`** — `claw-auto-git` owns this repo. Plan-step "Commit" = verify tests green, then STOP (no git).
- **Local-first:** LLM calls only via `cron_helpers.ollama_generate` (local Ollama). External HTTP only for data (weather/DNS/routing/BLS/geocode), 10s timeouts, `User-Agent: baza-empire/1.0 (contactahbco@gmail.com)`.
- **All Telegram sends via `core/telegram_fmt.py`** (`post_html`) / `cron_helpers` wrappers. Escape interpolated free text with `html.escape()` when hand-building HTML; markdown path is fine as-is.
- **Tests:** pytest under `tests/`, mock ALL external HTTP (`monkeypatch` on `urllib.request.urlopen` or the module's fetch fn). Run scoped tests + confirm no new failures in touched-module tests. Full suite has 3 known pre-existing failures — don't chase them.
- **Invoices/receipts read-only.** `ahb_invoices` status is Phil's signal (`status='Paid'` counting rule) — never UPDATE from new crons.
- **Dashboard** runs as `switchhacker`, `debug=False` → template changes require `sudo systemctl restart baza-dashboard`. Modals body-level. Privileged shell = `sudo -n …`, degrade gracefully on denial.
- **DB paths:** business = `dashboard/baza_projects.db` (WAL, 5s timeout, schemas drift — verify with `.schema` first); new infra DB = `dashboard/cron_health.db`.
- Each cron script: standalone `venv/bin/python` executable, imports `agents/cron_helpers.py` via the `FRAMEWORK_DIR` sys.path pattern used by every existing cron (copy the header from `agents/claw_batto/crons/infra_health.py`).
- Timestamps from `datetime` — ISO 8601 local time, matching existing code.

---

### Task 1: `core/cron_health_db.py` — infra DB (heartbeats, hashes, FYI queue, alert state)

**Files:**
- Create: `core/cron_health_db.py`
- Test: `tests/test_cron_health_db.py`

**Interfaces (Produces):**
```python
DB_PATH  # <framework>/dashboard/cron_health.db
def connect() -> sqlite3.Connection      # WAL, row_factory=Row, timeout=5
def init() -> None                        # idempotent CREATE TABLE IF NOT EXISTS ×4
def record_run_start(cron_name: str) -> int              # returns run id
def record_run_end(run_id: int, status: str, error: str | None = None) -> None
    # status ∈ {"ok","error","timeout"}; sets finished_at + duration_s
def recent_runs(limit: int = 200, cron_name: str | None = None) -> list[sqlite3.Row]
def last_runs_by_cron() -> dict[str, sqlite3.Row]         # newest run per cron_name
def delta_changed(cron_name: str, body: str, force_after_h: float = 72.0) -> bool
    # sha256 of body vs report_hashes; True (and records hash+now) when changed
    # OR last_sent_at older than force_after_h; False otherwise
def enqueue_fyi(cron_name: str, message: str, release_after: str) -> int
def pending_fyis(now_iso: str) -> list[sqlite3.Row]       # release_after <= now, consumed_at IS NULL
def mark_fyis_consumed(ids: list[int]) -> None
def should_alert(key: str, renotify_hours: float | None = None,
                 meta: dict | None = None) -> tuple[bool, int]
    # upserts cron_alert_state row for key; returns (send_now, row_id).
    # send_now False when: acked_at set, or snoozed_until > now, or
    # (renotify_hours set and last_seen within renotify_hours). Always bumps last_seen.
def alert_ack(row_id: int) -> None
def alert_snooze(row_id: int, hours: float = 24.0) -> None
def alert_get(row_id: int) -> sqlite3.Row | None
```

**Tables (exact):** per spec — `cron_runs(id INTEGER PK, cron_name TEXT, started_at TEXT, finished_at TEXT, status TEXT, duration_s REAL, error TEXT, host TEXT DEFAULT 'baza')`, `report_hashes(cron_name TEXT PRIMARY KEY, last_hash TEXT, last_sent_at TEXT)`, `fyi_queue(id INTEGER PK, cron_name TEXT, priority TEXT DEFAULT 'fyi', message TEXT, created_at TEXT, release_after TEXT, consumed_at TEXT)`, `cron_alert_state(id INTEGER PK, key TEXT UNIQUE, first_seen TEXT, last_seen TEXT, acked_at TEXT, snoozed_until TEXT, meta TEXT)`.

Support `BAZA_CRON_HEALTH_DB` env override of DB_PATH (tests point it at tmp_path). `python -m core.cron_health_db` runs `init()` (mirror `core/claw_review_db.py`).

**Steps:**
- [ ] Read `core/claw_review_db.py` for the house DB-module pattern.
- [ ] Write failing tests: `test_init_idempotent`, `test_record_run_lifecycle` (start→end sets duration/status), `test_delta_changed_first_true_repeat_false`, `test_delta_force_after` (backdate last_sent_at 73h → True), `test_fyi_enqueue_release_consume`, `test_should_alert_new_true_repeat_false_with_renotify`, `test_alert_ack_blocks`, `test_snooze_blocks_until_expiry` (backdate snoozed_until → fires again).
- [ ] Run `venv/bin/python -m pytest tests/test_cron_health_db.py -v` → FAIL (module missing).
- [ ] Implement module; run tests → PASS. No git commit (auto-git).

---

### Task 2: `core/weather_sources.py` + `core/weather_rules.py`

**Files:**
- Create: `core/weather_sources.py`, `core/weather_rules.py`
- Test: `tests/test_weather_sources.py`, `tests/test_weather_rules.py`

**Interfaces (Produces):**
```python
# weather_sources
def get_forecast(lat: float, lon: float) -> dict | None
# {"source":"nws"|"open_meteo",
#  "daily":[{"date","high_f","low_f","precip_prob_max","precip_in","wind_mph","gust_mph","conditions"} ×7],
#  "hourly":[{"ts","temp_f","rh","precip_prob","wind_mph","gust_mph"} ×48]}
def get_active_alerts(lat: float, lon: float) -> list[dict]
# [{"id","event","severity","headline","onset","ends"}] from api.weather.gov/alerts/active?point=
def _fetch_json(url: str, timeout: int = 10) -> dict | None   # single seam for tests/mocking

# weather_rules
def heat_index_f(temp_f: float, rh: float) -> float           # NWS Rothfusz regression
def default_profile() -> dict    # {"exterior": True, "trades": []}
def evaluate(forecast: dict, alerts: list[dict], profile: dict,
             work_hours: tuple[int,int] = (7,18)) -> list[dict]
# each hazard: {"key_suffix": "<hazard>:<date>", "hazard": "heat|rain|wind|cold_concrete|cold_paint|nws:<event>",
#               "severity": "alert"|"fyi", "date": "YYYY-MM-DD", "detail": "<human line>"}
```

**Threshold rules (exact, from spec):** heat-index ≥90°F fyi / ≥103°F alert (any work-hour); rain: work-hours precip_prob ≥50% or daily precip_in ≥0.1 — only if `profile["exterior"]`; wind ≥20 sustained or ≥35 gust — exterior only; cold: low <40 when `"concrete"` or `"masonry"` in trades, low <50 when `"paint"` in trades; every NWS alert → hazard severity `alert` (fyi if severity=="Minor"). Hazards for today/tomorrow → `alert`, days 2–6 → `fyi` (except NWS = always as above).

**NWS flow:** `points/{lat},{lon}` → `properties.forecastHourly` + `forecastGridData`… keep it simple: use `forecastHourly` for hourly and `forecast` for daily periods; map to normalized shape (temperature already °F; windSpeed strings like "10 to 20 mph" → take max int). Open-Meteo: `forecast?latitude&longitude&hourly=temperature_2m,relative_humidity_2m,precipitation_probability,wind_speed_10m,wind_gusts_10m&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch&timezone=America/New_York&forecast_days=7`.

**Steps:**
- [ ] Failing tests with canned JSON fixtures (inline dicts, monkeypatch `_fetch_json`): `test_nws_normalization`, `test_open_meteo_fallback_when_nws_none`, `test_alerts_parse`, `test_heat_index_known_value` (T=95,RH=60 → 114±2), `test_evaluate_heat_tiers`, `test_evaluate_rain_exterior_only` (interior profile → no rain hazard), `test_evaluate_wind_gust`, `test_evaluate_cold_requires_trade`, `test_nws_alert_always_alert`, `test_far_day_downgraded_to_fyi`.
- [ ] Run → FAIL. Implement both modules. Run → PASS. No git commit.

---

### Task 3: `core/geocode.py` + save-hook + backfill (item 8)

**Files:**
- Create: `core/geocode.py`, `scripts/backfill_geocode.py`
- Modify: `dashboard/app.py` (project create/update route — grep `ahb_projects` for the INSERT/UPDATE handlers)
- Test: `tests/test_geocode.py`

**Interfaces (Produces):**
```python
def geocode(address: str) -> tuple[float, float] | None   # Nominatim /search?format=json&limit=1, UA header, None on any failure
def ensure_project_coords(conn, project_id: str) -> tuple[float, float] | None
# reads ahb_projects row; if lat/lon present return them; else geocode COALESCE(address, location);
# on success UPDATE latitude, longitude, geocoded_at=datetime('now') and return; else None
```

**Steps:**
- [ ] Failing tests: `test_geocode_parses_first_result`, `test_geocode_none_on_error`, `test_ensure_coords_cached_no_fetch` (row already geocoded → geocode never called), `test_ensure_coords_updates_row` (tmp sqlite with `ahb_projects` DDL copied from the real `.schema`).
- [ ] Implement; PASS.
- [ ] Dashboard hook: in the project save/update route(s), after successful write, if address present and latitude NULL → `try: ensure_project_coords(...) except Exception: pass` (never block a save on geocoding). Add `test_geocode.py::test_hook_never_raises`.
- [ ] `scripts/backfill_geocode.py`: iterate rows with address and NULL lat, `ensure_project_coords`, 1.1s sleep between calls (Nominatim rate rule), print summary. Don't run it here (deploy task does).

---

### Task 4: `cron_helpers` — `cron_run()`, `send_report()`, `send_alert()` (items 9, 16, 17, 18)

**Files:**
- Modify: `agents/cron_helpers.py`
- Test: `tests/test_cron_helpers_routing.py`

**Interfaces (Consumes):** Task 1 functions. **Produces:**
```python
@contextmanager
def cron_run(name: str):
    # record_run_start; yield; record_run_end ok — on exception record error (str(e)[:500]) and RE-RAISE.
    # cron_health_db failures must never break the cron (try/except around registry calls).

def in_quiet_hours(now: datetime.datetime | None = None) -> bool
    # env BAZA_QUIET_HOURS "21:00-06:30" (default), handles midnight wrap

def send_report(cron_name: str, message: str, priority: str = "fyi",
                delta_key: str | None = None, token=None, chat_id=None) -> bool
    # delta_key and not delta_changed(delta_key, message) → log+return False
    # priority=="alert" → send_telegram now, True
    # fyi + quiet hours → enqueue_fyi(release_after = next 06:30), False
    # fyi + day → send now, True

def send_alert(cron_name: str, message: str, alert_key: str,
               renotify_hours: float | None = None, buttons: bool = True,
               token=None, chat_id=None) -> bool
    # (ok, row_id) = should_alert(alert_key, renotify_hours); not ok → False
    # sends via post_html; buttons wired in Task 17 (pass reply_markup=None until then)
```

**Steps:**
- [ ] Failing tests (monkeypatch `send_telegram`/`post_html` to a recorder; `BAZA_CRON_HEALTH_DB`→tmp): `test_cron_run_records_ok`, `test_cron_run_records_error_and_reraises`, `test_cron_run_survives_registry_failure` (monkeypatch record_run_start to raise → body still runs), `test_quiet_hours_wrap` (22:00 True, 12:00 False, 06:00 True), `test_send_report_delta_suppress`, `test_send_report_fyi_quiet_enqueues`, `test_send_report_alert_always_sends`, `test_send_alert_dedups_by_key`.
- [ ] Implement; PASS. No git commit.

---

### Task 5: weather profile classifier + `weather_watch` cron (items 1–4, 6-ledger-write, 8-nag)

**Files:**
- Create: `agents/duke_harmon/crons/weather_watch.py`, `core/weather_profile.py`
- Modify: `config/agents.yaml` (duke `scheduled_tasks` += `{enabled: true, name: weather_watch, schedule: "0 5-19/2 * * *", script: agents/duke_harmon/crons/weather_watch.py, log: logs/duke_weather.log}`)
- Test: `tests/test_weather_watch.py`

**Interfaces (Consumes):** Tasks 1–4. **Produces:**
```python
# core/weather_profile.py
def get_weather_profile(conn, project_row) -> dict
# ahb_projects.weather_profile JSON if set; else classify scope+description via
# ollama_generate("qwen2.5:14b", ...) expecting JSON {"exterior":bool,"trades":[...]};
# parse defensively; cache to column; ANY failure → default_profile()
def ensure_weather_profile_column(conn)   # idempotent ALTER TABLE ADD COLUMN weather_profile TEXT

# weather_watch.py main(now=None) — testable entry:
# 1. sites = In-Progress OR start_date within 7 days
# 2. no coords after ensure_project_coords → send_alert(key=f"weather:noaddr:{pid}", renotify_hours=72)
# 3. dedupe fetches by (round(lat,2), round(lon,2))
# 4. hazards = evaluate(forecast, alerts, profile) per project
# 5. hazard sev alert → send_alert(key=f"weather:{pid}:{key_suffix}"); fyi → collect,
#    one combined send_report(cron_name="weather_watch", delta_key="weather_watch_fyi") at end
# 6. NWS all-clear: previously alerted `nws:` keys (cron_alert_state key LIKE 'weather:%:nws:%',
#    last_seen < now-6h, unacked) whose alert id no longer active → one "all clear" send_report(priority="alert")... keep simple: message when active-alert set for a site shrinks vs state
# 7. upsert today's weather_observations row per site (ledger)
```
`weather_observations` DDL (create if missing, in this task): per spec. Message format: markdown, one line per site — `⚠️ *Heat* — 123 Main St (Deck rebuild): heat index 104°F Thu 1–4pm`.

**Steps:**
- [ ] Failing tests (tmp DBs, monkeypatch `get_forecast`/`get_active_alerts`/`ollama_generate`/send recorder): `test_profile_cached_no_llm_second_call`, `test_profile_llm_garbage_falls_back`, `test_main_alerts_on_heat_for_exterior_site`, `test_main_skips_interior_rain`, `test_main_nag_missing_address_dedup`, `test_ledger_row_upserted`, `test_site_fetch_dedup` (2 projects same coords → 1 forecast call), `test_alert_dedup_across_runs`.
- [ ] Implement; PASS. `venv/bin/python scripts/sync-agent-crons.py` dry-run shows `[+] weather_watch`. Don't `--apply` (deploy task).

---

### Task 6: rain-day ledger skill + 7-day lookahead in Duke's morning cron (items 5, 6)

**Files:**
- Create: `skills/shared/weather_history.py`
- Modify: `agents/duke_harmon/crons/deadline_enforcer.py`
- Test: `tests/test_weather_history.py`

**Interfaces (Consumes):** `weather_observations` (T5), `get_forecast` (T2). **Produces:**
- Skill (SKILL_ARGS JSON `{"project_id": "...", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`) → prints markdown table of observed weather + summary counts (rain days ≥0.1in, high-wind days, ≥90° days) — Phil's delay-documentation evidence.
- `deadline_enforcer.py` gains `build_weather_lookahead(conn) -> str` appended to the existing morning message: per active site a compact `Mon☀️ Tue🌧80% …` week line + "Best exterior days: Tue/Wed" + flags where forecast collides with `start_date`/`end_date`.

**Steps:**
- [ ] Failing tests: `test_history_table_and_counts` (seed 5 obs rows), `test_history_empty_range_message`, `test_lookahead_best_days` (canned forecast → picks the 2 lowest-precip/wind workdays), `test_lookahead_no_sites_returns_empty`.
- [ ] Implement (skill follows `skills/shared/weather.py` structure: read SKILL_ARGS, print result); PASS. Wrap `deadline_enforcer` main in `cron_run("deadline_enforcer")` while touching it.

---

### Task 7: Simon briefing — per-site weather, FYI flush, artifact reuse (items 7, 20 + flush for 16/18)

**Files:**
- Modify: `agents/simon_bately/briefing_cron.py` (weather section ~line 223; add sections)
- Test: `tests/test_briefing_weather.py`

**Interfaces (Consumes):** T2 `get_forecast`, T1 `pending_fyis`/`mark_fyis_consumed`. **Produces:** three pure helpers in `briefing_cron.py` (unit-testable without Telegram): `build_site_weather_section(conn) -> str` (distinct active-site one-liners via one `get_forecast` per dedup'd coord; fallback literal Philadelphia line when no geocoded active sites), `build_fyi_section() -> str` ("📥 Overnight FYIs" from pending_fyis, marks consumed, "" when none), `read_recent_artifact(project_dir_glob: str, max_age_h: float = 12.0) -> str | None` (newest matching file under `dashboard/artifacts/`, None if stale → caller keeps current recompute path).

**Steps:**
- [ ] Read `briefing_cron.py` fully first (it has anti-hallucination + section assembly — do not disturb).
- [ ] Failing tests for the three helpers (tmp DBs/dirs, mocked forecast).
- [ ] Implement + wire into the section dict; wrap main in `cron_run("team_briefing")`; PASS.

---

### Task 8: retrofit all existing crons — heartbeat + routed sends (items 9, 16, 17)

**Files:**
- Modify: `agents/claw_batto/crons/{infra_health,code_review,icloud_ingest}.py`, `agents/phil_hass/crons/{financial_review,compliance_check,doc_watchdog}.py`, `agents/duke_harmon/crons/project_tracker.py`, `agents/nova_sterling/crons/client_pulse.py`, `agents/rex_valor/crons/lead_followup.py`, `agents/scout_reeves/crons/{market_watch,tech_radar}.py`, `agents/sam_axe/crons/brand_monitor.py`
- Test: `tests/test_cron_retrofit.py`

**Mechanical rule per script:** wrap entry in `with cron_run("<name>"):`; convert the final Telegram send — narrative/dump reports (`client_pulse`, `brand_monitor`, `market_watch`, `code_review`, `tech_radar`, `compliance_check`) → `send_report(name, msg, priority="fyi", delta_key=name)`; action/deadline reports (`infra_health`, `financial_review`, `doc_watchdog`, `project_tracker`, `deadline_enforcer` [done in T6], `lead_followup`, `icloud_ingest`) → `send_report(name, msg, priority="alert")`. Preserve each cron's artifact/event/journal calls untouched. "All quiet"-style sends now die naturally via delta suppression.

**Steps:**
- [ ] Failing test `test_all_declared_crons_use_cron_run`: parse `config/agents.yaml` scheduled_tasks, assert each script file's source contains `cron_run(` and (`send_report(` or `send_alert(`).
- [ ] Retrofit all 12 files (each keeps its own token env — pass `token=` through unchanged where scripts use per-agent tokens).
- [ ] `venv/bin/python -m py_compile` each; scoped tests PASS; spot-run one cron manually: `venv/bin/python agents/nova_sterling/crons/client_pulse.py` → exit 0 and a `cron_runs` row.

---

### Task 9: cron watchdog + drift check + log rotation + crontab timeouts (items 9, 11, 12, 15)

**Files:**
- Create: `scripts/cron_watchdog.py`, `scripts/rotate_logs.sh`, `configs/logrotate-baza.conf`
- Modify: `scripts/sync-agent-crons.py`
- Test: `tests/test_cron_watchdog.py`, `tests/test_sync_timeouts.py`

**Interfaces (Consumes):** T1 `last_runs_by_cron`/`should_alert`, croniter. **Produces:**
```python
# cron_watchdog.py
def expected_prev_fire(schedule: str, now: datetime) -> datetime   # croniter(schedule, now).get_prev(datetime)
def find_problems(declared: list[dict], runs: dict, now) -> list[dict]
# missed: no run since the 2nd-previous scheduled fire (grace 15 min) → {"type":"missed",...}
# error_streak: last 3 runs all status!="ok" → {"type":"errors",...}
# main(): parse agents.yaml scheduled_tasks (enabled only) → problems →
#   send_alert(key=f"cronwd:{name}:{type}", renotify_hours=6)
# plus: run sync-agent-crons.py --check; rc!=0 → send_alert(key="cronwd:drift", renotify_hours=24)
```
- `sync-agent-crons.py`: generated command becomes `timeout {timeout_min}m cd … && venv/bin/python …` — read optional per-task `timeout_min` from yaml (default 30). (Note: `timeout` must wrap the python invocation: `cd <fw> && timeout 30m venv/bin/python <script> >> log 2>&1`.)
- `logrotate-baza.conf`: `<fw>/logs/*.log { weekly missingok rotate 8 compress copytruncate notifempty }`; `rotate_logs.sh`: `/usr/sbin/logrotate --state <fw>/logs/.logrotate.state <fw>/configs/logrotate-baza.conf`.
- Register both in agents.yaml under **claw_batto**: `cron_watchdog` schedule `*/30 * * * *` log `logs/claw_cronwd.log`; `rotate_logs` schedule `10 4 * * *` script `scripts/rotate_logs.sh` log `logs/rotate.log` (confirm sync script handles non-python scripts — it builds shell lines, so `bash scripts/rotate_logs.sh` via a `command:` fallback or make script executable with shebang and use path directly; adjust sync to use `script:` extension: `.sh` → `bash`).
- Watchdog must skip crons that have never run yet AND were declared <24h ago is overkill — instead: never-run crons alert only after 2 missed fires like everything else (cron_runs empty → treat last run = epoch, but suppress via `renotify_hours=6` dedup key so it nags at most 4×/day).

**Steps:**
- [ ] Failing tests: `test_expected_prev_fire_every4h`, `test_missed_two_schedules_flagged`, `test_recent_run_ok_not_flagged`, `test_error_streak_flagged`, `test_sync_line_contains_timeout` (call the sync script's line-builder on a fixture yaml), `test_sh_script_uses_bash`.
- [ ] Implement; PASS. Verify `bash scripts/rotate_logs.sh` runs clean locally (creates state file).

---

### Task 10: dashboard `/crons` panel (item 10)

**Files:**
- Create: `dashboard/templates/crons.html`
- Modify: `dashboard/app.py` (route), `dashboard/templates/_nav.html` (link under the same submenu style as Agents/Projects)
- Test: `tests/test_crons_panel.py`

**Produces:** `GET /crons` — read-only page: table 1 = declared crons (agents.yaml: agent, name, schedule, enabled) joined with `last_runs_by_cron()` (last run, status chip ✅/❌/⏱, duration, error tail ≤200 chars, log path) + next-fire via croniter; table 2 = `systemctl list-timers 'baza-*' --all --no-pager` parsed (subprocess, degrade to "unavailable"); table 3 = last 50 `cron_runs`. JSON endpoint `GET /api/crons/status` returns the same data (the page renders server-side Jinja like existing templates — standalone template including `_nav.html`).

**Steps:**
- [ ] Read one existing simple route+template pair (e.g. hardware view) for house style.
- [ ] Failing tests with Flask test client: `test_crons_page_200_lists_declared`, `test_api_status_json_shape`, `test_error_tail_escaped` (error containing `<script>` renders escaped).
- [ ] Implement; PASS. Note in report: needs `baza-dashboard` restart at deploy.

---

### Task 11: `sync-agent-crons.py --target systemd` (+ failure-alert unit) (item 14)

**Files:**
- Modify: `scripts/sync-agent-crons.py`
- Create: `scripts/cron_failure_alert.py`, `configs/systemd-user/baza-cron-alert@.service` (template unit, installed by sync)
- Test: `tests/test_sync_systemd.py`

**Produces:**
```python
def cron_to_oncalendar(expr: str) -> str
# "0 */6 * * *"→"*-*-* 00/6:00:00"; "45 */6 * * *"→"*-*-* 00/6:45:00"; "0 9 * * *"→"*-*-* 09:00:00";
# "0 7 * * 1"→"Mon *-*-* 07:00:00"; "0 6 * * 3"→"Wed *-*-* 06:00:00"; "0 5-19/2 * * *"→"*-*-* 05..19/2:00:00";
# "15 6 * * 1-5"→"Mon..Fri *-*-* 06:15:00"; "*/30 * * * *"→"*-*-* *:00/30:00"; raise ValueError on unsupported
def render_units(task: dict) -> tuple[str, str]   # (service_text, timer_text)
# service: Type=oneshot, WorkingDirectory=<fw>, ExecStart=<fw>/venv/bin/python <script> (bash for .sh),
#   StandardOutput=append:<fw>/<log>, StandardError=inherit, RuntimeMaxSec=<timeout_min*60>,
#   EnvironmentFile=<fw>/configs/secrets.env, OnFailure=baza-cron-alert@%n.service   [in [Unit]]
# timer: OnCalendar=<converted>, Persistent=true, WantedBy=timers.target
# unit name: baza-cron-<name>.{service,timer} in ~/.config/systemd/user/
```
`--target systemd --apply`: write units, `systemctl --user daemon-reload`, `enable --now` each timer, then REMOVE the managed crontab lines (leave a `# baza-empire-managed migrated-to-systemd <date>` comment). `--target systemd` (dry-run default) prints the diff. `--target crontab` remains default/rollback. `cron_failure_alert.py <unit>`: `send_alert(cron_name="systemd", key=f"unitfail:{unit}", renotify_hours=6, message="❌ <unit> failed — journalctl --user -u <unit> -n 30")`.

**Steps:**
- [ ] Failing tests: `test_oncalendar_conversions` (all 8 above + ValueError case), `test_render_units_fields` (Persistent, OnFailure, RuntimeMaxSec, append log), `test_sh_script_execstart_bash`, `test_dry_run_no_writes` (tmp HOME).
- [ ] Implement; PASS. Do NOT apply here — deploy task owns the cutover.

---

### Task 12: `backup_verify` + `zfs_health` crons (items 21, 22)

**Files:**
- Create: `agents/claw_batto/crons/backup_verify.py`, `agents/claw_batto/crons/zfs_health.py`
- Modify: `config/agents.yaml` (claw: `backup_verify` `30 4 * * 0`, log `logs/claw_backup_verify.log`; `zfs_health` `0 5 * * 0`, log `logs/claw_zfs.log`)
- Test: `tests/test_backup_verify.py`, `tests/test_zfs_health.py`

**backup_verify checks** (pure `verify(backup_root) -> list[str]` of problems + `main()`): newest dated dir/file set under `/mnt/empirepool/backups/baza-empire` — (a) mtime <26h, (b) size >50% of previous backup's, (c) copy newest `baza_projects*` sqlite dump to tmpdir → `PRAGMA integrity_check` == "ok" (if it's a `.sql.gz`/dump, gunzip -t instead), (d) pg dump header check (`head -c 200` contains "PostgreSQL database dump" for .sql / `pg_restore -l` rc 0 for custom). Problems → `send_alert(key="backup_verify:fail", renotify_hours=24)`; clean → `send_report(..., priority="fyi", delta_key="backup_verify")` one-liner. **Read `scripts/backup.sh` first to learn the real layout — do not guess filenames.**

**zfs_health checks** (`check() -> (problems, info_lines)`): `zpool status -x` != "all pools are healthy"; `zpool list -H -o name,capacity` capacity >85%; last scrub (`zpool status` scan line) >45 days; `sudo -n smartctl -H /dev/<disk>` per `zpool status -P` device — on sudo denial append "smart: unavailable (sudo)" info, not a problem. Alert/fyi routing same pattern as backup_verify.

**Steps:**
- [ ] Failing tests with monkeypatched `run_cmd`/subprocess + tmp dirs (fake backup tree, canned zpool outputs incl. DEGRADED and old-scrub fixtures).
- [ ] Implement; PASS; manual smoke `venv/bin/python agents/claw_batto/crons/backup_verify.py` against the real pool (expect clean or a genuine finding — report it, don't fix).

---

### Task 13: `dns_cert_watch` cron (item 25)

**Files:**
- Create: `agents/claw_batto/crons/dns_cert_watch.py`
- Modify: `config/agents.yaml` (claw: `dns_cert_watch`, `20 */2 * * *`, log `logs/claw_dns.log`)
- Test: `tests/test_dns_cert_watch.py`

**Checks** (each a pure function taking injected fetchers): (1) resolve A of `ahb123.com`, `nova.ahb123.com`, `baza.ahb123.com` via `socket.getaddrinfo` — empty → problem; (2) `nova.ahb123.com` A vs current WAN IP (**read the existing ddns script the route-watchdog/ddns timer uses — reuse its WAN-IP lookup**; fallback `https://api.ipify.org`) — mismatch → problem "nova DNS drift: A=x WAN=y"; (3) cert expiry via `ssl` handshake on :443 (`ssl.create_default_context().wrap_socket`, `getpeercert()["notAfter"]`) — <14 days → problem; handshake failure on nova → problem, on baza.ahb123.com → info only (CF tunnel pending); (4) `https://nova.ahb123.com` GET expecting <500. Problems → `send_alert(key=f"dnswatch:{host}:{check}", renotify_hours=6)`.

**Steps:**
- [ ] Failing tests: `test_drift_detected`, `test_cert_expiry_window`, `test_handshake_fail_nova_is_problem_baza_is_not`, `test_all_green_silent` (send recorder stays empty).
- [ ] Implement; PASS; manual smoke run (report findings — nova drift is a KNOWN live issue, expect it to fire).

---

### Task 14: `invoice_followup` cron (item 23)

**Files:**
- Create: `agents/phil_hass/crons/invoice_followup.py`
- Modify: `config/agents.yaml` (phil: `invoice_followup`, `30 8 * * *`, log `logs/phil_invoice_followup.log`)
- Test: `tests/test_invoice_followup.py`

**Behavior:** `sqlite3 dashboard/baza_projects.db '.schema ahb_invoices'` FIRST (schemas drift). Select invoices unpaid past due (status not in ('Paid','Draft','Void') AND due/date field < today — adapt to real columns) joined to project/client for name+email. Per invoice, dedup `should_alert(key=f"invfu:{invoice_id}", renotify_hours=168)` (=1 per 7 days); draft reminder via `ollama_generate("qwen2.5:14b", …)` — professional, friendly, references invoice number/amount/days overdue, NO legal threats; deliver through the existing approval flow: **read `skills/shared/suggest_action.py` and reuse its suggestion mechanism** so Serge gets an approve-to-send card rather than an auto-send. DB strictly read-only. LLM down → skip with log (never send un-drafted).

**Steps:**
- [ ] Failing tests (tmp DB with real DDL, mocked LLM + suggestion recorder): `test_selects_only_overdue_unpaid`, `test_weekly_dedup`, `test_draft_contains_invoice_facts`, `test_no_llm_no_send`, `test_never_writes_db` (open conn in ro mode after run / assert no UPDATE executed via trace hook).
- [ ] Implement; PASS.

---

### Task 15: `material_prices` cron (item 24)

**Files:**
- Create: `agents/scout_reeves/crons/material_prices.py`
- Modify: `config/agents.yaml` (scout: `material_prices`, `30 6 * * 1`, log `logs/scout_prices.log`)
- Test: `tests/test_material_prices.py`

**Behavior:** BLS v1 `POST https://api.bls.gov/publicAPI/v1/timeseries/data/` body `{"seriesid": [...], "startyear": "<Y-1>", "endyear": "<Y>"}` for series: `WPU0811` softwood lumber, `WPU137` gypsum, `WPU1332` ready-mix concrete (VERIFY series ids resolve — a wrong id returns empty data, treat as "series unavailable" info line, don't fail the run), `WPU1017` steel mill, `WPU10250105` copper wire, `WPU057303` No.2 diesel. Store rows in `material_price_points` (create table if missing, DDL per spec) keyed unique (series_id, period). Compute latest vs previous month per series: |Δ|>5% → `send_alert(key=f"matprice:{series}:{period}", renotify_hours=999999)` (period-keyed = fires once); else include in trend table → `send_report(priority="fyi", delta_key="material_prices")`.

**Steps:**
- [ ] Failing tests (mocked POST fixture with 2 series × 13 months incl. a +7% jump): `test_rows_stored_idempotent_rerun`, `test_spike_alert_once`, `test_flat_fyi_table`, `test_missing_series_degrades`.
- [ ] Implement; PASS; manual smoke against live BLS once (25 req/day cap — one run is fine); report which series ids actually resolved.

---

### Task 16: `drive_context` cron (item 26)

**Files:**
- Create: `agents/duke_harmon/crons/drive_context.py`
- Modify: `config/agents.yaml` (duke: `drive_context`, `15 6 * * 1-5`, log `logs/duke_drive.log`)
- Test: `tests/test_drive_context.py`

**Behavior:** `BAZA_HOME_ADDRESS` env (secrets.env) → geocode once, cache lat/lon in `cron_alert_state.meta` under key `drivectx:home` (or a tiny json file `configs/.home_coords.json` — pick the json file, simpler). Unset → `send_alert(key="drivectx:setup", renotify_hours=168)` one-line setup note, exit 0. For each In-Progress site with coords: OSRM `GET https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false` → duration/distance. Message: `🚗 Drives this morning — 123 Main St: ~32 min (18 mi, no-traffic baseline)`; OSRM down → haversine × 2.1 min/mi estimate marked "rough". `send_report(priority="fyi")` (06:15 is outside quiet hours → sends immediately, lands right before Duke's 07:00 lookahead).

**Steps:**
- [ ] Failing tests: `test_no_home_address_setup_nag_once`, `test_route_message_format`, `test_osrm_down_haversine_fallback`, `test_no_active_sites_silent`.
- [ ] Implement; PASS.

---

### Task 17: Telegram inline buttons — Ack / Snooze / Task (item 19)

**Files:**
- Modify: `core/telegram_fmt.py` (post_html optional `reply_markup: dict | None = None` — attach to the FINAL chunk's sendMessage payload as `reply_markup` JSON; plain-text fallback path keeps it too), `agents/cron_helpers.py` (`send_alert` builds `{"inline_keyboard":[[{"text":"✓ Ack","callback_data":f"cron|ack|{row_id}"},{"text":"😴 24h","callback_data":f"cron|snooze|{row_id}"},{"text":"➕ Task","callback_data":f"cron|task|{row_id}"}]]}` when `buttons=True`), `core/base_agent.py` (register `CallbackQueryHandler(self._on_cron_callback, pattern=r"^cron\|")` next to the existing MessageHandler at the `Application.builder()` site ~line 1949)
- Test: `tests/test_alert_buttons.py`

**Handler behavior:** parse `cron|<action>|<row_id>`; `ack` → `alert_ack`, edit message text += "\n\n✓ acknowledged"; `snooze` → `alert_snooze(id, 24)`, += "😴 snoozed 24h"; `task` → INSERT INTO `tasks` (**check `.schema tasks` first**; title = first line of alert meta/message, status/priority defaults matching Duke's dispatch expectations), += "➕ task created"; always `await query.answer()`. Callback DB ops wrapped in try/except → `query.answer("failed: …")`. Buttons only ship on BaseAgent-token sends; legacy claw/phil `agent.py` bots can't answer callbacks — acceptable, alerts default to Simon's token (`cron_helpers.TELEGRAM_TOKEN`) which is BaseAgent… **verify which architecture Simon runs (`agents/simon_bately/agent.py` — legacy `core/agent.py`!); if Simon is legacy, route buttoned alerts through a BaseAgent bot token instead (Duke `TELEGRAM_DUKE_HARMON`) and document.**

**Steps:**
- [ ] Failing tests: `test_post_html_reply_markup_last_chunk_only` (recorder on urlopen), `test_send_alert_button_payload`, `test_callback_ack_updates_state` (fake `update`/`query` objects, async via `asyncio.run`), `test_callback_task_inserts_row`, `test_callback_bad_id_answers_error`.
- [ ] Implement; PASS. `venv/bin/python -m pytest tests/test_telegram_fmt.py -v` → all 49 still green (no regression in the fresh rich-text work).

---

### Task 18: Deploy, cutover & verification

**Files:** none new (ops task) — update `.superpowers/sdd/progress.md` + session log.

**Steps:**
- [ ] `venv/bin/python -m core.cron_health_db` (init); `venv/bin/python -c "from core.weather_profile import ensure_weather_profile_column; ..."` run migrations; `venv/bin/python scripts/backfill_geocode.py` (expect ~22 geocodes, 1.1s apart).
- [ ] Full test sweep: `venv/bin/python -m pytest tests/ -x -q` — no NEW failures vs the 3 known pre-existing.
- [ ] `scripts/sync-agent-crons.py` (dry-run) → review adds; `--apply` (crontab, WITH timeouts) → verify `crontab -l`.
- [ ] systemd cutover: `--target systemd` dry-run review → `--target systemd --apply` → `systemctl --user list-timers 'baza-cron-*'` shows every unit with sane NEXT; confirm crontab managed lines removed; run one unit by hand `systemctl --user start baza-cron-client_pulse.service` → journal clean + `cron_runs` row.
- [ ] `sudo systemctl restart baza-dashboard` → `/crons` renders. Restart `baza-agent-duke-harmon` (or whichever BaseAgent handles callbacks) → send test alert with buttons → tap-test Ack path (ask Serge OR verify handler registered in journal).
- [ ] Manual smoke: `weather_watch` (real forecast for active sites; expect the no-address nag for the In-Progress project unless backfill fixed it), `dns_cert_watch` (expect nova drift finding), `backup_verify`.
- [ ] Append session-log entry + deploy report; list follow-ups. No git commits (auto-git).

---

## Self-Review

- **Spec coverage:** items 1–4,8→T5; 2→T2; 3→T5; 5→T6; 6→T5+T6; 7→T7; 9→T1+T4+T8+T9; 10→T10; 11,12,15→T9; 13 (retry/backoff) → covered narrowly: transient LLM/API failures degrade gracefully in-run (T2/T5/T12–16) and systemd `Persistent=true` + watchdog catch whole-run losses — explicit per-call retry deemed YAGNI beyond source fallbacks (NWS→Open-Meteo, ollama 3-instance chain already exists); 14→T11+T18; 16,17,18→T4+T8; 19→T17; 20→T7; 21,22→T12; 23→T14; 24→T15; 25→T13; 26→T16. ✔
- **Placeholders:** none — every step names tests and exact behavior; implementers read cited real files before coding. ✔
- **Type consistency:** `should_alert` returns `(bool, int)` used in T4/T5/T9/T13–16; `send_report`/`send_alert` signatures consistent across T4→T8→T12–17; `cron_to_oncalendar` only in T11. ✔
