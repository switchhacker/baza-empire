"""Read-only network state probe for the Network tab.

Pure parse functions (this task) are separated from shelling-out collectors
(added below them) exactly like hardware_probe.py, so all verdict/parse logic
is unit-testable with injected fixture strings."""
import json
import re
import subprocess
import sys
import time
import urllib.error
from datetime import datetime, timezone

SKIP_IFACES = {"lo"}

KNOWN_PORTS = {
    8888: "dashboard",
    8000: "tool-server",
    4000: "litellm",
    7860: "sd-webui",
    11434: "ollama-primary",
    11435: "ollama-cuda",
    11436: "ollama-cpu",
    11437: "ollama-amd",
    11438: "ollama-dual",
    5432: "postgres",
    443: "caddy",
    80: "caddy",
}

UNITS = [
    "caddy.service",
    "snap.tailscale.tailscaled.service",
    "cloudflared.service",
    "baza-ddns.service",
    "openvpn.service",
]

# PROBE_UNITS extends UNITS with the timer so probe_services() shows it on the
# Network tab/registry, but UNITS stays unchanged so network_ops.py's svc
# action whitelist never allows start/stop/restart on the timer (it has its
# own ddns_timer_enable/disable actions).
PROBE_UNITS = UNITS + ["baza-ddns.timer"]

# nova A expectation uses "@WAN" sentinel — substituted at probe time
EXPECTED_DNS = [
    ("ahb123.com", "A", ["198.49.23.144", "198.49.23.145", "198.185.159.144", "198.185.159.145"]),
    ("www.ahb123.com", "CNAME", ["ext-sq.squarespace.com"]),
    ("ahb123.com", "MX", ["1 smtp.google.com"]),
    ("nova.ahb123.com", "NS", ["ns1.desec.io", "ns2.desec.org"]),
    ("nova.ahb123.com", "A", "@WAN"),
    ("baza.ahb123.com", "A", None),
    ("ahb123.com", "TXT", None),
    ("google._domainkey.ahb123.com", "TXT", None),
]

# module-level caches: (timestamp, value)
_wan_cache: tuple[float, str | None] | None = None
_reach_cache: tuple[float, list] | None = None


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
        ips = [a.get("local") for a in it.get("addr_info", []) if a.get("family") == "inet" and a.get("local")]
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
    actual = [a.strip().rstrip(".").lower() for a in (actual or []) if a.strip()]
    if expected is None:
        ok = bool(actual)
    else:
        exp = [e.strip().rstrip(".").lower() for e in expected]
        ok = set(exp) == set(actual)
    return {"name": name, "rtype": rtype, "expected": expected,
            "actual": actual, "ok": ok}


def parse_caddy_sites(text):
    """Pure: parse Caddyfile text → list of {host, bind, upstreams[]}."""
    sites = []
    current = None
    depth = 0
    for line in (text or "").splitlines():
        stripped = line.strip()
        # top-level site block: hostname { at column 0
        if depth == 0 and re.match(r'^[a-z0-9.\-]+\s*\{', line):
            host = re.match(r'^([a-z0-9.\-]+)', line).group(1)
            current = {"host": host, "bind": None, "upstreams": []}
            depth = 1
            continue
        if current is None:
            # count braces even outside a site block we care about
            depth += stripped.count("{") - stripped.count("}")
            if depth < 0:
                depth = 0
            continue
        depth += stripped.count("{") - stripped.count("}")
        if depth <= 0:
            sites.append(current)
            current = None
            depth = 0
            continue
        # bind only at top-level inside site block; reverse_proxy at any depth
        if depth == 1:
            m_bind = re.match(r'bind\s+(\S+)', stripped)
            if m_bind:
                current["bind"] = m_bind.group(1)
        m_proxy = re.match(r'reverse_proxy\s+(\S+)', stripped)
        if m_proxy:
            upstream = m_proxy.group(1)
            if upstream not in current["upstreams"]:
                current["upstreams"].append(upstream)
    if current:
        sites.append(current)
    return sites


# ───────────── collectors ─────────────

