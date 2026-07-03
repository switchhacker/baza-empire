#!/usr/bin/env python3
"""Claw Batto — dns_cert_watch cron (cron-improvements plan, item 25 / Task 13).

DNS drift, TLS cert expiry, and liveness checks for ahb123.com,
nova.ahb123.com, and baza.ahb123.com.

Checks (each a pure function taking injected fetchers, so tests never touch
the real network):
  1. check_dns_resolution — A-record lookup via socket.getaddrinfo for all
     three hosts; empty result -> problem.
  2. check_nova_drift — nova.ahb123.com's A record vs this box's current WAN
     IP; mismatch -> problem "nova DNS drift: A=<a> WAN=<wan>". nova is
     self-hosted behind a residential connection with a rotating WAN IP and
     no DNS-API-capable registrar for the apex domain, so deSEC (delegated
     subdomain) has to be kept in sync by scripts/baza_ddns_update.py
     (baza-ddns.service/.timer) — this check is the read-only watchdog for
     when that sync has fallen behind. See memory: project_nova_caddy_dynamic_ip.
  3. check_cert_expiry — TLS cert expiry via an ssl handshake on :443
     (ssl.create_default_context().wrap_socket, getpeercert()["notAfter"]).
     <14 days left -> problem. A handshake failure is a problem for most
     hosts, but info-only for baza.ahb123.com, whose Cloudflare tunnel is
     still pending setup (see memory: project_cloudflare_tunnel_domain) —
     a failed handshake there is expected, not actionable, until that lands.
  4. check_liveness — GET https://nova.ahb123.com, expecting status < 500.

Problems -> send_alert(key=f"dnswatch:{host}:{check}", renotify_hours=6) so
a standing issue (e.g. the known-live nova drift) doesn't re-page every run.

WAN-IP lookup reuses the approach from scripts/baza_ddns_update.py (the
script behind baza-ddns.service/.timer): try a short list of plain-text IP
echo services and use the first one that returns a value, falling back to
https://api.ipify.org (first in the list, and the sole fallback called out
in the task brief). Unlike baza_ddns_update.py's detect_wan_ip() — which
requires 2-of-N source agreement because it's about to *write* a DNS record
— this is a read-only comparison, so first-success is enough (mirrors
dashboard/network_probe.py's probe_wan_ip(), which does the same drift
comparison for the network dashboard).
"""
import concurrent.futures
import datetime
import logging
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *  # noqa: F401,F403 (log, now, cron_run, send_alert, TELEGRAM_TOKEN, ...)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLAW-DNS] %(message)s")

CRON_NAME = "dns_cert_watch"
AGENT_TOKEN = os.getenv("TELEGRAM_CLAW_BATTO", TELEGRAM_TOKEN)
USER_AGENT = "baza-empire/1.0 (contactahbco@gmail.com)"

HOSTS = ["ahb123.com", "nova.ahb123.com", "baza.ahb123.com"]
DRIFT_HOST = "nova.ahb123.com"
LIVENESS_URL = "https://nova.ahb123.com"
CERT_WARN_DAYS = 14
RENOTIFY_HOURS = 6

# Same service list scripts/baza_ddns_update.py uses (DEFAULT_IP_SERVICES),
# minus the deSEC-consensus requirement — see module docstring.
WAN_IP_SERVICES = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)

# baza.ahb123.com's Cloudflare tunnel is pending setup — a TLS handshake
# failure there is expected right now, so it's reported as info, not alerted.
HANDSHAKE_INFO_ONLY_HOSTS = {"baza.ahb123.com"}

# socket.getaddrinfo has no native timeout param, so default_resolver bounds
# it itself via a thread + future timeout (see below). A module-level global
# (read at call time, not bound as a default-arg value) so tests can shrink
# it with monkeypatch.setattr(module, "RESOLVER_TIMEOUT_SECONDS", ...) to
# keep a hanging-resolver test fast without touching real network timing.
RESOLVER_TIMEOUT_SECONDS = 10


# ── injected fetchers (real implementations; tests swap these) ─────────────

