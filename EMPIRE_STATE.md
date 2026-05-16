# Empire State

Hand-curated source-of-truth for what is currently live, what is killed, and
what changed recently. Read at every agent boot. Topic blocks are loaded
on-demand by the `self_orient` skill, not at boot.

Owner: Serge. Keep this tight — 1.5 KB target, 4 KB hard cap.
Update LIVE/KILLED/TOPIC by hand. RECENT auto-syncs nightly from session-log.

## LIVE
- ahb123.com — Squarespace + Caddy proxy → baza-tool-server (migrated 2026-04-23)
- baza-dashboard — http://localhost:8888 (Flask, port 8888, all tabs active)
- baza cloud — private auth, family mode, fixed user_id=1 (Serge)
- 9 agents — Simon Claw Phil Sam Rex Duke Scout Nova Specter
- nova.ahb123.com — Nova client-facing chat
- Method 4 pricing on AHB123 (materials/labor/tools/profit/overhead)
- Memories tab — 67k photos / 14k videos indexed from /mnt/empirepool/cloud/1/Photos
- Pulse tab — /empire-pulse, claim_verifier wired, fabrications surfaced (2026-05-11)

## KILLED
- mining (nerdminer-monitor.service) — purged 2026-05-10, 17-file cleanup. DO NOT re-enable.
- old ollama 0.18.3 — upgraded to 0.23.2 on baza + phantom (2026-05-10)
- mock databases in agent tests — burned us in a prior migration; use real PG.

## RECENT
- 2026-05-15 ahb123 — Add Project modal: calendar range, client contact, draft autosave
- 2026-05-15 ahb123 — revert Add Project date selector to plain inputs
- 2026-05-15 ahb123 — inline editable Start/End dates on each project row
- 2026-05-15 ahb123 — unified click-to-pick project timeframe (modal + edit + list)
- 2026-05-15 ahb123 — persistent custom scopes in project modal datalist
- 2026-05-15 Baza image picker — pick from Data Hub anywhere an image upload exists
- 2026-05-15 ahb123 — drag-and-drop projects between year groups
- 2026-05-15 ahb123 — Uncle Sam quick year tabs (current + past 3)
- 2026-05-15 Vision tab fix + queue backfill + Sam burst-batch reply
- 2026-05-15 Vision thumbnails — 403 fix
- 2026-05-15 ahb123 — fix: year pills on DASHBOARD tab (not Uncle Sam)
- 2026-05-15 ahb123 — Billing: completed projects grouped by year
- 2026-05-15 Inbound is now public; Vault is the only private location; Sam handles all file types
- 2026-05-15 QuickRF — bulk vendor rename propagation in Review & Edit Recent panel
- 2026-05-15 QuickRF — rqe column rework + project picker

## TOPIC: mining
KILLED 2026-05-10. nerdminer-monitor.service stopped + disabled. Full purge:
1015 restart loops resolved by removing the service entirely. 17-file cleanup
across Simon, Claw, Phil, core, tools, dashboard. Skills `mining_earnings`,
`mining_status`, etc. removed. Do NOT re-enable. Crypto reporting is gone too.

## TOPIC: ahb123
LIVE since 2026-04-23. Migrated from old WordPress site to Squarespace bundle
at `proj-ahb123/sq_bundle/`. Caddy reverse-proxies → baza-tool-server. Nova
serves client-facing chat at nova.ahb123.com. AHB123 dashboard tab has:
projects (35), invoices, estimates with Method 4 pricing, payroll, sticky pad
notes, Memories cloud media browser. NOT "still on the old site". NOT "we
should migrate". The migration is DONE.

## TOPIC: pulse
The /empire-pulse dashboard tab (added 2026-05-11). Shows talk-vs-ship ratio
per agent: claimed completions (journal rows matching completion verbs) vs
artifacts actually saved. Health: green = shipping, yellow = some output, red =
all-talk. `core/claim_verifier.py` now runs inside `journal_log()` (2026-05-11)
— any completion claim without a matching agent-attributed artifact in the
last 2h gets `verified=FALSE` and a `[UNVERIFIED CLAIM]` banner prepended.
Simon currently red with 5 fabrications, 0 ships in 30d.

## TOPIC: dashboard
Flask app at dashboard/app.py, port 8888. Tabs: Empire Pulse, AHB123 (projects/
invoices/payroll/sticky), Datahub (artifacts), Cloud (Memories), Approvals,
Chains, Comms, Crons, Email, Infra, Journal, Memory, Mobile, Portal, Private,
Shell, Skills, Tasks, Vision. baza-dashboard.service runs it under systemd.

## TOPIC: claim_verifier
core/claim_verifier.py. Detects completion verbs in agent text and checks
whether any artifact attributed to that agent landed in dashboard/artifacts/
within the last 2h. Returns {verified, claims, unbacked_count, artifact_count}.
Wired into journal_log() as of 2026-05-11. Tests: tests/test_claim_verifier.py
(9 pass). Per-agent strict mode skips weak-fallback so non-shippers cannot
free-ride on coworker artifacts.

## TOPIC: myself
This is a special topic — the self_orient skill returns the calling agent's
own directory tree (1 level), full skills list, and last 5 task_journal rows
when invoked with `{"topic":"myself"}`.