def probe_interfaces():
    """Shell wrapper: ip -j addr show + ip -j route show."""
    try:
        _, addr_out, _ = _run(["ip", "-j", "addr", "show"])
        _, route_out, _ = _run(["ip", "-j", "route", "show"])
        return parse_interfaces(addr_out, route_out)
    except Exception as e:
        return {"nics": [], "routes": [], "err": str(e)}


def probe_tailscale():
    """Shell wrapper: tailscale status --json + tailscale serve status."""
    try:
        _, status_out, _ = _run(["tailscale", "status", "--json"])
        _, serve_out, _ = _run(["tailscale", "serve", "status"])
        return parse_tailscale(status_out, serve_out)
    except Exception as e:
        return {"self": {}, "peers": [], "serves": [], "err": str(e)}


def probe_services():
    """Return {unit: parse_unit_show()} for each unit in PROBE_UNITS."""
    result = {}
    for unit in PROBE_UNITS:
        try:
            _, out, _ = _run(["systemctl", "show", unit,
                               "--property=ActiveState,SubState,ActiveEnterTimestamp"])
            result[unit] = parse_unit_show(out)
        except Exception as e:
            result[unit] = {"active": "unknown", "sub": "unknown", "since": "", "err": str(e)}
    return result


def probe_listeners():
    """Shell wrapper: ss -tlnp."""
    try:
        _, out, _ = _run(["ss", "-tlnp"])
        return parse_listeners(out)
    except Exception as e:
        print(f"[network_probe] listeners probe failed: {e}", file=sys.stderr)
        return []


def probe_wan_ip():
    """Return WAN IP string or None; cached 60s."""
    global _wan_cache
    import urllib.request
    now = time.time()
    if _wan_cache and (now - _wan_cache[0]) < 60:
        return _wan_cache[1]
    value = None
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "baza-probe/1"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                value = resp.read().decode().strip()
            if value:
                break
        except Exception:
            continue
    _wan_cache = (now, value)
    return value


def probe_dns():
    """Run dig for each EXPECTED_DNS entry; substitute @WAN with probe_wan_ip()."""
    wan = probe_wan_ip()
    results = []
    for entry in EXPECTED_DNS:
        name, rtype, expected = entry
        # resolve @WAN sentinel
        if expected == "@WAN":
            expected = [wan] if wan else None
        try:
            _, out, _ = _run(["dig", "+short", name, rtype], timeout=8)
            actual = [line for line in out.splitlines() if line.strip()]
        except Exception:
            actual = []
        v = dns_verdict(name, rtype, expected, actual)
        # amber for @WAN when WAN unknown
        if entry[2] == "@WAN" and wan is None:
            v["ok"] = None
        results.append(v)
    return results


def probe_caddy():
    """Read /etc/caddy/Caddyfile, validate, glob backups."""
    import glob
    result = {"active": False, "sites": [], "valid": False, "validate_err": None, "backups": []}
    try:
        with open("/etc/caddy/Caddyfile") as f:
            text = f.read()
        result["sites"] = parse_caddy_sites(text)
        result["active"] = True
    except Exception as e:
        result["validate_err"] = str(e)
        return result
    try:
        rc, _, err = _run(["caddy", "validate", "--config", "/etc/caddy/Caddyfile"])
        result["valid"] = (rc == 0)
        if rc != 0:
            result["validate_err"] = err.strip()
    except Exception as e:
        result["validate_err"] = str(e)
    try:
        backups = []
        for path in sorted(glob.glob("/etc/caddy/Caddyfile.bak.*")):
            import os
            ts = os.path.getmtime(path)
            backups.append({"name": path.split("/")[-1], "ts": ts})
        result["backups"] = backups
    except Exception:
        pass
    return result


def probe_cloudflared():
    """Check cloudflared binary, config, unit, tunnel list."""
    import os
    result = {
        "installed": os.path.isfile("/usr/local/bin/cloudflared"),
        "version": None,
        "config_exists": os.path.isfile(os.path.expanduser("~/.cloudflared/config.yml")),
        "unit_state": "unknown",
        "tunnels": "",
    }
    if result["installed"]:
        try:
            _, out, _ = _run(["cloudflared", "--version"])
            result["version"] = out.strip().split("\n")[0]
        except Exception:
            pass
        try:
            _, out, _ = _run(["systemctl", "show", "cloudflared.service",
                               "--property=ActiveState"])
            kv = parse_unit_show(out)
            result["unit_state"] = kv.get("active", "unknown")
        except Exception:
            pass
        try:
            rc, out, err = _run(["cloudflared", "tunnel", "list"], timeout=8)
            result["tunnels"] = out.strip() if rc == 0 else "not authenticated"
        except Exception:
            result["tunnels"] = "not authenticated"
    return result