def _resolve_addrinfo(host: str) -> list[str]:
    """The actual blocking socket.getaddrinfo call, run inside
    default_resolver's executor. Factored out so tests can monkeypatch just
    this (to simulate a hang) while leaving default_resolver's public
    (host) -> list[str] contract, and the injected-resolver seam used
    everywhere else in this module, untouched."""
    infos = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def default_resolver(host: str) -> list[str]:
    """Return the sorted, deduped list of IPv4 addresses host resolves to,
    or [] on any resolution failure (NXDOMAIN, timeout, a hang past
    RESOLVER_TIMEOUT_SECONDS, etc.).

    socket.getaddrinfo blocks with no way to pass a timeout, so the call is
    run in a worker thread and bounded via future.result(timeout=...); a
    stuck resolver degrades to an empty result exactly like any other
    resolution failure, rather than hanging the cron. The pool is shut down
    with wait=False so a still-hung worker thread doesn't itself block this
    call from returning.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_resolve_addrinfo, host)
        return future.result(timeout=RESOLVER_TIMEOUT_SECONDS)
    except Exception:
        return []
    finally:
        executor.shutdown(wait=False)


def default_wan_ip_getter() -> str | None:
    """First-success WAN IP lookup across WAN_IP_SERVICES. See module
    docstring for why this differs from baza_ddns_update.py's consensus
    version."""
    for url in WAN_IP_SERVICES:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                ip = resp.read().decode().strip()
            if ip:
                return ip
        except Exception:
            continue
    return None


def default_cert_fetcher(host: str, port: int = 443, timeout: int = 10) -> dict:
    """Return getpeercert() for host:port via a real TLS handshake. Raises
    on any connection/handshake failure — callers decide how to treat it."""
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            return ssock.getpeercert()


def default_http_prober(url: str, timeout: int = 10) -> int | None:
    """Return the HTTP status code for a GET on url. Connection-level
    failures (DNS, refused, timeout, TLS error, ...) return None rather than
    raising; an HTTP-level error status is still returned as a code."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


# ── pure check functions ────────────────────────────────────────────────────

def check_dns_resolution(hosts=HOSTS, resolver=default_resolver) -> list[dict]:
    """Check (1). Returns a list of problem dicts for hosts with no A record."""
    problems = []
    for host in hosts:
        if not resolver(host):
            problems.append({
                "host": host, "check": "resolve",
                "message": f"{host}: DNS resolution failed (no A record returned)",
            })
    return problems


def check_nova_drift(resolver=default_resolver, wan_ip_getter=default_wan_ip_getter,
                     host: str = DRIFT_HOST) -> dict | None:
    """Check (2). Returns a problem dict on A-vs-WAN mismatch, else None.

    Silent (None, no verdict) when either side is unavailable — an
    unresolvable host is check (1)'s problem to report, and an unknown WAN
    IP means there's nothing to compare against.
    """
    ips = resolver(host)
    if not ips:
        return None
    wan = wan_ip_getter()
    if not wan:
        return None
    if wan not in ips:
        a_repr = ips[0] if len(ips) == 1 else ",".join(ips)
        return {
            "host": host, "check": "drift",
            "message": f"nova DNS drift: A={a_repr} WAN={wan}",
        }
    return None


