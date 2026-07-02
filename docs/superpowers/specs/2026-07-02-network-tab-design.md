# Network tab + dashboard-wide hover-help — design

_2026-07-02. Approved by Serge (raw-control safety model chosen explicitly)._

## Goal

One place to see and control everything network on the empire: a **Network** tab under
the Projects submenu (`/network`) that maps Internet → router → baza → services →
public sites live, exposes **one-click manual controls for every toggle baza can
reach** (services, Tailscale, Caddy, interfaces, DNS providers, Cloudflare tunnel
migration, firewall), and a **settings registry** of every network config known to
baza. Plus a separate small system: **hover-help popovers** across the whole
dashboard (baza + ahb123 tab) for any workflow that takes 2+ steps.

## Ground truth (probed 2026-07-02)

- **Tailscale**: baza-1 `100.127.118.103` (offers exit node), phantom `100.89.36.114`
  active, 4 personal devices. `tailscale serve`: `baza-1.tailee5dc8.ts.net` → :8888,
  `:8443` → :8889 (vision). Snap-managed unit `snap.tailscale.tailscaled.service`.
- **Caddy**: active. `/etc/caddy/Caddyfile` terminates `nova.ahb123.com`, binds
  **192.168.1.68 only** (enp6s0, DHCP-reserved on the Fios G3100; can't wildcard :443
  because tailscaled holds :443 on the Tailscale IP). Routes: reviews → dashboard
  :8888, nova chat/leads/widget → tool-server :8000. Timestamped `.bak` files sit
  beside it (existing backup convention — keep it).
- **cloudflared 2026.6.1** installed at `/usr/local/bin/cloudflared`; **no tunnel, no
  config, no service yet**. 8-phase plan: `~/Desktop/ahb123-cloudflare-tunnel-plan.md`
  + record sheet `~/Desktop/ahb123-cloudflare-migration.md`. Blocked on Serge Phase 1
  (create Cloudflare account, add zone).
- **DNS**: apex `ahb123.com` = 4 Squarespace A records + `www` CNAME on **Google Cloud
  DNS**; `nova` sub-zone delegated to **deSEC** (ns1.desec.io/ns2.desec.org) →
  96.227.96.20 (residential IP, known drift); Google Workspace MX/SPF/DKIM live on the
  apex. `baza-ddns.service` (oneshot, Google Cloud DNS updater via
  `scripts/baza_ddns_update.py`) exists, **inactive**, no timer.
- **Interfaces**: 3 NICs all on 192.168.1.0/24 behind Fios G3100 (192.168.1.1):
  enp6s0 = .68 (reserved, Caddy bind), enp7s0 = .46, wlp5s0 = .39; three default
  routes (metrics 100/200/600); docker0 + br-cc6ec115c0f7 bridges; `openvpn.service`
  active-exited (no configured tunnels).
- **Router**: Fios G3100 — **no API**. DHCP reservation + any port-forwards are
  browser-manual; baza can only verify from outside.
- Dashboard: Flask (`dashboard/app.py`), standalone full-page templates that each
  include `templates/_nav.html`; `debug=False` → template edits need
  `sudo systemctl restart baza-dashboard`. Established ops-page pattern:
  `hardware_probe.py` (read-only collectors) + `hardware_ops.py` (actions) +
  `hardware.html`.

## Decisions made

- **Safety model: RAW CONTROL — no confirms, no auto-revert** (Serge's explicit
  choice). Actions that can sever remote access (stop tailscaled, stop caddy, NIC
  down) are styled red with a ⚡ lockout badge, but fire on one click.
- Architecture: follow the hardware pattern (probe/ops split inside the dashboard),
  not a separate service.
- Every mutating action is audit-logged.
- Hover-help is a **registry-driven** system (data, not per-template code), hard
  minimum 2 steps per entry, enforced by test.

## Part 1 — Network tab

### Files

| File | Role |
|------|------|
| `dashboard/network_probe.py` | Read-only fact collectors. No side effects, safe to call every page load. |
| `dashboard/network_ops.py` | Whitelisted mutating actions + audit log. Nothing shells out except through the whitelist. |
| `dashboard/network_routes.py` | Flask blueprint: page route `/network` + JSON APIs `/api/network/*`. Registered in `app.py`. |
| `dashboard/templates/network.html` | The tab. Nav entry in `_nav.html` Projects submenu (beside Infra/Edge). |
| `dashboard/network.db` (SQLite) | `audit_log`, `provider_tokens`, `manual_facts`, `wizard_state`. Separate DB like `claw_reviews.db` — keeps business DB clean. |

### 1a. Topology map (top of page)

Server builds a topology JSON from probes; client renders an HTML/CSS column-flow map
(no heavyweight graph lib): **Internet** (WAN IP) → **Fios G3100** → **baza NICs** +
docker bridges → **local services with ports** (dashboard :8888, tool-server :8000,
LiteLLM :4000, SD :7860, ollama :11434-38, Caddy :80/:443, Postgres :5432) — with the
**Tailscale mesh** overlaid (each peer: name, IP, OS, online/last-seen, exit-node
flag) and **public edge chains** drawn hop-by-hop:

- `ahb123.com` → Google Cloud DNS (→ Cloudflare post-migration) → Squarespace
- `nova.ahb123.com` → deSEC A → WAN IP → router forward → Caddy@.68 → :8000/:8888
- `baza.ahb123.com` → Cloudflare Tunnel → cloudflared → :8888 _(shows "planned" until live)_
- `baza-1.tailee5dc8.ts.net` → tailscale serve → :8888 / :8443→:8889

Every hop gets a live health dot: DNS resolves to expected value? port listening?
HTTP probe returns 2xx/3xx? TLS cert valid + days-to-expiry? Mismatches (e.g. deSEC A
≠ current WAN IP — the known drift) render amber with a one-line diagnosis. Clicking
any node opens a body-level drawer with its facts and its controls.

### 1b. Probes (`network_probe.py`)

Each returns a dict; page assembles them. All subprocess calls use JSON output where
available (`ip -j`, `tailscale status --json`) and are unit-tested against captured
fixtures.

- `interfaces()` — `ip -j addr` + `ip -j route`: NICs, IPs, routes+metrics, bridges.
- `wan_ip()` — 2 independent HTTPS resolvers, 3s timeout each, cached 60s.
- `tailscale()` — `tailscale status --json` + `tailscale serve status`: self, peers,
  serve mappings, exit-node advertisement.
- `dns_checks()` — `dig +short` against: apex A set (== Squarespace 4?), `www` CNAME,
  `nova` A (== WAN IP?), `baza` CNAME, MX, TXT/SPF, `google._domainkey` TXT, `nova`
  NS delegation. Each check = expected vs actual vs verdict.
- `services()` — `systemctl show` for caddy, snap.tailscale.tailscaled, cloudflared,
  baza-ddns (+timer), openvpn: active-state, sub-state, since.
- `caddy()` — parse Caddyfile (sites, binds, upstreams), `caddy validate` result,
  list of `.bak` backups with timestamps.
- `cloudflared()` — binary version, tunnel list (if authed), config.yml presence,
  service state → drives wizard phase detection.
- `listeners()` — `ss -tlnp`: port → process map.
- `firewall()` — ufw status / iptables -S summary (read-only view; rule toggles only
  if ufw is active — probe decides).
- `certs()` — TLS expiry for nova.ahb123.com and ts.net serve endpoint.
- `reachability()` — external checks: HTTP HEAD to https://nova.ahb123.com,
  https://ahb123.com, (post-migration) https://baza.ahb123.com — from baza's own
  egress, labeled as such.

### 1c. Controls (`network_ops.py`)

`ACTIONS` dict: `key → {cmd builder, risk: safe|risky, description}`. The API route
accepts an action key + validated params only — **no free-form commands** (the
diagnostics toolbox below is the one exception, with its own arg sanitizer).
Dashboard runs as root, so no sudoers changes needed. Every call appends to
`audit_log` (ts, action, params, rc, stdout/stderr tail).

- **Services**: start/stop/restart × {caddy, tailscaled, cloudflared, openvpn};
  `baza-ddns` run-now + enable/disable its timer (create timer unit if missing —
  P2 wizard territory, see below).
- **Tailscale**: `up`/`down`; advertise/stop-advertising exit node; serve add/remove
  for the two known mappings (8888, 8443→8889); (peers are display-only — no remote
  control of other devices).
- **Caddy**: in-browser Caddyfile editor → **Save & Apply** pipeline: write temp →
  `caddy validate` → on pass: timestamped `.bak` of current, move temp in place,
  `systemctl reload caddy` → report; on fail: show validate stderr, nothing touched.
  One-click **rollback** to any listed `.bak` (same validate-then-reload pipeline).
- **Interfaces**: per-NIC `ip link set up/down` (red ⚡), `dhclient -r && dhclient`
  renew per NIC.
