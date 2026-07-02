# Network Tab + Dashboard-Wide Hover-Help Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/network` dashboard tab that maps Internet → router → baza → services → public sites live with one-click raw controls for everything network (services, Tailscale, Caddy, NICs, DNS providers, Cloudflare migration wizard, firewall, diagnostics), plus a registry-driven hover-help popover system across the whole dashboard.

**Architecture:** Follows the established hardware pattern — `network_probe.py` (pure parsers + `_run` collectors, fixture-testable), `network_ops.py` (whitelisted actions + SQLite audit/state in `network.db`), `network_routes.py` (Flask blueprint), `network.html` (standalone full-page template incl. `_nav.html`). Hover-help is `static/help.js`/`help.css` + `static/help_content.json`, loaded from `_nav.html`.

**Tech Stack:** Python 3 / Flask / SQLite / subprocess (`sudo -n` prefix), vanilla JS, pytest (`venv/bin/python -m pytest`).

**Spec:** `docs/superpowers/specs/2026-07-02-network-tab-design.md` — read it first.

## Global Constraints

- Repo root: `/home/switchhacker/baza-empire/agent-framework-v3`. All paths below relative to it.
- **Do NOT manually `git commit`/`git push`** — `claw-auto-git.timer` commits hourly (CLAUDE.md rule). Task boundaries end with passing tests, not commits.
- Dashboard runs as `switchhacker` with blanket NOPASSWD sudo → privileged commands use `sudo -n` argv prefix, `shell=False` always.
- RAW CONTROL: no confirm dialogs anywhere. Risky actions get `class="btn-risky"` (red, ⚡ badge) — styling only.
- No free-form command execution: only whitelisted action keys + validated params. Diagnostics toolbox validates args with strict regexes.
- Local-first: no LLM calls; external HTTP only for WAN-IP echo, DNS provider APIs, reachability HEADs.
- Popovers/drawers appended to `document.body` (modal rule — never nested in a tab container).
- `debug=False` → after template/static edits: `sudo systemctl restart baza-dashboard` before browser verification.
- Unit names: `caddy.service`, `snap.tailscale.tailscaled.service`, `cloudflared.service`, `baza-ddns.service`, `openvpn.service`.
- Tests run from repo root: `venv/bin/python -m pytest tests/test_network_probe.py -v` etc. Test files import dashboard modules the way `tests/test_new_gap_skills.py` does: `sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))`.
- Known ground truth (probed 2026-07-02, use in fixtures): NICs enp6s0=.68/enp7s0=.46/wlp5s0=.39, router 192.168.1.1, tailnet baza-1=100.127.118.103 + phantom=100.89.36.114, serve :443→8888 + :8443→8889, Caddy binds 192.168.1.68 for nova.ahb123.com, apex A = {198.49.23.144, 198.49.23.145, 198.185.159.144, 198.185.159.145}, www CNAME ext-sq.squarespace.com, MX smtp.google.com, nova NS ns1.desec.io/ns2.desec.org.

---

### Task 1: `network.db` state layer (tables, audit log, tokens, manual facts, wizard state)

**Files:**
- Create: `dashboard/network_db.py`
- Test: `tests/test_network_db.py`

**Interfaces:**
- Produces (used by Tasks 3–10):
  - `ensure_tables(db_path=None) -> str` (returns path used; default `dashboard/network.db`, chmod 0600)
  - `audit(action: str, params: dict, rc: int, out: str, err: str, db_path=None) -> int` (row id)
  - `recent_audit(limit=200, db_path=None) -> list[dict]`
  - `get_token(provider: str, db_path=None) -> str|None`, `set_token(provider, token, db_path=None)`
  - `facts_list(db_path=None) -> list[dict]`, `fact_set(key, value, note="", db_path=None)`, `fact_delete(key, db_path=None)`
  - `wizard_get(db_path=None) -> dict` (phase→state), `wizard_set(phase: str, state: str, note="", db_path=None)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_network_db.py
import os, sys, stat

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))

import network_db


def test_roundtrip(tmp_path):
    db = str(tmp_path / "network.db")
    network_db.ensure_tables(db)
    # 0600 perms
    assert stat.S_IMODE(os.stat(db).st_mode) == 0o600

    rid = network_db.audit("svc_restart", {"unit": "caddy"}, 0, "ok", "", db_path=db)
    assert rid == 1
    rows = network_db.recent_audit(db_path=db)
    assert rows[0]["action"] == "svc_restart" and rows[0]["params"]["unit"] == "caddy"

    assert network_db.get_token("desec", db_path=db) is None
    network_db.set_token("desec", "tok123", db_path=db)
    assert network_db.get_token("desec", db_path=db) == "tok123"
    network_db.set_token("desec", "tok456", db_path=db)  # upsert
    assert network_db.get_token("desec", db_path=db) == "tok456"

    network_db.fact_set("router.reservation", "enp6s0 -> 192.168.1.68", note="G3100 DHCP", db_path=db)
    assert network_db.facts_list(db_path=db)[0]["key"] == "router.reservation"
    network_db.fact_delete("router.reservation", db_path=db)
    assert network_db.facts_list(db_path=db) == []

    network_db.wizard_set("phase1", "done", note="zone added", db_path=db)
    assert network_db.wizard_get(db_path=db)["phase1"]["state"] == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_network_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'network_db'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/network_db.py
"""SQLite state for the Network tab: audit log, provider tokens, manual
router facts, migration-wizard state. Separate file (network.db) so the
business DB stays clean; chmod 0600 because tokens live here."""
import json
import os
import sqlite3

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "network.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT (datetime('now','localtime')),
  action TEXT NOT NULL, params TEXT, rc INTEGER, out TEXT, err TEXT);
CREATE TABLE IF NOT EXISTS provider_tokens(
  provider TEXT PRIMARY KEY, token TEXT NOT NULL,
  updated TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS manual_facts(
  key TEXT PRIMARY KEY, value TEXT NOT NULL, note TEXT DEFAULT '',
  updated TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS wizard_state(
  phase TEXT PRIMARY KEY, state TEXT NOT NULL, note TEXT DEFAULT '',
  updated TEXT DEFAULT (datetime('now','localtime')));
"""


def _con(db_path=None):
    p = db_path or DEFAULT_DB
    con = sqlite3.connect(p, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def ensure_tables(db_path=None):
    p = db_path or DEFAULT_DB
    con = _con(p)
    con.executescript(_SCHEMA)
    con.commit()
    con.close()
    os.chmod(p, 0o600)
    return p


def audit(action, params, rc, out, err, db_path=None):
    con = _con(db_path)
    cur = con.execute(
        "INSERT INTO audit_log(action,params,rc,out,err) VALUES(?,?,?,?,?)",
        (action, json.dumps(params or {}), rc, (out or "")[-2000:], (err or "")[-2000:]))
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def recent_audit(limit=200, db_path=None):
    con = _con(db_path)
    rows = con.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d.get("params") or "{}")
        out.append(d)
    return out


def get_token(provider, db_path=None):
    con = _con(db_path)
    row = con.execute("SELECT token FROM provider_tokens WHERE provider=?",
                      (provider,)).fetchone()
    con.close()
    return row["token"] if row else None


def set_token(provider, token, db_path=None):
    con = _con(db_path)
    con.execute("INSERT INTO provider_tokens(provider,token) VALUES(?,?) "
                "ON CONFLICT(provider) DO UPDATE SET token=excluded.token, "
                "updated=datetime('now','localtime')", (provider, token))
    con.commit(); con.close()


def facts_list(db_path=None):
    con = _con(db_path)
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM manual_facts ORDER BY key").fetchall()]
    con.close()
    return rows


def fact_set(key, value, note="", db_path=None):
    con = _con(db_path)
    con.execute("INSERT INTO manual_facts(key,value,note) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "note=excluded.note, updated=datetime('now','localtime')",
                (key, value, note))
    con.commit(); con.close()


def fact_delete(key, db_path=None):
    con = _con(db_path)
    con.execute("DELETE FROM manual_facts WHERE key=?", (key,))
    con.commit(); con.close()


def wizard_get(db_path=None):
    con = _con(db_path)
    rows = con.execute("SELECT * FROM wizard_state").fetchall()
    con.close()
    return {r["phase"]: {"state": r["state"], "note": r["note"], "updated": r["updated"]}
            for r in rows}


def wizard_set(phase, state, note="", db_path=None):
    con = _con(db_path)
    con.execute("INSERT INTO wizard_state(phase,state,note) VALUES(?,?,?) "
                "ON CONFLICT(phase) DO UPDATE SET state=excluded.state, "
                "note=excluded.note, updated=datetime('now','localtime')",
                (phase, state, note))
    con.commit(); con.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_network_db.py -v`