def check_cert_expiry(host: str, cert_fetcher=default_cert_fetcher,
                      warn_days: float = CERT_WARN_DAYS,
                      info_only_hosts=HANDSHAKE_INFO_ONLY_HOSTS,
                      now: datetime.datetime | None = None) -> dict | None:
    """Check (3). Returns {"host", "check": "cert", "level", "message"} or
    None when the cert is healthy (handshake OK, well outside warn_days).

    level is "problem" for handshake failures on any host not in
    info_only_hosts and for real expiry-window hits; "info" for handshake
    failures on hosts in info_only_hosts (e.g. baza.ahb123.com pre-tunnel).

    getpeercert()["notAfter"] is always GMT/UTC (RFC 5280), so the default
    `now` must be UTC too (made naive to compare against the naive datetime
    parsed from notAfter) -- comparing it against a local-time now() silently
    skews the expiry window by the box's UTC offset (~4h on this box).
    """
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    try:
        cert = cert_fetcher(host)
    except Exception as e:
        level = "info" if host in info_only_hosts else "problem"
        return {
            "host": host, "check": "cert", "level": level,
            "message": f"{host}: TLS handshake failed ({e})",
        }

    not_after_raw = (cert or {}).get("notAfter")
    if not not_after_raw:
        return {
            "host": host, "check": "cert", "level": "problem",
            "message": f"{host}: certificate has no notAfter field",
        }
    try:
        # ssl.cert_time_to_seconds parses notAfter's "%b %d %H:%M:%S %Y %Z"
        # format against a fixed English month table (unlike
        # datetime.strptime, whose %b is locale-dependent and would
        # mis-parse under a non-English locale); it also assumes/requires
        # UTC per RFC 5280, matching the UTC `now` above.
        expires = datetime.datetime.fromtimestamp(
            ssl.cert_time_to_seconds(not_after_raw), tz=datetime.timezone.utc
        ).replace(tzinfo=None)
    except ValueError:
        return {
            "host": host, "check": "cert", "level": "problem",
            "message": f"{host}: could not parse cert expiry {not_after_raw!r}",
        }

    days_left = (expires - now).total_seconds() / 86400
    if days_left < warn_days:
        return {
            "host": host, "check": "cert", "level": "problem",
            "message": f"{host}: TLS cert expires in {days_left:.1f} days ({not_after_raw})",
        }
    return None


def check_liveness(url: str = LIVENESS_URL, http_prober=default_http_prober,
                   host: str = DRIFT_HOST) -> dict | None:
    """Check (4). Returns a problem dict on connection failure or a >=500
    response, else None."""
    status = http_prober(url)
    if status is None:
        return {
            "host": host, "check": "liveness",
            "message": f"{url}: unreachable (request failed)",
        }
    if status >= 500:
        return {
            "host": host, "check": "liveness",
            "message": f"{url}: HTTP {status} (server error)",
        }
    return None


# ── orchestration ────────────────────────────────────────────────────────

def run_checks(hosts=HOSTS, resolver=default_resolver, wan_ip_getter=default_wan_ip_getter,
               cert_fetcher=default_cert_fetcher, http_prober=default_http_prober):
    """Run all four checks. Returns (problems, infos) — lists of dicts."""
    problems = list(check_dns_resolution(hosts, resolver))

    drift = check_nova_drift(resolver=resolver, wan_ip_getter=wan_ip_getter)
    if drift:
        problems.append(drift)

    infos = []
    for host in hosts:
        cert_result = check_cert_expiry(host, cert_fetcher=cert_fetcher)
        if cert_result:
            (problems if cert_result["level"] == "problem" else infos).append(cert_result)

    live = check_liveness(http_prober=http_prober)
    if live:
        problems.append(live)

    return problems, infos


def send_problems(problems, cron_name: str = CRON_NAME, token: str | None = None,
                  renotify_hours: float = RENOTIFY_HOURS) -> list[bool]:
    """Alert on each problem via send_alert, deduped per host+check for
    renotify_hours. Returns the list of send_alert() return values."""
    tok = token or AGENT_TOKEN
    results = []
    for p in problems:
        key = f"dnswatch:{p['host']}:{p['check']}"
        message = f"\U0001f310 DNS/CERT WATCH\n\n{p['message']}"
        results.append(send_alert(cron_name, message, key,
                                  renotify_hours=renotify_hours, token=tok))
    return results


def main():
    with cron_run(CRON_NAME):
        log.info("Starting dns_cert_watch...")
        problems, infos = run_checks(HOSTS, default_resolver, default_wan_ip_getter,
                                     default_cert_fetcher, default_http_prober)

        for info in infos:
            log.info(f"INFO (not alerted): {info['message']}")

        send_problems(problems)

        if problems:
            log.info(f"{len(problems)} problem(s) found:")
            for p in problems:
                log.info(f"  [{p['check']}] {p['message']}")
        else:
            log.info("All checks green.")
        log.info(f"Done. {len(problems)} problem(s), {len(infos)} info item(s).")


if __name__ == "__main__":
    main()
