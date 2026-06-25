#!/usr/bin/env python3
"""baza DDNS — keep nova.ahb123.com's A record at this box's WAN IP, via deSEC.

Why: nova.ahb123.com is self-hosted on baza behind Caddy, but the residential
WAN IP rotates (drifted 71.175.76.97 -> 96.227.96.20 and silently broke nova on
2026-06-06). The domain's registrar (Squarespace) has no DNS API / no DDNS, so
authority for *just* nova.ahb123.com is delegated (NS records at Squarespace) to
deSEC.io, which has a clean API. This watcher updates the deSEC A record only
when the WAN IP actually changed. See memory: project_nova_caddy_dynamic_ip.

Config (root-only env file): /etc/baza-ddns/ddns.env
  DESEC_DOMAIN=nova.ahb123.com     # the zone created in deSEC (delegated subdomain)
  DESEC_TOKEN=<deSEC API token>
  SUBNAME=                         # '' = apex of DESEC_DOMAIN (i.e. nova.ahb123.com itself)
  TTL=60
  # optional: IP_SERVICES=comma,separated,https urls returning a bare IPv4

Run:  baza-ddns.service (oneshot) on baza-ddns.timer (every 5 min).
Exit: 0 = no change or updated OK; non-zero = error (timer retries next tick).
Safety: needs >=2 WAN-IP sources to agree before writing; only ever touches the
single configured SUBNAME/type=A rrset in the delegated deSEC zone.
"""
from __future__ import annotations
import json
import os
import sys
import ipaddress
import urllib.request
import urllib.error

CONF = "/etc/baza-ddns/ddns.env"
DESEC_API = "https://desec.io/api/v1"
DEFAULT_IP_SERVICES = [
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
    "https://ipv4.icanhazip.com",
]


def log(msg: str) -> None:
    print(f"[baza-ddns] {msg}", flush=True)


def die(msg: str, code: int = 1):
    log(f"ERROR: {msg}")
    sys.exit(code)


def load_conf(path: str) -> dict:
    if not os.path.exists(path):
        die(f"config {path} not found — not configured yet")
    conf = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            conf[k.strip()] = v.strip().strip('"').strip("'")
    for req in ("DESEC_DOMAIN", "DESEC_TOKEN"):
        if not conf.get(req):
            die(f"config missing required key: {req}")
    conf.setdefault("SUBNAME", "")
    conf.setdefault("TTL", "60")
    return conf


def http_get(url: str, headers: dict | None = None, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode().strip()


def detect_wan_ip(services: list[str]) -> str:
    """Return the WAN IPv4 only if >=2 independent services agree."""
    votes: dict[str, int] = {}
    for url in services:
        try:
            ip = http_get(url).strip()
            ipaddress.IPv4Address(ip)  # validates; raises on garbage/IPv6
            if not ipaddress.ip_address(ip).is_global:
                log(f"  {url} -> {ip} (non-public, ignored)")
                continue
            votes[ip] = votes.get(ip, 0) + 1
            log(f"  {url} -> {ip}")
        except Exception as e:
            log(f"  {url} -> failed ({e.__class__.__name__})")
    if not votes:
        die("could not determine WAN IP from any source")
    ip, n = max(votes.items(), key=lambda kv: kv[1])
    if n < 2:
        die(f"WAN IP sources did not reach consensus ({votes}) — refusing to write")
    return ip


def desec(method: str, path: str, token: str, body=None) -> tuple[int, object]:
    url = f"{DESEC_API}/{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Token {token}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 404, None  # let callers treat "absent rrset" as a normal state
        detail = e.read().decode(errors="replace")
        die(f"deSEC API {method} {path} -> HTTP {e.code}: {detail}")


def current_a(conf: dict) -> list[str] | None:
    sub = conf["SUBNAME"] or "@"  # deSEC addresses the apex rrset as subname '@' in the URL
    status, body = desec(
        "GET",
        f"domains/{conf['DESEC_DOMAIN']}/rrsets/{sub}/A/",
        conf["DESEC_TOKEN"],
    )
    if status == 404 or body is None:
        return None
    return body.get("records")


def set_a(conf: dict, ip: str, exists: bool):
    sub = conf["SUBNAME"] or "@"
    payload = {"subname": conf["SUBNAME"], "type": "A", "ttl": int(conf["TTL"]),
               "records": [ip]}
    if exists:
        desec("PATCH", f"domains/{conf['DESEC_DOMAIN']}/rrsets/{sub}/A/",
              conf["DESEC_TOKEN"], payload)
    else:
        desec("POST", f"domains/{conf['DESEC_DOMAIN']}/rrsets/",
              conf["DESEC_TOKEN"], payload)


def main() -> int:
    conf = load_conf(CONF)
    services = [s.strip() for s in conf.get("IP_SERVICES", "").split(",") if s.strip()]
    services = services or DEFAULT_IP_SERVICES
    fqdn = (conf["SUBNAME"] + "." if conf["SUBNAME"] else "") + conf["DESEC_DOMAIN"]

    wan_ip = detect_wan_ip(services)
    existing = current_a(conf)
    cur = existing[0] if existing else None

    if cur == wan_ip:
        log(f"no change — {fqdn} already {wan_ip}")
        return 0

    log(f"updating {fqdn}: {cur or '(none)'} -> {wan_ip} (ttl {conf['TTL']})")
    set_a(conf, wan_ip, exists=existing is not None)
    log("update submitted OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