Expected: PASS (1 test)

---

### Task 2: `network_probe.py` — pure parsers (interfaces, tailscale, services, listeners, dns verdicts)

**Files:**
- Create: `dashboard/network_probe.py`
- Test: `tests/test_network_probe.py`

**Interfaces:**
- Produces (pure functions, fixture-testable — collectors come in Task 3):
  - `parse_interfaces(ip_addr_json: str, ip_route_json: str) -> dict` → `{"nics":[{name, ips[], up}], "routes":[{dst, dev, metric, gateway}]}`
  - `parse_tailscale(status_json: str, serve_text: str) -> dict` → `{"self":{...}, "peers":[{host, ip, os, online, last_seen, exit_node}], "serves":[{listen, target}]}`
  - `parse_unit_show(text: str) -> dict` → `{"active": "active", "sub": "running", "since": "..."}`
  - `parse_listeners(ss_text: str) -> list[dict]` → `[{port, proc, addr}]`
  - `dns_verdict(name, rtype, expected, actual: list[str]) -> dict` → `{"name", "rtype", "expected", "actual", "ok": bool}` (ok = set-equality for multi-value; for expectation `"@WAN"` caller substitutes WAN IP first; expected `None` = informational, ok=True when non-empty)

- [ ] **Step 1: Write the failing tests** (fixtures are trimmed real outputs)

```python
# tests/test_network_probe.py
import json, os, sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))

import network_probe as np

IP_ADDR = json.dumps([
    {"ifname": "lo", "operstate": "UNKNOWN", "addr_info": [{"family": "inet", "local": "127.0.0.1"}]},
    {"ifname": "enp6s0", "operstate": "UP", "addr_info": [{"family": "inet", "local": "192.168.1.68"}]},
    {"ifname": "wlp5s0", "operstate": "UP", "addr_info": [{"family": "inet", "local": "192.168.1.39"}]},
    {"ifname": "tailscale0", "operstate": "UNKNOWN", "addr_info": [{"family": "inet", "local": "100.127.118.103"}]},
])
IP_ROUTE = json.dumps([
    {"dst": "default", "gateway": "192.168.1.1", "dev": "enp6s0", "metric": 100},
    {"dst": "192.168.1.0/24", "dev": "enp6s0"},
])

TS_STATUS = json.dumps({
    "Self": {"HostName": "baza-1", "TailscaleIPs": ["100.127.118.103"], "OS": "linux", "Online": True},
    "Peer": {
        "k1": {"HostName": "phantom", "TailscaleIPs": ["100.89.36.114"], "OS": "linux",
               "Online": True, "LastSeen": "2026-07-02T13:00:00Z", "ExitNodeOption": False},
        "k2": {"HostName": "iphone-15", "TailscaleIPs": ["100.124.197.40"], "OS": "iOS",
               "Online": False, "LastSeen": "2026-06-28T10:00:00Z", "ExitNodeOption": False},
    },
})
TS_SERVE = ("https://baza-1.tailee5dc8.ts.net (tailnet only)\n"
            "|-- / proxy http://127.0.0.1:8888\n\n"
            "https://baza-1.tailee5dc8.ts.net:8443 (tailnet only)\n"
            "|-- / proxy http://localhost:8889\n")

UNIT_SHOW = "ActiveState=active\nSubState=running\nActiveEnterTimestamp=Wed 2026-07-01 10:00:00 EDT\n"

SS = ('LISTEN 0 128 0.0.0.0:8888 0.0.0.0:* users:(("python",pid=1234,fd=5))\n'
      'LISTEN 0 128 192.168.1.68:443 0.0.0.0:* users:(("caddy",pid=999,fd=9))\n')


def test_parse_interfaces():
    r = np.parse_interfaces(IP_ADDR, IP_ROUTE)
    nics = {n["name"]: n for n in r["nics"]}
    assert nics["enp6s0"]["up"] is True and "192.168.1.68" in nics["enp6s0"]["ips"]
    assert "lo" not in nics  # loopback excluded
    assert r["routes"][0]["gateway"] == "192.168.1.1" and r["routes"][0]["metric"] == 100


def test_parse_tailscale():
    r = np.parse_tailscale(TS_STATUS, TS_SERVE)
    assert r["self"]["ip"] == "100.127.118.103"
    phantom = [p for p in r["peers"] if p["host"] == "phantom"][0]
    assert phantom["online"] is True
    assert {"listen": "443", "target": "http://127.0.0.1:8888"} in r["serves"]
    assert {"listen": "8443", "target": "http://localhost:8889"} in r["serves"]


def test_parse_unit_show():
    r = np.parse_unit_show(UNIT_SHOW)
    assert r["active"] == "active" and r["sub"] == "running"


def test_parse_listeners():
    r = np.parse_listeners(SS)
    by_port = {x["port"]: x for x in r}
    assert by_port[8888]["proc"] == "python"
    assert by_port[443]["addr"] == "192.168.1.68"


def test_dns_verdict():
    apex = ["198.49.23.144", "198.49.23.145", "198.185.159.144", "198.185.159.145"]
    ok = np.dns_verdict("ahb123.com", "A", apex, list(reversed(apex)))
    assert ok["ok"] is True  # order-insensitive
    bad = np.dns_verdict("nova.ahb123.com", "A", ["1.2.3.4"], ["96.227.96.20"])
    assert bad["ok"] is False
    info = np.dns_verdict("ahb123.com", "TXT", None, ["v=spf1 ..."])
    assert info["ok"] is True  # informational: non-empty answer
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_network_probe.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the parsers**

```python
# dashboard/network_probe.py
"""Read-only network state probe for the Network tab.

Pure parse functions (this task) are separated from shelling-out collectors
(added below them) exactly like hardware_probe.py, so all verdict/parse logic
is unit-testable with injected fixture strings."""
import json
import re
import subprocess

SKIP_IFACES = {"lo"}


