#!/usr/bin/env python3
"""Get all IP addresses, interfaces, Tailscale status."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
info = {"interfaces": [], "tailscale": None}

try:
    result = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=10)
    ifaces = json.loads(result.stdout)
    for iface in ifaces:
        addrs = []
        for a in iface.get("addr_info", []):
            addrs.append({"ip": a.get("local", ""), "prefix": a.get("prefixlen", ""), "family": a.get("family", "")})
        if addrs:
            info["interfaces"].append({"name": iface.get("ifname", ""), "state": iface.get("operstate", ""), "addresses": addrs})
except Exception:
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        info["interfaces"].append({"ips": result.stdout.strip().split()})
    except Exception:
        pass

try:
    result = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
    ts = json.loads(result.stdout)
    info["tailscale"] = {"self": ts.get("Self", {}).get("TailscaleIPs", []), "online": ts.get("Self", {}).get("Online", False)}
except Exception:
    info["tailscale"] = {"status": "not_available"}

print(json.dumps(info))