def parse_ufw(text):
    """Pure: parse `ufw status verbose` output.

    Returns {"present": bool, "active": bool, "rules": [str]}.
    present=False / active=False / rules=[] when text is empty or missing.
    """
    if not text or not text.strip():
        return {"present": False, "active": False, "rules": []}

    lines = text.splitlines()
    # Check for "Status: active" or "Status: inactive"
    active = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("status:"):
            status_val = stripped[len("status:"):].strip().lower()
            if status_val == "active":
                active = True
                break
            elif status_val == "inactive":
                active = False
                break

    # Collect rule lines: lines after the header separator (line starting with "--")
    # The separator in ufw output looks like "--    ------    ----"
    rules = []
    in_rules = False
    for line in lines:
        stripped = line.strip()
        # Detect separator: a line composed entirely of dashes and whitespace.
        # ufw emits "--  ------  ----"; require a "--" prefix so a stray
        # single-dash line can't be mistaken for the header rule.
        if not in_rules and stripped and all(c in "- " for c in stripped) and stripped.startswith("--"):
            in_rules = True
            continue
        if in_rules and stripped:
            rules.append(stripped)

    return {"present": True, "active": active, "rules": rules}


def probe_firewall():
    """Probe ufw status; fall back to iptables -S if ufw missing/rc!=0.

    Returns {"present": bool, "active": bool|None, "rules": [str]}.
    Never raises.
    """
    try:
        rc, out, _err = _run(["sudo", "-n", "ufw", "status", "verbose"])
        if rc == 0 and out.strip():
            return parse_ufw(out)
        # ufw absent or failed — fall back to iptables
        try:
            rc2, out2, _err2 = _run(["sudo", "-n", "iptables", "-S"])
            rules = [line.strip() for line in out2.splitlines()[:40] if line.strip()]
            return {"present": False, "active": None, "rules": rules}
        except Exception:
            return {"present": False, "active": None, "rules": []}
    except Exception:
        return {"present": False, "active": False, "rules": []}


def probe_certs():
    """TLS cert days_left for nova.ahb123.com."""
    import ssl
    import socket
    hosts = ["nova.ahb123.com"]
    out = []
    for host in hosts:
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=4) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                    cert = ssock.getpeercert()
            not_after = cert.get("notAfter", "")
            # format: "Jul  2 12:00:00 2027 GMT"
            import datetime
            exp = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            days_left = (exp - now).days
            out.append({"host": host, "days_left": days_left, "ok": days_left > 14})
        except Exception as e:
            out.append({"host": host, "days_left": None, "ok": False, "err": str(e)})
    return out