def _run(cmd, timeout=10):
    """Run argv, return (rc, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:  # noqa: BLE001
        return -1, "", str(e)


# ───────────── pure parsers ─────────────

def parse_interfaces(ip_addr_json, ip_route_json):
    nics = []
    for it in json.loads(ip_addr_json or "[]"):
        name = it.get("ifname", "")
        if name in SKIP_IFACES:
            continue
        ips = [a["local"] for a in it.get("addr_info", []) if a.get("family") == "inet"]
        nics.append({"name": name, "ips": ips,
                     "up": it.get("operstate") in ("UP", "UNKNOWN")})
    routes = []
    for r in json.loads(ip_route_json or "[]"):
        routes.append({"dst": r.get("dst", ""), "dev": r.get("dev", ""),
                       "gateway": r.get("gateway"), "metric": r.get("metric")})
    return {"nics": nics, "routes": routes}


def parse_tailscale(status_json, serve_text):
    st = json.loads(status_json or "{}")
    self_ = st.get("Self") or {}
    me = {"host": self_.get("HostName", ""), "os": self_.get("OS", ""),
          "ip": (self_.get("TailscaleIPs") or [""])[0],
          "online": bool(self_.get("Online"))}
    peers = []
    for p in (st.get("Peer") or {}).values():
        peers.append({"host": p.get("HostName", ""),
                      "ip": (p.get("TailscaleIPs") or [""])[0],
                      "os": p.get("OS", ""), "online": bool(p.get("Online")),
                      "last_seen": p.get("LastSeen", ""),
                      "exit_node": bool(p.get("ExitNodeOption"))})
    serves, listen = [], None
    for line in (serve_text or "").splitlines():
        m = re.search(r"https://[^\s:]+(?::(\d+))?\s", line + " ")
        if line.startswith("https://"):
            listen = m.group(1) if (m and m.group(1)) else "443"
        pm = re.search(r"proxy\s+(\S+)", line)
        if pm and listen:
            serves.append({"listen": listen, "target": pm.group(1)})
    return {"self": me, "peers": peers, "serves": serves}


def parse_unit_show(text):
    kv = dict(line.split("=", 1) for line in (text or "").splitlines() if "=" in line)
    return {"active": kv.get("ActiveState", "unknown"),
            "sub": kv.get("SubState", "unknown"),
            "since": kv.get("ActiveEnterTimestamp", "")}


def parse_listeners(ss_text):
    out = []
    for line in (ss_text or "").splitlines():
        if not line.startswith("LISTEN"):
            continue
        m = re.search(r"\s(\S+):(\d+)\s", line)
        pm = re.search(r'users:\(\("([^"]+)"', line)
        if m:
            out.append({"addr": m.group(1), "port": int(m.group(2)),
                        "proc": pm.group(1) if pm else ""})
    return out


def dns_verdict(name, rtype, expected, actual):
    actual = [a.strip().rstrip(".") for a in (actual or []) if a.strip()]
    if expected is None:
        ok = bool(actual)
    else:
        exp = [e.strip().rstrip(".") for e in expected]
        ok = set(exp) == set(actual)
    return {"name": name, "rtype": rtype, "expected": expected,
            "actual": actual, "ok": ok}
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_network_probe.py -v`
Expected: PASS (5 tests)

---

### Task 3: `network_probe.py` — collectors + `status()` aggregator + edge chains

**Files:**
- Modify: `dashboard/network_probe.py` (append)
- Test: `tests/test_network_probe.py` (append)

**Interfaces:**
- Produces:
  - `probe_interfaces()`, `probe_tailscale()`, `probe_services()`, `probe_listeners()` — shell wrappers around Task 2 parsers
  - `probe_wan_ip() -> str|None` (2 echo services, 3s timeout, module-level 60s cache)
  - `probe_dns() -> list[verdict]` (dig-based, uses `dns_verdict`; nova A expectation = WAN IP)
  - `probe_caddy() -> dict` → `{"active", "sites":[...], "valid": bool, "validate_err", "backups":[{name, ts}]}`
  - `probe_cloudflared() -> dict` → `{"installed", "version", "config_exists", "unit_state", "tunnels": str}`
  - `probe_certs() -> list[{host, days_left, ok}]` and `probe_reachability() -> list[{url, status, ok}]` (cached 60s)
  - `status() -> dict` — the one call routes use; assembles everything + `edges` (the 4 public chains with per-hop health) + `known_ports` service map
  - `EXPECTED_DNS` constant (record-sheet expectations), `KNOWN_PORTS` constant (`{8888: "dashboard", 8000: "tool-server", 4000: "litellm", 7860: "sd-webui", 11434-11438: "ollama-*", 5432: "postgres", 443: "caddy", 80: "caddy"}`)
  - `UNITS` constant = `["caddy.service", "snap.tailscale.tailscaled.service", "cloudflared.service", "baza-ddns.service", "openvpn.service"]`

**Notes for implementer:**
- `probe_wan_ip`: try `https://api.ipify.org` then `https://ifconfig.me/ip` via `urllib.request`, 3s timeout each; cache `(ts, value)` in a module global for 60s. Return `None` if both fail.
- `probe_dns`: run `dig +short <name> <type>` via `_run`, split lines. Expectations table:
  ```python
  EXPECTED_DNS = [
      ("ahb123.com", "A", ["198.49.23.144", "198.49.23.145", "198.185.159.144", "198.185.159.145"]),
      ("www.ahb123.com", "CNAME", ["ext-sq.squarespace.com"]),
      ("ahb123.com", "MX", ["1 smtp.google.com"]),
      ("nova.ahb123.com", "NS", ["ns1.desec.io", "ns2.desec.org"]),
      ("nova.ahb123.com", "A", "@WAN"),          # substitute probe_wan_ip()
      ("baza.ahb123.com", "A", None),            # informational until migration
      ("ahb123.com", "TXT", None),               # SPF present?
      ("google._domainkey.ahb123.com", "TXT", None),
  ]
  ```
  For `"@WAN"`: if WAN unknown, verdict ok=None (amber "unknown"). NS answers come back with trailing dots — `dns_verdict` already strips them.
- `probe_caddy`: `parse_caddy_sites(text)` pure helper — regex site blocks (`^([a-z0-9.\-]+)\s*\{` at indent 0) and `bind`/`reverse_proxy` lines → `sites: [{host, bind, upstreams[]}]`. Collector reads `/etc/caddy/Caddyfile`, runs `_run(["caddy", "validate", "--config", "/etc/caddy/Caddyfile"])` (rc 0 = valid), globs `/etc/caddy/Caddyfile.bak.*`.
- `probe_cloudflared`: `installed` = `/usr/local/bin/cloudflared` exists; version from `cloudflared --version`; `config_exists` = `~/.cloudflared/config.yml` isfile; unit via `systemctl show`; `tunnels` = stdout of `cloudflared tunnel list` (rc≠0 → "not authenticated").
- `probe_certs`: `ssl.create_default_context()` + `socket.create_connection((host,443), timeout=4)` → `getpeercert()` → `notAfter` days-left, for `nova.ahb123.com`; wrap all in try/except → `{"host":..., "days_left": None, "ok": False, "err": str(e)}`.
- `probe_reachability`: `urllib.request` HEAD (method override) to `https://ahb123.com`, `https://nova.ahb123.com`, `https://baza.ahb123.com`; 5s timeout; ok = status < 400. Cache 60s alongside WAN cache.
- `status()` assembles: `{"interfaces", "tailscale", "services": {unit: parse_unit_show(...)}, "listeners", "wan_ip", "dns", "caddy", "cloudflared", "certs", "reach", "edges", "ts": iso-now}`.
- `edges` builder is a **pure function** `build_edges(dns, wan_ip, caddy, cloudflared, listeners, tailscale) -> list` producing the 4 chains; each hop = `{"label", "ok": True|False|None, "detail"}`:
  1. `ahb123.com`: hop DNS-apex (verdict ok) → hop Squarespace (reach ok for https://ahb123.com)
  2. `nova.ahb123.com`: hop deSEC-A (verdict; amber on drift) → hop router-forward (reach https://nova.ahb123.com) → hop Caddy@.68 (caddy active + 443 listener on 192.168.1.68) → hop upstreams (:8000/:8888 listening)
  3. `baza.ahb123.com`: if not `cloudflared["config_exists"]` → single hop "planned — run Migration wizard" (ok=None); else CF chain (unit active, reach)
  4. `ts.net`: per serve mapping → hop serve → hop target port listening
- **Test** `build_edges` purely with dicts (drift case: nova A ≠ WAN → hop ok False; planned baza → ok None) and `parse_caddy_sites` with a trimmed Caddyfile string fixture. Collectors themselves get one smoke test each guarded to not assert on live values (just shape: keys present, no exception).

- [ ] **Step 1: Write failing tests for `parse_caddy_sites` + `build_edges` (append to test file)** — fixture Caddyfile string with `nova.ahb123.com { bind 192.168.1.68 ... reverse_proxy localhost:8888 ... reverse_proxy localhost:8000 }`; assert host/bind/upstreams extracted; edges: drift fixture → nova chain hop ok False; no-config cloudflared → baza chain single planned hop.
- [ ] **Step 2: Run — expect FAIL (functions missing)**
- [ ] **Step 3: Implement collectors + `build_edges` + `status()` per notes above**
- [ ] **Step 4: `venv/bin/python -m pytest tests/test_network_probe.py -v` — all PASS; then live smoke: `venv/bin/python -c "import sys; sys.path.insert(0,'dashboard'); import network_probe; s=network_probe.status(); print(sorted(s.keys())); print(len(s['edges']),'edges')"` from repo root — prints keys + 4 edges, no traceback**

---

### Task 4: `network_ops.py` — action whitelist + service/tailscale/interface/ddns actions

**Files:**
- Create: `dashboard/network_ops.py`
- Test: `tests/test_network_ops.py`

**Interfaces:**
- Consumes: `network_db.audit`, `network_db.ensure_tables`
- Produces:
  - `ACTIONS: dict[str, dict]` — `key -> {"risk": "safe"|"risky", "desc": str, "argv": callable(params)->list[str]}`
  - `run_action(key: str, params: dict, db_path=None) -> dict` → `{"ok": bool, "rc": int, "out": str, "err": str, "action": key}`; raises `ValueError` on unknown key or bad params; audits every run (including failures)
  - `VALID_UNIT = re.compile(...)`, `VALID_NIC = re.compile(r"^(enp6s0|enp7s0|wlp5s0)$")`

**Action table (exact):**

| key | risk | argv |
|-----|------|------|
| `svc` (params: unit ∈ UNITS, verb ∈ start/stop/restart) | stop of caddy/tailscaled = risky | `["sudo","-n","systemctl",verb,unit]` |
| `ddns_run` | safe | `["sudo","-n","systemctl","start","baza-ddns.service"]` |
| `ts_up` / `ts_down` | down = risky | `["sudo","-n","tailscale","up"]` / `["sudo","-n","tailscale","down"]` |
| `ts_exit_node` (params: on bool) | safe | `["sudo","-n","tailscale","set","--advertise-exit-node=true|false"]` |
| `ts_serve` (params: mapping ∈ {"dash","vision"}, on bool) | off = risky | dash on: `["sudo","-n","tailscale","serve","--bg","--https=443","http://127.0.0.1:8888"]`, dash off: `[...,"serve","--https=443","off"]`; vision uses `--https=8443` → `http://localhost:8889` |
| `nic` (params: name VALID_NIC, verb ∈ up/down) | down = risky | `["sudo","-n","ip","link","set",name,verb]` |
| `dhcp_renew` (params: name VALID_NIC) | risky | `["sudo","-n","dhclient","-v",name]` (30s timeout) |

- [ ] **Step 1: Failing tests** — unknown key raises ValueError; `svc` with unit not in UNITS raises; `nic` with `name="evil; rm"` raises; monkeypatch `network_ops._run` to return `(0,"ok","")` and assert `run_action("svc", {"unit":"caddy.service","verb":"restart"}, db_path=tmp)` returns ok=True AND wrote an audit row with action `svc`; monkeypatch rc=1 → ok=False, still audited.

```python
# tests/test_network_ops.py (core of it)
import os, sys
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
import pytest
import network_db, network_ops


def test_whitelist_and_audit(tmp_path, monkeypatch):
    db = str(tmp_path / "n.db"); network_db.ensure_tables(db)
    with pytest.raises(ValueError):
        network_ops.run_action("rm_rf", {}, db_path=db)
    with pytest.raises(ValueError):
        network_ops.run_action("svc", {"unit": "evil.service", "verb": "stop"}, db_path=db)
    with pytest.raises(ValueError):
        network_ops.run_action("nic", {"name": "eth0; rm -rf /", "verb": "down"}, db_path=db)
    calls = []
    monkeypatch.setattr(network_ops, "_run", lambda cmd, timeout=20: (calls.append(cmd) or (0, "ok", "")))
    r = network_ops.run_action("svc", {"unit": "caddy.service", "verb": "restart"}, db_path=db)
    assert r["ok"] and calls[0] == ["sudo", "-n", "systemctl", "restart", "caddy.service"]
    assert network_db.recent_audit(db_path=db)[0]["action"] == "svc"
    monkeypatch.setattr(network_ops, "_run", lambda cmd, timeout=20: (1, "", "boom"))
    r = network_ops.run_action("ts_down", {}, db_path=db)
    assert r["ok"] is False and len(network_db.recent_audit(db_path=db)) == 2


def test_serve_argv():
    argv = network_ops.ACTIONS["ts_serve"]["argv"]({"mapping": "dash", "on": False})
    assert argv == ["sudo", "-n", "tailscale", "serve", "--https=443", "off"]
```

- [ ] **Step 2: Run — FAIL (module missing)**
- [ ] **Step 3: Implement** — module has its own `_run` (same shape as probe's, default timeout 20s, `dhcp_renew` passes 30), `ACTIONS` per table (argv callables validate params and raise ValueError on anything off-whitelist), `run_action` = look up key → build argv → `_run` → `network_db.audit(key, params, rc, out, err, db_path)` → return dict. `risk` is resolved per-call where verb-dependent: `ACTIONS[key].get("risk_fn", lambda p: ACTIONS[key]["risk"])(params)` — expose `action_meta() -> [{key, desc, risk}]` for the UI.
- [ ] **Step 4: `venv/bin/python -m pytest tests/test_network_ops.py -v` — PASS**

---

### Task 5: `network_routes.py` blueprint + app registration + nav entry

**Files:**
- Create: `dashboard/network_routes.py`
- Modify: `dashboard/app.py` (append registration next to hardware's at ~line 16319)
- Modify: `dashboard/templates/_nav.html` (Projects submenu, after Infra)
- Create: `dashboard/templates/network.html` (minimal shell this task; full UI Task 6)
- Test: `tests/test_network_routes.py`

**Interfaces:**
- Consumes: `network_probe.status`, `network_ops.run_action/action_meta`, `network_db.*`
- Produces routes:
  - `GET /network` → `render_template("network.html", nav_active="network")`
  - `GET /api/network/status` → `jsonify(network_probe.status())`
  - `POST /api/network/action` body `{action, params}` → run_action result; ValueError → 400 `{"error": ...}`
  - `GET /api/network/audit` → `{"rows": recent_audit(limit)}`
  - `network_bp = Blueprint("network", __name__)` and `init_network()` (calls `network_db.ensure_tables()`)

- [ ] **Step 1: Failing test** — build a Flask test app registering just the blueprint (like other route tests), monkeypatch `network_probe.status` → `{"edges": [], "ts": "x"}` and `network_ops.run_action` → sentinel; assert: GET `/api/network/status` 200 + JSON; POST `/api/network/action` `{"action":"nope"}` with real run_action → 400; POST with monkeypatched run_action 200; GET `/network` 200 contains `id="net-map"`.

```python
# tests/test_network_routes.py
import os, sys
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
from flask import Flask
import network_routes


def make_app(tmp_path, monkeypatch):
    monkeypatch.setattr(network_routes.network_db, "DEFAULT_DB", str(tmp_path / "n.db"))
    app = Flask("t", template_folder=os.path.join(REPO_ROOT, "dashboard", "templates"),
                static_folder=os.path.join(REPO_ROOT, "dashboard", "static"))
    network_routes.init_network()
    app.register_blueprint(network_routes.network_bp)
    return app.test_client()


def test_status_and_action(tmp_path, monkeypatch):
    c = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(network_routes.network_probe, "status", lambda: {"edges": [], "ts": "x"})
    assert c.get("/api/network/status").status_code == 200
    r = c.post("/api/network/action", json={"action": "definitely_not_real", "params": {}})
    assert r.status_code == 400
    monkeypatch.setattr(network_routes.network_ops, "run_action",
                        lambda k, p, db_path=None: {"ok": True, "rc": 0, "out": "", "err": "", "action": k})
    assert c.post("/api/network/action", json={"action": "svc", "params": {}}).get_json()["ok"]
    assert c.get("/api/network/audit").status_code == 200
```

Note: `GET /network` render needs the full `_nav.html` context other pages pass; if `_nav.html` requires variables (check how `hardware_page` renders), mirror that. If rendering under the bare test app fails on nav includes, keep the page-render assertion in the live-verify step instead of the unit test.

- [ ] **Step 2: Run — FAIL**
- [ ] **Step 3: Implement** `network_routes.py`:

```python
# dashboard/network_routes.py
from flask import Blueprint, jsonify, render_template, request

try:
    from dashboard import network_db, network_ops, network_probe
except ImportError:
    import network_db, network_ops, network_probe

network_bp = Blueprint("network", __name__)


def init_network():
    network_db.ensure_tables()


@network_bp.route("/network")
def network_page():
    return render_template("network.html", nav_active="network")


@network_bp.route("/api/network/status")
def api_status():
    return jsonify(network_probe.status())


@network_bp.route("/api/network/action", methods=["POST"])
def api_action():
    body = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(network_ops.run_action(body.get("action", ""), body.get("params") or {}))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@network_bp.route("/api/network/audit")
def api_audit():
    limit = min(int(request.args.get("limit", 200)), 500)
    return jsonify({"rows": network_db.recent_audit(limit)})
```

`app.py` — append right after the hardware registration block (find `app.register_blueprint(_hardware_bp)`):

```python
try:
    from dashboard.network_routes import network_bp as _network_bp, init_network as _init_network
except ImportError:
    from network_routes import network_bp as _network_bp, init_network as _init_network
_init_network()
app.register_blueprint(_network_bp)
```

`_nav.html` — inside the Projects `nav-submenu`, directly after the Infra line:

```html
<a href="/network" class="{% if _act == 'network' %}active{% endif %}" title="Network — map + controls for the whole stack">&#127760; Network</a>
```

`network.html` — copy the outer skeleton of `hardware.html` (doctype, head/css includes, `{% include '_nav.html' %}`, container div) with `<div id="net-map">loading…</div>` placeholder body; how `_act` gets set: check top of `hardware.html`/`_nav.html` (`{% set _act = ... %}` convention) and mirror with `network`.

- [ ] **Step 4: `venv/bin/python -m pytest tests/test_network_routes.py -v` — PASS**

---

### Task 6: `network.html` P1 UI — topology map, services panel, tailscale panel, NIC panel, audit drawer

**Files:**
- Modify: `dashboard/templates/network.html` (full page)
- Test: manual live verify (template JS; no pytest)

**Interfaces:**
- Consumes: `/api/network/status`, `/api/network/action`, `/api/network/audit` (Task 5 shapes)

**Build (single-page vanilla JS, follow hardware.html / dashboard house style — dark theme, cards):**

1. **Map section** (`#net-map`): three-column flow — col 1 Internet card (WAN IP + reach dots), col 2 Router card (192.168.1.1 + manual-facts note) & Tailscale mesh card (self + peers w/ green/grey dots, last-seen, exit-node ⚡ badge), col 3 baza card (NICs w/ up/down dot + IPs; listeners table port→proc from `KNOWN_PORTS` labels). Below: **Edge chains** — one row per `edges[]` entry, hops rendered as pills joined by →, pill class by `ok` (true=green, false=red, null=amber/planned), `detail` in title attr. Clicking any card opens `#net-drawer` (body-level fixed right drawer) with the raw JSON facts + that node's controls.
2. **Controls row**: Services card — one row per unit in `status.services`: name, state badge, Start/Stop/Restart buttons (`postAction('svc',{unit,verb})`); Stop for caddy + tailscaled gets `btn-risky` (red bg + ⚡). `ddns_run` button on the baza-ddns row. Tailscale card — Up/Down (Down risky), exit-node checkbox → `ts_exit_node`, serve toggles for dash/vision → `ts_serve` (off risky). NIC card — per NIC Up/Down (down risky) + "Renew DHCP".
3. **Audit drawer**: nav button "🧾 Audit" → body-level drawer, fetch `/api/network/audit`, table ts/action/params/rc.
4. JS core:

```javascript
async function postAction(action, params){
  const r = await fetch('/api/network/action', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action, params})});
  const j = await r.json();
  toast(j.ok ? `✓ ${action}` : `✗ ${action}: ${(j.err||j.error||'').slice(0,200)}`);
  setTimeout(refresh, 1200);
}
async function refresh(){
  const s = await (await fetch('/api/network/status')).json();
  renderMap(s); renderServices(s); renderTailscale(s); renderNics(s);
}
refresh(); setInterval(refresh, 15000);
```

   `toast()` — reuse the dashboard's existing toast helper if one exists in shared JS (grep `static/js/`); otherwise 10-line inline fallback.
5. CSS: `.btn-risky{background:#7f1d1d;border-color:#dc2626}` + `.btn-risky::after{content:" ⚡"}`, pill colors `.hop-ok/.hop-bad/.hop-amber`, drawer `position:fixed;right:0;top:0;height:100%;z-index:9999` appended to body.

- [ ] **Step 1: Build the page per the layout above**
- [ ] **Step 2: `sudo systemctl restart baza-dashboard && sleep 2 && curl -s http://127.0.0.1:8888/network | grep -c 'net-map'` → ≥1; `curl -s http://127.0.0.1:8888/api/network/status | python3 -m json.tool | head` → real JSON with 4 edges**
- [ ] **Step 3: Browser verify over Tailscale (`https://baza-1.tailee5dc8.ts.net/network`): map renders, peers show, a SAFE action round-trips (e.g. Restart baza-ddns or Restart caddy) and lands in the audit drawer. Do NOT click Stop tailscaled.**

---

### Task 7: Caddy editor — read/apply (validate→backup→reload)/rollback

**Files:**
- Modify: `dashboard/network_ops.py` (append `caddy_read/caddy_apply/caddy_rollback`)
- Modify: `dashboard/network_routes.py` (3 routes)
- Modify: `dashboard/templates/network.html` (Caddy card: state, sites table from probe, "Edit Caddyfile" → body-level modal w/ textarea + Apply; backups list w/ Rollback buttons)
- Test: `tests/test_network_ops.py` (append)

**Interfaces:**
- Produces:
  - `caddy_read() -> {"path", "text", "backups": [names]}`
  - `caddy_apply(text: str, db_path=None) -> {"ok", "stage": "validate"|"reload"|"done", "err"}` — pipeline: write `text` to `/etc/caddy/.Caddyfile.staged` (via `sudo -n tee`, input on stdin) → `sudo -n caddy validate --config /etc/caddy/.Caddyfile.staged` → on fail: return stage=validate + stderr, **live file untouched** → on pass: `sudo -n cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak.<YYYYmmdd-HHMMSS>` → `sudo -n cp .staged Caddyfile` → `sudo -n systemctl reload caddy` → audit + return done
  - `caddy_rollback(backup_name: str, db_path=None)` — name must match `re.fullmatch(r"Caddyfile\.bak\.[\w-]+", name)`; same validate→bak-current→cp→reload pipeline using the backup as source
- Routes: `GET /api/network/caddyfile` → caddy_read; `POST /api/network/caddyfile` `{text}` → caddy_apply; `POST /api/network/caddyfile/rollback` `{name}` → caddy_rollback

- [ ] **Step 1: Failing tests** — monkeypatch `_run` with a recorder that returns rc=1 + "syntax error" for the validate argv: assert `caddy_apply("bad")` → ok False, stage "validate", and **no** `cp`/`reload` argv was recorded after it; happy-path recorder (all rc=0): assert argv sequence = tee-staged → validate → cp-bak → cp-live → reload, audit row written; `caddy_rollback("../../etc/passwd")` raises ValueError.
- [ ] **Step 2: Run — FAIL**
- [ ] **Step 3: Implement per pipeline above** (tee pattern: `subprocess.run(["sudo","-n","tee",staged], input=text, ...)` — extend `_run` with optional `input_text=None`). Wire routes + modal UI (textarea monospace, Apply button shows stage+stderr on fail).
- [ ] **Step 4: Tests PASS; live: restart dashboard, open editor, apply the UNCHANGED current Caddyfile → expect ok/done, a new .bak, `systemctl is-active caddy` = active, and https://nova.ahb123.com still 200.**

---

### Task 8: deSEC DNS panel (token + view/edit nova RRsets)

**Files:**
- Create: `dashboard/network_dns.py`
- Modify: `dashboard/network_routes.py` (token + rrset routes), `dashboard/templates/network.html` (DNS providers section: deSEC card)
- Test: `tests/test_network_dns.py`

**Interfaces:**
- Produces (`network_dns.py`; all HTTP via injectable `http=None` param defaulting to a small `urllib` wrapper `_http(method, url, headers, body) -> (status, json)` so tests mock it):
  - `desec_rrsets(token, http=None) -> list[{subname, type, ttl, records[]}]` — GET `https://desec.io/api/v1/domains/nova.ahb123.com/rrsets/`, header `Authorization: Token <token>`
  - `desec_set_rrset(token, subname, rtype, ttl, records: list[str], http=None) -> dict` — PUT `.../rrsets/<subname or '@'>/<rtype>/` body `{"subname","type","ttl","records"}`; records validated: for type A each must match IPv4 regex
  - `DESEC_DOMAIN = "nova.ahb123.com"`
- Routes: `GET/POST /api/network/token/<provider>` (POST body `{token}` → `network_db.set_token`; GET returns `{"set": bool}` — **never the token itself**); `GET /api/network/desec` → rrsets (403-style `{"error":"no token"}` if unset); `POST /api/network/desec` `{subname, rtype, ttl, records}` → set + audit (`action="desec_set"`).
- UI: deSEC card — token input (password field, saved via POST, shows "token set ✓"), RRset table, edit row → prompt-modal, and a one-click **"Point nova A → current WAN IP"** button (uses `status.wan_ip`, calls set_rrset `@/A`), which is the drift fix.

- [ ] **Step 1: Failing tests** — mock `http`: `desec_rrsets` sends Token header + right URL; `desec_set_rrset` PUTs right body; bad IP (`"96.227.96"`) raises ValueError; route GET token never leaks (`resp.get_json() == {"set": True}` after POST).
- [ ] **Step 2: FAIL** → **Step 3: implement** → **Step 4: unit PASS. Live wiring waits until Serge pastes a deSEC token — the card correctly shows "no token yet" state meanwhile (verify that renders).**

---

### Task 9: Google Cloud DNS + Cloudflare panels + ddns timer

**Files:**
- Modify: `dashboard/network_ops.py`, `dashboard/network_routes.py`, `dashboard/templates/network.html`
- Create: `/etc/systemd/system/baza-ddns.timer` (via ops action, content below)
- Test: `tests/test_network_ops.py` (append)

**Work:**
1. **GCloud DNS card**: shows current `nova`-relevant records from `probe_dns` + the ddns unit/timer state; buttons = `ddns_run` (exists since Task 4) and new actions `ddns_timer_enable` / `ddns_timer_disable`:
   - `ddns_timer_enable` argv is a two-step op (implement as a small python function, not single argv): if `/etc/systemd/system/baza-ddns.timer` missing, write it via `sudo -n tee`:
     ```ini
     [Unit]
     Description=baza DDNS — hourly WAN IP sync to Google Cloud DNS
     [Timer]
     OnCalendar=hourly
     RandomizedDelaySec=300
     [Install]
     WantedBy=timers.target
     ```
     then `sudo -n systemctl daemon-reload` + `sudo -n systemctl enable --now baza-ddns.timer`. Disable = `disable --now`. Both audited. Register these as ACTIONS entries whose `argv` is `None` and add a `fn` field — extend `run_action`: if `ACTIONS[key]["fn"]` exists call it (it returns the result dict and does its own audit via a passed helper).
2. **Cloudflare card** (scaffold): token input (same `/api/network/token/cloudflare` plumbing as Task 8), and `GET /api/network/cloudflare` → if token set, GET `https://api.cloudflare.com/client/v4/zones?name=ahb123.com` (Bearer) via the same `_http` wrapper in `network_dns.py` (`cf_zone_status(token, http=None) -> {"found": bool, "status": str, "name_servers": []}`); card shows "zone not on Cloudflare yet — see Migration wizard" until found.
3. Tests: mocked-`_run` sequence assertions for `ddns_timer_enable` (tee only when file missing — monkeypatch `os.path.exists`), `cf_zone_status` header/URL via mock http.

- [ ] Steps: failing tests → implement → PASS → live: restart dashboard, GCloud card shows unit inactive + timer state; do NOT enable timer yet (Serge's call — the button exists, spec's raw-control).

---

### Task 10: Cloudflare migration wizard

**Files:**
- Create: `dashboard/network_wizard.py`
- Modify: `dashboard/network_routes.py`, `dashboard/templates/network.html` (wizard section)
- Test: `tests/test_network_wizard.py`

**Interfaces:**
- `PHASES: list[dict]` — 8 entries mirroring `~/Desktop/ahb123-cloudflare-tunnel-plan.md` exactly: `{"id": "phase0".."phase8", "who": "claude"|"serge", "title", "instructions" (markdown, copy-paste exact from the plan file incl. the grey-cloud/NS/MX warnings for phase 2), "verify": callable|None, "run": action-key|None}`
- `detect(status: dict, wizard_db: dict) -> list[dict]` — pure: merges live probe evidence over stored state; e.g. phase0 auto-done (cloudflared installed), phase4 done when `dig NS ahb123.com` ∈ cloudflare NS, phase5 done when `config_exists`, phase6 done when unit active, phase8 done when reach baza.ahb123.com ok. Manual phases (1,2,7) come from `wizard_state` (Serge clicks "Mark done") + verify evidence where possible.
- Verify helpers (in `network_wizard.py`, using `network_probe._run` dig): `verify_ns()` → `{"ok": bool, "actual": [...]}` (ok when both NS contain `.ns.cloudflare.com`); `verify_baza_dns()`; `verify_email()` → MX+SPF+DKIM verdicts (reuse `probe_dns` results).
- Run actions (registered in `ACTIONS` as `fn`-style, all audited):
  - `wiz_tunnel_create` → `sudo -n -u switchhacker cloudflared tunnel create baza-dashboard` — actually cloudflared auth lives in `~/.cloudflared` of switchhacker, dashboard already runs as switchhacker → plain `["cloudflared","tunnel","create","baza-dashboard"]`
  - `wiz_write_config` → writes `~/.cloudflared/config.yml`:
    ```yaml
    tunnel: baza-dashboard
    credentials-file: /home/switchhacker/.cloudflared/<UUID>.json   # UUID discovered from `cloudflared tunnel list` output at run time
    ingress:
      - hostname: baza.ahb123.com
        service: http://localhost:8888
      - service: http_status:404
    ```
  - `wiz_route_dns` → `["cloudflared","tunnel","route","dns","baza-dashboard","baza.ahb123.com"]`
  - `wiz_install_service` → `sudo -n cloudflared --config /home/switchhacker/.cloudflared/config.yml service install` then `sudo -n systemctl enable --now cloudflared`
- Routes: `GET /api/network/wizard` → `detect(status, wizard_get())`; `POST /api/network/wizard/mark` `{phase, state}`; `POST /api/network/wizard/verify` `{phase}`; run buttons go through the normal `/api/network/action`.
- UI: vertical checklist, each phase card = who-badge (🔴 Serge / 🟢 baza), state (todo/done/verified/blocked), instructions collapsible, Run/Verify/Mark-done buttons as applicable.

- [ ] **Step 1: Failing tests** — `detect` purity: fixture status (cloudflared installed, no config, google NS) + empty db → phase0 done, phase3 todo, phase5 todo; fixture with cloudflare NS + config + active unit → phases 3-6 done. `verify_ns` parse: monkeypatch dig output `"tia.ns.cloudflare.com.\nkip.ns.cloudflare.com.\n"` → ok True; google NS → False. `wiz_write_config` extracts UUID from fixture `cloudflared tunnel list` stdout and writes yaml containing hostname line (write to tmp path via injected home).
- [ ] **Step 2: FAIL** → **Step 3: implement** → **Step 4: unit PASS; live: wizard renders, phase0 shows done, phase1 shows Serge instructions. Tunnel-create/route/install buttons will 4xx-ish gracefully until Serge does phase 1/5-login — verify the error surfaces in the toast + audit, not a 500.**

---

### Task 11: Firewall panel

**Files:**
- Modify: `dashboard/network_probe.py` (`probe_firewall`), `network_ops.py` (ufw actions), `network_routes.py` (nothing new — uses status+action), `network.html` (card)
- Test: append to probe/ops tests

**Work:**
- `probe_firewall()`: `sudo -n ufw status verbose` → pure `parse_ufw(text) -> {"present": bool, "active": bool, "rules": [str]}` (`present=False` when rc≠0/binary missing → card falls back to `sudo -n iptables -S` first 40 lines read-only).
- Actions: `ufw_toggle` (params on bool → `["sudo","-n","ufw","--force","enable"|"disable"]`, enable risky — could block tailscale/caddy if defaults deny), `ufw_rule` (params: verb ∈ allow/deny/delete-allow/delete-deny, port int 1-65535, proto ∈ tcp/udp) → e.g. `["sudo","-n","ufw","allow","8888/tcp"]`, delete verbs → `["sudo","-n","ufw","delete","allow","8888/tcp"]`. Param validation raises ValueError.
- Tests: `parse_ufw` on active/inactive/missing fixtures; `ufw_rule` argv + port 99999 raises.

- [ ] failing tests → implement → PASS → live render check.

---

### Task 12: Diagnostics toolbox

**Files:**
- Modify: `dashboard/network_ops.py` (`run_diag`), `network_routes.py` (`POST /api/network/diag`), `network.html` (toolbox card)
- Test: append `tests/test_network_ops.py`

**Interfaces:**
- `run_diag(tool: str, target: str, extra: dict, db_path=None) -> {"ok","out","err","rc"}`; tools table:

| tool | argv | constraints |
|------|------|-------------|
| ping | `["ping","-c",str(count),"-W","2",target]` | count 1-10 default 4 |
| traceroute | `["traceroute","-w","2","-m","20",target]` | 45s timeout |
| dig | `["dig","+short",target,rtype]` | rtype ∈ {A,AAAA,CNAME,MX,NS,TXT,SOA} |
| curl | `["curl","-sSI","--max-time","8",url]` | url must start https:// or http:// |
| port | `["nc","-z","-v","-w","3",target,str(port)]` | port 1-65535 |

- `target` must match `re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", target)` (hostname/IPv4 charset — kills metachars); curl's url: `re.fullmatch(r"https?://[A-Za-z0-9.\-]+(?::\d+)?(/[A-Za-z0-9.\-_/?=&%]*)?", url)`. Every run audited as `diag_<tool>`.
- UI: tool select + target input + go → `<pre>` output. dig box gets the type select.

- [ ] failing tests (bad target `"a.com; reboot"` raises; ping argv correct; curl bad scheme raises) → implement → PASS → live: dig ahb123.com A from the browser returns the Squarespace IPs.

---

### Task 13: Settings registry section

**Files:**
- Modify: `dashboard/network_probe.py` (`settings_registry()`), `network_routes.py` (`GET /api/network/registry`, `POST/DELETE /api/network/facts`), `network.html` (bottom table)
- Test: append probe tests

**Work:**
- `settings_registry(status: dict, facts: list) -> list[{group, key, value, source, edit}]` — pure assembler:
  - Caddy: path + per-site bind/upstreams (edit → opens Caddy editor)
  - cloudflared: config path or "absent" (edit → wizard)
  - tailscale: serve mappings, exit-node flag (edit → the toggles)
  - ddns: unit+timer states
  - env: grep `BAZA_DASHBOARD_URL|NOVA_|CADDY_` lines from `.env`* files in repo root + `~/baza-empire/.env.nuc` if readable (values shown, read-only)
  - router (manual_facts rows, editable + "verify" where a `verify_url` note is present → runs reachability)
- Seed manual facts on first `init_network()` if table empty: `router.model=Fios G3100`, `router.admin=http://192.168.1.1`, `router.reservation=enp6s0 f0:2f:74:1b:aa:e9 → 192.168.1.68`, `router.port_forward=443,80 → 192.168.1.68 (verify: https://nova.ahb123.com)`.
- Tests: pure assembler groups/edit flags from fixture status; facts routes CRUD (reuse route-test client).

- [ ] failing tests → implement → PASS → live render.

---

### Task 14: Hover-help system (`help.js`, `help.css`, registry, tests)

**Files:**
- Create: `dashboard/static/help_content.json`, `dashboard/static/help.js`, `dashboard/static/css/help.css` (put css beside existing `static/css/`)
- Modify: `dashboard/templates/_nav.html` (asset includes at top of the nav include, so every page gets them)
- Test: `tests/test_help_registry.py`

**Interfaces:**
- JSON schema: `{ "<key>": {"title": str, "steps": [str, ...>=2], "link": str|absent} }`
- Markup contract: `data-help="<key>"` on any element → `?` badge injected after it.

- [ ] **Step 1: Failing tests**

```python
# tests/test_help_registry.py
import json, os, re, glob

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(REPO_ROOT, "dashboard")
REG = os.path.join(DASH, "static", "help_content.json")


def _registry():
    with open(REG) as f:
        return json.load(f)


def test_schema_and_min_steps():
    reg = _registry()
    assert reg, "registry must not be empty"
    for key, entry in reg.items():
        assert re.fullmatch(r"[a-z0-9_.\-]+", key), key
        assert entry["title"].strip()
        assert isinstance(entry["steps"], list) and len(entry["steps"]) >= 2, \
            f"{key}: hover-help is for 2+ step workflows only"
        assert all(isinstance(s, str) and s.strip() for s in entry["steps"])


def test_every_template_key_exists():
    reg = _registry()
    used = set()
    for tpl in glob.glob(os.path.join(DASH, "templates", "*.html")):
        used.update(re.findall(r'data-help="([^"]+)"', open(tpl).read()))
    missing = used - set(reg)
    assert not missing, f"data-help keys missing from registry: {missing}"


def test_nav_includes_assets():
    nav = open(os.path.join(DASH, "templates", "_nav.html")).read()
    assert "help.js" in nav and "help.css" in nav
```

- [ ] **Step 2: FAIL** → **Step 3: Implement:**

`help.css`:

```css
.help-badge{display:inline-flex;align-items:center;justify-content:center;
  width:15px;height:15px;margin-left:5px;border-radius:50%;font-size:10px;
  font-weight:700;cursor:help;background:#1e3a5f;color:#7dd3fc;
  border:1px solid #38bdf8;vertical-align:middle;user-select:none}
.help-pop{position:fixed;z-index:99999;max-width:340px;background:#0f172a;
  color:#e2e8f0;border:1px solid #38bdf8;border-radius:8px;padding:12px 14px;
  font-size:13px;line-height:1.45;box-shadow:0 8px 30px rgba(0,0,0,.6)}
.help-pop h4{margin:0 0 6px;color:#7dd3fc;font-size:13px}
.help-pop ol{margin:0;padding-left:18px}
.help-pop li{margin:3px 0}
.help-pop a{color:#38bdf8}
```

`help.js`:

```javascript
/* Hover-help: elements with data-help="<key>" get a ? badge whose hover/tap
   shows numbered steps from /static/help_content.json. Popover lives on
   document.body (never trapped by display:none ancestors). */
(function () {
  let REG = null, pop = null, hideTimer = null;

  async function reg() {
    if (!REG) REG = await (await fetch('/static/help_content.json')).json();
    return REG;
  }

  function hide() { if (pop) { pop.remove(); pop = null; } }

  async function show(badge, key) {
    const r = await reg(); const e = r[key];
    if (!e) return;
    hide();
    pop = document.createElement('div');
    pop.className = 'help-pop';
    const ol = e.steps.map(s => `<li>${s}</li>`).join('');
    pop.innerHTML = `<h4>${e.title}</h4><ol>${ol}</ol>` +
      (e.link ? `<div style="margin-top:6px"><a href="${e.link}">more →</a></div>` : '');
    document.body.appendChild(pop);
    const b = badge.getBoundingClientRect();
    pop.style.left = Math.min(b.left, innerWidth - pop.offsetWidth - 12) + 'px';
    pop.style.top = (b.bottom + 6 + pop.offsetHeight > innerHeight
      ? b.top - pop.offsetHeight - 6 : b.bottom + 6) + 'px';
    pop.addEventListener('mouseenter', () => clearTimeout(hideTimer));
    pop.addEventListener('mouseleave', () => hideTimer = setTimeout(hide, 200));
  }

  function attach(el) {
    if (el.dataset.helpDone) return;
    el.dataset.helpDone = '1';
    const badge = document.createElement('span');
    badge.className = 'help-badge'; badge.textContent = '?';
    badge.setAttribute('role', 'button');
    el.insertAdjacentElement('afterend', badge);
    badge.addEventListener('mouseenter', () => show(badge, el.dataset.help));
    badge.addEventListener('mouseleave', () => hideTimer = setTimeout(hide, 200));
    badge.addEventListener('click', ev => { ev.stopPropagation(); pop ? hide() : show(badge, el.dataset.help); });
  }

  function scan() { document.querySelectorAll('[data-help]').forEach(attach); }
  document.addEventListener('DOMContentLoaded', scan);
  new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });
})();
```

`_nav.html` (first lines of the include):

```html
<link rel="stylesheet" href="/static/css/help.css">
<script defer src="/static/help.js"></script>
```

`help_content.json` — seed with the Network-tab entries now (dashboard-wide sweep is Task 15): `network.caddy_edit` (edit → validate runs automatically → backup created → reload; on validate-fail nothing changes), `network.wizard` (phases run top-to-bottom; 🔴 = you in a browser, 🟢 = baza button; NS flip is the point of no return until reverted), `network.desec_token` (get token at desec.io → paste → saved 0600 → then edit nova records), `network.serve_toggle` (2 steps incl. "turning dash serve off kills this very page over ts.net"), `network.ddns_timer`, `network.diag` — each ≥2 steps, exact wording implementer's choice but concrete.

- [ ] **Step 4: `venv/bin/python -m pytest tests/test_help_registry.py -v` PASS; restart dashboard; on /network hover a badge → popover with numbered steps; press Esc → closes; check one page WITHOUT any data-help (e.g. /cloud) console-error-free.**

---

### Task 15: Hover-help coverage sweep across existing tabs

**Files:**
- Modify: `dashboard/static/help_content.json` + one `data-help` attribute per workflow in: `templates/ahb123.html` (invoice lifecycle, quote→invoice, materials picker, payment terms), `templates/email.html` (multi-account OAuth incl. Test-User 403 gotcha, attachments/share), `templates/cloud.html` (baza-import, vault unlock/lock), `templates/datahub.html` (bin picker), `templates/hardware.html` (snapshot→verify flow), social template(s) (`social_*` grep for the composer/publish + connections OAuth blocks), `templates/settings.html` if OAuth lives there — **grep first, attach to the section heading or primary button of each flow**.
- Test: existing `tests/test_help_registry.py` (`test_every_template_key_exists` enforces sync automatically)

**Entries (each ≥2 steps, content from memory files + CLAUDE.md):** `invoice.lifecycle` (primary sent → deposit recorded → In Progress → balance-invoice route; never re-add auto 50/50 lines), `invoice.quote_convert`, `materials.import` (manage modal → CSV import → picker), `receipts.correct` (edit vendor/category/date → corrections table → receipt_learn picks it up; totals never auto-change), `social.publish` (sources → editor → copy panel → publish/export), `social.connect_oauth`, `email.add_account` (GCP Testing mode → add Test User first or 403 → OAuth paste-back), `cloud.import` (`baza-import <mount> "<label>" --register` → lands in Imports/YYYY-MM-DD), `vault.use` (unlock → work → lock; panic wipes keys), `bin.picker`, `hardware.swap` (snapshot baseline → swap → verify-diff after reboot).

- [ ] Add attributes + entries → run full help test → PASS → restart dashboard → spot-check 3 tabs render badges.

---

### Task 16: Final verification + docs + session log

- [ ] Full suite: `venv/bin/python -m pytest tests/test_network_db.py tests/test_network_probe.py tests/test_network_ops.py tests/test_network_routes.py tests/test_network_dns.py tests/test_network_wizard.py tests/test_help_registry.py -v` — ALL PASS
- [ ] Regression: `venv/bin/python -m pytest tests/ -x -q -k "not slow"` — no new failures vs. before this work (capture baseline count first)
- [ ] `sudo systemctl restart baza-dashboard && sleep 2`; verify `/network` over Tailscale: map + 4 edge chains + all panels render; run one safe action; check audit drawer; hover 2 help badges on 2 different tabs
- [ ] Confirm `nova.ahb123.com` still serves (`curl -sI https://nova.ahb123.com | head -1` → HTTP 200/30x) and caddy active — we touched its config pipeline
- [ ] Append session-log entry (`### YYYY-MM-DD HH:MM | Network tab + hover-help — shipped`, with file list + test counts). Do NOT git commit — auto-git handles it.
- [ ] Update memory: new file `project_network_tab.md` + MEMORY.md line (what shipped, where controls live, raw-control decision, wizard state location)

## Self-Review Notes

- Spec coverage: topology map (T3/T6), raw controls services/ts/nic (T4/T6), Caddy editor+rollback (T7), deSEC/GCloud/CF panels (T8/T9), wizard (T10), firewall (T11), diagnostics (T12), settings registry + manual facts + seed (T13/T1), audit log (T1/T4-12), hover-help system + ≥2-step rule + coverage (T14/T15), local-first (no LLM anywhere), body-level popovers (T6/T14). Cert/reachability probes (T3) feed edges + registry verify buttons.
- Types consistent: `run_action(key, params, db_path=None) -> {ok, rc, out, err, action}` used by all routes; verdict dict shape shared probe↔wizard; `_http(method, url, headers, body) -> (status, json)` shared deSEC/CF.
- Deliberate deferrals (not placeholders): full `network.html` markup follows hardware.html house style rather than being reproduced verbatim; help entry wording is specified by content, not literal strings.