- **DNS providers** (each its own panel, token stored in `provider_tokens` +
  mirrored to a 0600 file like social-pipeline tokens; panel greys out until token
  present):
  - **deSEC**: view/edit `nova.ahb123.com` RRsets via api.desec.io (this fixes the
    A-record drift from the tab, and can point nova at Cloudflare later — "control
    how our websites are linked").
  - **Google Cloud DNS**: reuse `scripts/baza_ddns_update.py`'s existing creds path;
    run-once button + current record view.
  - **Cloudflare**: token panel scaffolded now; zone/DNS/tunnel status lights up the
    moment the migration lands.
  - **dig anything** box: name + type → `dig` output (read-only, arg-sanitized).
- **Migration wizard**: the 8-phase Cloudflare plan rendered as an interactive
  checklist backed by `wizard_state` + live phase-detection from `cloudflared()`
  probe. 🟢 phases (tunnel create, config.yml write, DNS route, systemd install,
  verify) each get a **Run** button; 🔴 phases (account, zone, NS flip, Access app)
  get exact copy-paste instructions + a **Verify** button (e.g. `dig NS ahb123.com`
  == Cloudflare NS?). Wizard survives restarts; phases can be re-verified any time.
- **Firewall**: if ufw active — enable/disable, allow/deny rule add/remove from a
  form. If not active: read-only iptables view + note.
- **Diagnostics toolbox**: ping (count≤10), traceroute, dig, curl -sI, port-check
  (`nc -z`) — each a form with strict arg validation (hostname/IP/port regex, no
  shell metachars; args passed as list, `shell=False`), output streamed into a pre.

### 1d. Settings registry (bottom section)

One table of every network setting baza knows: source file, current value, edit
affordance where sane. Rows are generated from probes + a curated list:
`/etc/caddy/Caddyfile` (→ editor), `~/.cloudflared/config.yml` (→ editor once it
exists), ddns script config, tailscale prefs (accept-routes, exit node, serve),
`.env*` URL entries (BAZA_DASHBOARD_URL etc.), and **manual router facts** stored in
`manual_facts` (G3100 DHCP reservation .68, port-forward rules, admin URL
http://192.168.1.1) — editable as records so the map stays truthful, each with a
"verify from outside" button where checkable.

### 1e. Plumbing / cross-cutting

- Blueprint registered in `app.py`; nav entry added to `_nav.html` Projects submenu.
- All popups/drawers body-level (modal rule).
- Page auto-refreshes status via `/api/network/status` poll every 15s; probes with
  network egress (WAN IP, reachability) cached server-side 60s.
- **Local-first rule**: no cloud LLM calls anywhere in this feature; external HTTP is
  limited to WAN-IP echo services, provider DNS APIs, and reachability checks.
- Audit log viewable in a drawer (last 200 actions).

## Part 2 — Hover-help ("guides") across baza + ahb123

- **Assets**: `dashboard/static/help.js` + `help.css`, included from `_nav.html` so
  every page that has the nav gets them with zero per-template work.
- **Content**: `dashboard/static/help_content.json` — `{key: {title, steps: [...],
  link?}}`. Data, not code; extending coverage = add a JSON entry + one attribute.
- **Markup**: any element gains `data-help="<key>"`. `help.js` injects a small `?`
  badge next to it; hover (desktop) / tap (mobile) shows a popover with the title and
  **numbered steps**, optional "more" link (e.g. to the wizard or a doc). Popover is
  appended to `document.body` and positioned from the badge rect (never trapped by an
  ancestor `display:none`); one popover at a time; Esc/blur closes.
- **Hard rule: entries must have ≥ 2 steps** — this system documents multi-step
  workflows only, never single-click buttons. Enforced by test.
- **Initial coverage** (each = one JSON entry + one `data-help` attribute):
  invoice lifecycle (primary → deposit → In Progress → balance invoice), quote →
  invoice conversion, materials picker manage/CSV import, receipt correction →
  receipt_learn, social composer → publish, social connections OAuth, email
  multi-account OAuth paste-back (incl. Test User gotcha), cloud import
  (`baza-import`), vault unlock/lock, bin picker, hardware snapshot → verify-diff,
  and in the new Network tab: Caddyfile edit → validate → apply, migration wizard
  phases, deSEC token setup, tailscale serve changes.

## Testing (TDD throughout)

- Probe parsers against captured fixture outputs (`ip -j`, `tailscale status --json`,
  `systemctl show`, `dig`, `ss`), including degraded cases (tailscale down, caddy
  invalid, no cloudflared config).
- Ops: action whitelist (unknown key → 400), param validation (bad NIC name, bad
  hostname, shell metachars rejected), audit row written per action, Caddyfile
  apply pipeline (validate-fail leaves file untouched; pass creates .bak).
- deSEC/Cloudflare clients with mocked HTTP.
- Wizard phase detection from probe fixtures.
- Help: JSON schema valid; every entry ≥2 steps; every `data-help` key used in any
  template exists in the registry (grep-based test).
- Live verify: restart dashboard, curl `/network` + `/api/network/status`, click-test
  over Tailscale.

## Build order

1. **P1** — probes + topology map + service/tailscale controls + audit log + nav.
2. **P2** — Caddy editor+rollback, DNS provider panels (deSEC first), migration
   wizard, ddns timer.
3. **P3** — firewall, diagnostics toolbox, settings registry, reachability/certs.
4. **P4** — hover-help system + initial coverage sweep across existing tabs.

## Risks / notes

- Raw control means a mis-click on `tailscale down` or NIC-down over a remote
  session strands the session until physical/other-path access — accepted by Serge;
  red ⚡ styling is the only mitigation.
- Dashboard has no auth (family mode); the Network tab inherits that. The CF Access
  gate (migration Phase 7) is the eventual front door for WAN exposure.
- Fios G3100 stays manual forever (no API) — the design treats router state as
  declared facts + outside-in verification, never as a toggle.
- Don't touch the 5 ollama units' "duplicate serve" logic; Network tab only displays
  their ports.