def _http_status(url, timeout=5):
    """GET or HEAD request to url; return HTTP status code.

    Tries HEAD first; if 405 (Method Not Allowed), retries with GET.
    Returns status code, or None on other errors.
    """
    import urllib.request
    try:
        # Try HEAD first
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "baza-probe/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        if e.code == 405:
            # Method not allowed; retry with GET
            try:
                req = urllib.request.Request(url, method="GET",
                                             headers={"User-Agent": "baza-probe/1"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.status
            except urllib.error.HTTPError as e2:
                return e2.code
            except Exception:
                return None
        else:
            return e.code
    except Exception:
        return None


def probe_reachability():
    """HEAD-request ahb123.com, nova.ahb123.com/health, baza.ahb123.com; cached 60s.

    For GET-only routes that return 405 on HEAD, automatically retries with GET.
    """
    global _reach_cache
    now = time.time()
    if _reach_cache and (now - _reach_cache[0]) < 60:
        return _reach_cache[1]
    urls = ["https://ahb123.com", "https://nova.ahb123.com/health", "https://baza.ahb123.com"]
    results = []
    for url in urls:
        try:
            status = _http_status(url, timeout=5)
            if status is None:
                results.append({"url": url, "status": None, "ok": False, "err": "connection failed"})
            else:
                results.append({"url": url, "status": status, "ok": status < 400})
        except Exception as e:
            results.append({"url": url, "status": None, "ok": False, "err": str(e)})
    _reach_cache = (now, results)
    return results



# ───────────── edge chain builder (pure) ─────────────

def build_edges(dns, wan_ip, caddy, cloudflared, listeners, tailscale):
    """Return list of 4 chain dicts: {chain, hops: [{label, ok, detail}]}."""
    listen_ports = {l["port"] for l in listeners}
    listen_by_port = {l["port"]: l for l in listeners}

    def _dns_verdict_for(name, rtype):
        for v in dns:
            if v["name"] == name and v["rtype"] == rtype:
                return v
        return None

    # ── 1. ahb123.com ─────────────────────────────────────────────────────
    apex_v = _dns_verdict_for("ahb123.com", "A")
    apex_hop_ok = apex_v["ok"] if apex_v else None

    # reach result for ahb123.com (may not be passed; use None for pure tests)
    sq_hop = {"label": "Squarespace", "ok": None, "detail": "not probed in this call"}

    chain_apex = {
        "chain": "ahb123.com",
        "hops": [
            {"label": "DNS apex (A)", "ok": apex_hop_ok,
             "detail": f"expected Squarespace IPs, actual {apex_v['actual'] if apex_v else []}"},
            sq_hop,
        ],
    }

    # ── 2. nova.ahb123.com ────────────────────────────────────────────────
    nova_a = _dns_verdict_for("nova.ahb123.com", "A")
    nova_dns_ok = nova_a["ok"] if nova_a else None

    # router-forward: reach nova (None in pure test context)
    router_hop = {"label": "router-forward (reach nova)", "ok": None, "detail": "not probed in this call"}

    # Caddy active + 443 listener on 192.168.1.68
    caddy_ok = False
    caddy_detail = "caddy not active"
    if caddy.get("active"):
        if any(l["port"] == 443 and l.get("addr", "") in ("192.168.1.68", "0.0.0.0", "*") for l in listeners):
            caddy_ok = True
            caddy_detail = "caddy active, :443 listening"
        else:
            caddy_detail = "caddy active but :443 not found in listeners"
    caddy_hop = {"label": "Caddy@.68 (:443)", "ok": caddy_ok, "detail": caddy_detail}

    # upstreams :8000 / :8888 listening
    nova_upstreams = []
    for site in caddy.get("sites", []):
        if site["host"] == "nova.ahb123.com":
            nova_upstreams = site.get("upstreams", [])
    up_ports = []
    for up in nova_upstreams:
        m = re.search(r":(\d+)$", up)
        if m:
            up_ports.append(int(m.group(1)))
    if not up_ports:
        up_ports = [8888, 8000]
    all_up_ok = all(p in listen_ports for p in up_ports)
    up_hop = {
        "label": f"upstreams {up_ports}",
        "ok": all_up_ok if up_ports else None,
        "detail": f"listening: {[p for p in up_ports if p in listen_ports]} / missing: {[p for p in up_ports if p not in listen_ports]}",
    }

    chain_nova = {
        "chain": "nova.ahb123.com",
        "hops": [
            {"label": "deSEC A → WAN", "ok": nova_dns_ok,
             "detail": f"actual {nova_a['actual'] if nova_a else []}, expected WAN {wan_ip}"},
            router_hop,
            caddy_hop,
            up_hop,
        ],
    }

    # ── 3. baza.ahb123.com ────────────────────────────────────────────────
    if not cloudflared.get("config_exists"):
        chain_baza = {
            "chain": "baza.ahb123.com",
            "hops": [
                {"label": "planned — run Migration wizard", "ok": None,
                 "detail": "cloudflared config not found; tunnel not provisioned yet"},
            ],
        }
    else:
        cf_active = cloudflared.get("unit_state") == "active"
        cf_hop = {"label": "cloudflared tunnel", "ok": cf_active,
                  "detail": cloudflared.get("tunnels", "")}
        cf_reach_hop = {"label": "baza.ahb123.com reach", "ok": None, "detail": "not probed in this call"}
        chain_baza = {
            "chain": "baza.ahb123.com",
            "hops": [cf_hop, cf_reach_hop],
        }

    # ── 4. ts.net (Tailscale serve mappings) ──────────────────────────────
    ts_hops = []
    for serve in tailscale.get("serves", []):
        listen_port_str = serve.get("listen", "443")
        target = serve.get("target", "")
        m = re.search(r":(\d+)$", target)
        target_port = int(m.group(1)) if m else None
        target_ok = (target_port in listen_ports) if target_port else None
        ts_hops.append({
            "label": f"serve :{listen_port_str} → {target}",
            "ok": target_ok,
            "detail": f"target port {target_port} {'listening' if target_ok else 'NOT listening'}",
        })
    if not ts_hops:
        ts_hops = [{"label": "no serve mappings configured", "ok": None, "detail": ""}]

    chain_ts = {"chain": "ts.net", "hops": ts_hops}

    return [chain_apex, chain_nova, chain_baza, chain_ts]


# ───────────── settings registry (pure) ─────────────

_SECRET_WORDS = re.compile(r'(SECRET|TOKEN|KEY|PASSWORD|PASS|PWD|CRED)', re.IGNORECASE)
_URL_LIKE = re.compile(r'^https?://', re.IGNORECASE)
# Bare host[:port][/path]: must have a dot (domain) or be localhost, then optional :port and /path
_SAFE_HOST = re.compile(r'^(localhost|[A-Za-z0-9]([A-Za-z0-9.\-]*[A-Za-z0-9])?\.([A-Za-z0-9\-]+\.)*[A-Za-z0-9\-]+)(?::\d+)?(?:/[\w./\-]*)?$', re.IGNORECASE)


def _mask_env_value(key, value):
    """Mask env value unless it looks safe-by-default.

    Safe (shown in full): URL-ish (matches ^https?://), bare host[:port][/path],
    purely numeric (port), or boolean (true/false/on/off).
    Always masked if key contains secret-word (belt and suspenders).
    Everything else is masked to ***.
    """
    # Always mask if key looks like a secret
    if _SECRET_WORDS.search(key):
        return "***"

    value_str = str(value) if value is not None else ""

    # Safe patterns
    if _URL_LIKE.match(value_str):  # https://... or http://...
        return value_str
    if _SAFE_HOST.match(value_str):  # localhost or domain.name[:port][/path]
        return value_str
    if value_str.isdigit():  # purely numeric (port)
        return value_str
    if value_str.lower() in ("true", "false", "on", "off", "yes", "no"):  # boolean
        return value_str

    # Default: mask everything else
    return "***"


def settings_registry(status, facts, env=None):
    """Pure assembler: status dict + facts list + optional env dict → registry rows.

    Each row: {group, key, value, source, edit}.
    edit=True means the row is a manual_fact (editable via /api/network/facts).
    edit=False means read-only.  edit=str is a hint string (e.g. "opens Caddy editor").
    """
    rows = []

    # ── caddy ──
    caddy = status.get("caddy") or {}
    rows.append({
        "group": "caddy", "key": "caddyfile",
        "value": "/etc/caddy/Caddyfile" if caddy.get("active") else "/etc/caddy/Caddyfile (inactive)",
        "source": "filesystem", "edit": "opens Caddy editor",
    })
    for site in caddy.get("sites") or []:
        host = site.get("host", "")
        bind = site.get("bind") or "(any)"
        upstreams = ", ".join(site.get("upstreams") or [])
        rows.append({
            "group": "caddy", "key": f"site:{host}",
            "value": f"bind={bind} → {upstreams}" if upstreams else f"bind={bind}",
            "source": "Caddyfile", "edit": "opens Caddy editor",
        })

    # ── cloudflared ──
    cf = status.get("cloudflared") or {}
    if cf.get("config_exists"):
        cfg_path = "~/.cloudflared/config.yml"
    else:
        cfg_path = "absent"
    rows.append({
        "group": "cloudflared", "key": "config",
        "value": cfg_path,
        "source": "filesystem", "edit": "opens cloudflared wizard",
    })
    rows.append({
        "group": "cloudflared", "key": "unit_state",
        "value": cf.get("unit_state") or "unknown",
        "source": "systemd", "edit": False,
    })

    # ── tailscale ──
    ts = status.get("tailscale") or {}
    serves = ts.get("serves") or []
    if serves:
        for sv in serves:
            rows.append({
                "group": "tailscale", "key": f"serve:{sv.get('listen','')}",
                "value": sv.get("target", ""),
                "source": "tailscale serve status", "edit": False,
            })
    else:
        rows.append({
            "group": "tailscale", "key": "serve",
            "value": "no mappings configured",
            "source": "tailscale serve status", "edit": False,
        })
    self_ = ts.get("self") or {}
    rows.append({
        "group": "tailscale", "key": "exit_node",
        "value": str(bool(self_.get("exit_node", False))),
        "source": "tailscale status", "edit": False,
    })

    # ── ddns ──
    services = status.get("services") or {}
    for key_suffix, unit in [("service", "baza-ddns.service"), ("timer", "baza-ddns.timer")]:
        svc = services.get(unit) or {}
        rows.append({
            "group": "ddns", "key": f"ddns.{key_suffix}",
            "value": svc.get("active") or "unknown",
            "source": "systemd", "edit": False,
        })

    # ── env ──
    if env:
        _RELEVANT = re.compile(r'^(BAZA_|NOVA_|CADDY_|SECRET|API_KEY|TOKEN)', re.IGNORECASE)
        for k, v in env.items():
            if _RELEVANT.match(k):
                rows.append({
                    "group": "env", "key": k,
                    "value": _mask_env_value(k, str(v)),
                    "source": ".env", "edit": False,
                })

    # ── router (manual_facts) ──
    for fact in (facts or []):
        rows.append({
            "group": "router", "key": fact["key"],
            "value": fact["value"],
            "source": "manual_facts",
            "edit": True,
            "note": fact.get("note", ""),
        })

    return rows


# ───────────── aggregator ─────────────

def status():
    """Assemble all probe results into a single dict for the Network tab route."""
    ifaces = probe_interfaces()
    ts = probe_tailscale()
    services = probe_services()
    listeners = probe_listeners()
    wan_ip = probe_wan_ip()
    dns = probe_dns()
    caddy = probe_caddy()
    cloudflared_info = probe_cloudflared()
    certs = probe_certs()
    reach = probe_reachability()
    firewall = probe_firewall()

    # Inject reach results into edges builder for ahb123.com + nova hops
    edges = _build_edges_with_reach(dns, wan_ip, caddy, cloudflared_info, listeners, ts, reach)

    return {
        "interfaces": ifaces,
        "tailscale": ts,
        "services": services,
        "listeners": listeners,
        "wan_ip": wan_ip,
        "dns": dns,
        "caddy": caddy,
        "cloudflared": cloudflared_info,
        "certs": certs,
        "reach": reach,
        "edges": edges,
        "firewall": firewall,
        "known_ports": KNOWN_PORTS,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def _build_edges_with_reach(dns, wan_ip, caddy, cloudflared, listeners, tailscale, reach):
    """build_edges extended with live reach data stitched into apex/nova hops."""
    edges = build_edges(dns, wan_ip, caddy, cloudflared, listeners, tailscale)
    reach_by_url = {r["url"]: r for r in reach}

    for chain in edges:
        if chain["chain"] == "ahb123.com":
            sq = reach_by_url.get("https://ahb123.com")
            if sq:
                chain["hops"][1]["ok"] = sq["ok"]
                chain["hops"][1]["detail"] = f"HTTP {sq.get('status')}"
        elif chain["chain"] == "nova.ahb123.com":
            nova_r = reach_by_url.get("https://nova.ahb123.com/health")
            if nova_r:
                chain["hops"][1]["ok"] = nova_r["ok"]
                chain["hops"][1]["detail"] = f"HTTP {nova_r.get('status')}"
        elif chain["chain"] == "baza.ahb123.com":
            if cloudflared.get("config_exists"):
                baza_r = reach_by_url.get("https://baza.ahb123.com")
                if baza_r and len(chain["hops"]) > 1:
                    chain["hops"][1]["ok"] = baza_r["ok"]
                    chain["hops"][1]["detail"] = f"HTTP {baza_r.get('status')}"
    return edges
