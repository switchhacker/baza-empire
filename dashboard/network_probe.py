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
