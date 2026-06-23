#!/usr/bin/env python3
"""baza DDNS — keep a Google Cloud DNS A record pointed at this box's WAN IP.

Why this exists: nova.ahb123.com is self-hosted on baza behind Caddy, but the
WAN IP is a residential dynamic address that rotates (it drifted 71.175.76.97 →
96.227.96.20 and silently broke nova on 2026-06-06). This watcher detects the
current WAN IP and, only when it changed, updates the A record via the Cloud DNS
API. See memory: project_nova_caddy_dynamic_ip.

Config (env file, root-only): /etc/baza-ddns/ddns.env
  GCP_PROJECT=<gcp project id hosting the Cloud DNS zone>
  GCP_ZONE=<managed-zone NAME (the GCP resource name, not the domain)>
  RECORD=nova.ahb123.com.          # FQDN with trailing dot
  TTL=300
  SA_KEY=/etc/baza-ddns/sa-key.json
  # optional: IP_SERVICES=comma,separated,https urls returning a bare IP

Run:  baza-ddns.service (oneshot) on baza-ddns.timer (every 5 min).
Exit: 0 = no change or updated OK; non-zero = error (timer retries next tick).
Safety: only ever touches the single configured RECORD/type=A. Requires >=2
WAN-IP sources to agree before writing, so a flaky echo service can't poison DNS.
"""
from __future__ import annotations
import json
import os
import sys
import ipaddress
import urllib.request
import urllib.error

CONF = "/etc/baza-ddns/ddns.env"
DNS_API = "https://dns.googleapis.com/dns/v1"
SCOPE = "https://www.googleapis.com/auth/ndev.clouddns.readwrite"
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
    for req in ("GCP_PROJECT", "GCP_ZONE", "RECORD", "SA_KEY"):
        if not conf.get(req):
            die(f"config missing required key: {req}")
    conf.setdefault("TTL", "300")
    if not conf["RECORD"].endswith("."):
        conf["RECORD"] += "."
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
            ipaddress.IPv4Address(ip)  # validates + raises on garbage/IPv6
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


def get_token(sa_key_path: str) -> str:
    if not os.path.exists(sa_key_path):
        die(f"service-account key {sa_key_path} not found")
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests as gar
    except Exception:
        die("google-auth not importable — is /opt/baza-ddns/venv set up?")
    creds = service_account.Credentials.from_service_account_file(
        sa_key_path, scopes=[SCOPE]
    )
    creds.refresh(gar.Request())
    return creds.token


def api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = f"{DNS_API}/{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        die(f"Cloud DNS API {method} {path} -> HTTP {e.code}: {detail}")


def current_a_record(conf: dict, token: str) -> dict | None:
    res = api(
        "GET",
        f"projects/{conf['GCP_PROJECT']}/managedZones/{conf['GCP_ZONE']}"
        f"/rrsets?name={conf['RECORD']}&type=A",
        token,
    )
    sets = res.get("rrsets", [])
    return sets[0] if sets else None


def update_record(conf: dict, token: str, new_ip: str, existing: dict | None):
    new_rrset = {
        "name": conf["RECORD"],
        "type": "A",
        "ttl": int(conf["TTL"]),
        "rrdatas": [new_ip],
    }
    change = {"additions": [new_rrset]}
    if existing:
        change["deletions"] = [existing]  # must match exactly to delete
    api(
        "POST",
        f"projects/{conf['GCP_PROJECT']}/managedZones/{conf['GCP_ZONE']}/changes",
        token,
        change,
    )


def main() -> int:
    conf = load_conf(CONF)
    services = [s.strip() for s in conf.get("IP_SERVICES", "").split(",") if s.strip()]
    services = services or DEFAULT_IP_SERVICES

    wan_ip = detect_wan_ip(services)
    token = get_token(conf["SA_KEY"])
    existing = current_a_record(conf, token)
    cur = existing["rrdatas"][0] if (existing and existing.get("rrdatas")) else None

    if cur == wan_ip:
        log(f"no change — {conf['RECORD']} already {wan_ip}")
        return 0

    log(f"updating {conf['RECORD']}: {cur or '(none)'} -> {wan_ip} (ttl {conf['TTL']})")
    update_record(conf, token, wan_ip, existing)
    log("update submitted OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
