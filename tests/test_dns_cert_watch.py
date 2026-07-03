"""Tests for agents/claw_batto/crons/dns_cert_watch.py (Task 13 of the
cron-improvements plan): DNS drift, TLS cert expiry, and liveness checks for
ahb123.com / nova.ahb123.com / baza.ahb123.com.

All network access (DNS resolution, WAN-IP lookup, TLS handshake, HTTP GET)
is provided via injected fetchers — nothing here touches the real network.
cron_health_db (used by cron_run/send_alert under the hood) is pointed at a
tmp-file DB per test, mirroring the fixture pattern in
tests/test_cron_helpers_routing.py.
"""
import datetime
import importlib
import os
import sys
import tempfile
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


@pytest.fixture()
def watch(monkeypatch):
    """Fresh agents.claw_batto.crons.dns_cert_watch (+ its cron_helpers /
    cron_health_db deps), DB pointed at a tmp file so alert-dedup state never
    touches the real dashboard/cron_health.db."""
    tmpdir = tempfile.mkdtemp(prefix="dns_cert_watch_")
    path = os.path.join(tmpdir, "cron_health.db")
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", path)

    for mod in ("core.cron_health_db", "agents.cron_helpers",
                "agents.claw_batto.crons.dns_cert_watch"):
        if mod in sys.modules:
            del sys.modules[mod]

    chdb = importlib.import_module("core.cron_health_db")
    chdb.init()
    return importlib.import_module("agents.claw_batto.crons.dns_cert_watch")


@pytest.fixture()
def alert_recorder(monkeypatch, watch):
    """Recorder standing in for watch.send_alert (imported into the module's
    namespace via `from agents.cron_helpers import *`); replacing it here
    bypasses cron_health_db's dedup logic entirely so tests only assert on
    what dns_cert_watch itself decided to send."""
    calls = []

    def fake_send_alert(cron_name, message, alert_key, renotify_hours=None,
                        buttons=True, token=None, chat_id=None):
        calls.append({
            "cron_name": cron_name, "message": message, "alert_key": alert_key,
            "renotify_hours": renotify_hours, "token": token,
        })
        return True

    monkeypatch.setattr(watch, "send_alert", fake_send_alert)
    return calls


def _cert(days_from_now):
    """A fake getpeercert() dict expiring `days_from_now` days from now.

    notAfter is always GMT/UTC (RFC 5280) -- built from
    datetime.now(timezone.utc), not local now(), so this fixture stays
    correct (and its "GMT" label stays true) on a box whose local time isn't
    UTC. See test_cert_expiry_default_now_is_utc_not_local for a test that
    would actually catch a UTC/local regression; the boundary tests using
    this helper just need consistent units, not a live UTC-vs-local
    difference.
    """
    expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days_from_now)
    return {"notAfter": expires.strftime("%b %d %H:%M:%S %Y GMT")}


# ── check_nova_drift ─────────────────────────────────────────────────────

def test_drift_detected(watch):
    result = watch.check_nova_drift(
        resolver=lambda host: ["96.227.96.20"],
        wan_ip_getter=lambda: "71.175.76.97",
    )
    assert result is not None
    assert result["host"] == "nova.ahb123.com"
    assert result["check"] == "drift"
    assert result["message"] == "nova DNS drift: A=96.227.96.20 WAN=71.175.76.97"


def test_drift_not_detected_when_matching(watch):
    result = watch.check_nova_drift(
        resolver=lambda host: ["71.175.76.97"],
        wan_ip_getter=lambda: "71.175.76.97",
    )
    assert result is None


def test_drift_silent_when_wan_unknown(watch):
    """If the WAN-IP lookup itself fails, there's nothing to compare against
    -- must not report a false drift."""
    result = watch.check_nova_drift(
        resolver=lambda host: ["71.175.76.97"],
        wan_ip_getter=lambda: None,
    )
    assert result is None


def test_drift_silent_when_unresolvable(watch):
    """An unresolvable nova host is check (1)'s problem, not a drift verdict."""
    result = watch.check_nova_drift(
        resolver=lambda host: [],
        wan_ip_getter=lambda: "71.175.76.97",
    )
    assert result is None


# ── check_cert_expiry: expiry window ────────────────────────────────────

def test_cert_expiry_window(watch):
    """A cert with < 14 days left is a problem; one with plenty of runway is not."""
    soon = watch.check_cert_expiry("ahb123.com", cert_fetcher=lambda host: _cert(5))
    assert soon is not None
    assert soon["level"] == "problem"
    assert soon["check"] == "cert"
    assert soon["host"] == "ahb123.com"

    healthy = watch.check_cert_expiry("ahb123.com", cert_fetcher=lambda host: _cert(90))
    assert healthy is None


def test_cert_expiry_window_boundary_just_under(watch):
    result = watch.check_cert_expiry("ahb123.com", cert_fetcher=lambda host: _cert(13.9))
    assert result is not None
    assert result["level"] == "problem"


def test_cert_expiry_window_boundary_just_over(watch):
    result = watch.check_cert_expiry("ahb123.com", cert_fetcher=lambda host: _cert(14.5))
    assert result is None


def test_cert_expiry_missing_not_after_is_problem(watch):
    result = watch.check_cert_expiry("ahb123.com", cert_fetcher=lambda host: {})
    assert result is not None
    assert result["level"] == "problem"


def test_cert_expiry_default_now_is_utc_not_local(watch):
    """Pins the UTC-vs-local fix: notAfter is GMT, so check_cert_expiry's
    default `now` must be UTC too. Uses the box's real UTC clock (not local)
    to build notAfter 13.9 / 14.1 days out and calls check_cert_expiry with
    no injected `now`, exercising its actual default. On the pre-fix code
    (bare datetime.datetime.now(), local time) this box's ~4h EDT offset
    inflates apparent days-left enough to flip the 13.9-day case from
    "problem" to a false "healthy" -- so this fails under the bug and passes
    under the fix, on any box not already running in UTC.
    """
    utc_now = datetime.datetime.now(datetime.timezone.utc)

    soon = watch.check_cert_expiry(
        "ahb123.com",
        cert_fetcher=lambda host: {
            "notAfter": (utc_now + datetime.timedelta(days=13.9)).strftime("%b %d %H:%M:%S %Y GMT")
        },
    )
    assert soon is not None
    assert soon["level"] == "problem"

    healthy = watch.check_cert_expiry(
        "ahb123.com",
        cert_fetcher=lambda host: {
            "notAfter": (utc_now + datetime.timedelta(days=14.1)).strftime("%b %d %H:%M:%S %Y GMT")
        },
    )
    assert healthy is None


# ── handshake failure: problem on nova, info-only on baza ──────────────

def test_handshake_fail_nova_is_problem_baza_is_not(watch):
    def boom(host):
        raise ConnectionRefusedError("connection refused")

    nova_result = watch.check_cert_expiry("nova.ahb123.com", cert_fetcher=boom)
    assert nova_result is not None
    assert nova_result["level"] == "problem"
    assert nova_result["check"] == "cert"
    assert nova_result["host"] == "nova.ahb123.com"

    baza_result = watch.check_cert_expiry("baza.ahb123.com", cert_fetcher=boom)
    assert baza_result is not None
    assert baza_result["level"] == "info"
    assert baza_result["check"] == "cert"
    assert baza_result["host"] == "baza.ahb123.com"


def test_handshake_fail_ahb123_is_problem(watch):
    """ahb123.com (external, Squarespace-hosted) isn't in the info-only set --
    a handshake failure there is a real problem."""
    def boom(host):
        raise TimeoutError("timed out")

    result = watch.check_cert_expiry("ahb123.com", cert_fetcher=boom)
    assert result is not None
    assert result["level"] == "problem"


# ── check_dns_resolution ──────────────────────────────────────────────────

def test_dns_resolution_empty_is_problem(watch):
    problems = watch.check_dns_resolution(["ahb123.com"], resolver=lambda host: [])
    assert len(problems) == 1
    assert problems[0]["host"] == "ahb123.com"
    assert problems[0]["check"] == "resolve"


def test_dns_resolution_success_is_silent(watch):
    problems = watch.check_dns_resolution(["ahb123.com"], resolver=lambda host: ["1.2.3.4"])
    assert problems == []


def test_dns_resolution_checks_each_host_independently(watch):
    def resolver(host):
        return [] if host == "baza.ahb123.com" else ["1.2.3.4"]

    problems = watch.check_dns_resolution(
        ["ahb123.com", "nova.ahb123.com", "baza.ahb123.com"], resolver=resolver
    )
    assert len(problems) == 1
    assert problems[0]["host"] == "baza.ahb123.com"


# ── default_resolver: bounded, doesn't hang ─────────────────────────────

def test_default_resolver_times_out_instead_of_hanging(watch, monkeypatch):
    """socket.getaddrinfo has no timeout param; default_resolver must bound
    it itself. Swap in a fake raw resolve that sleeps well past a shrunk
    RESOLVER_TIMEOUT_SECONDS (keeps the test fast) and confirm the call
    returns promptly with [] -- an unresolved-problem result, same as any
    other resolution failure -- rather than blocking for the fake's full
    sleep."""
    def hangs(host):
        time.sleep(2)
        return ["1.2.3.4"]

    monkeypatch.setattr(watch, "_resolve_addrinfo", hangs)
    monkeypatch.setattr(watch, "RESOLVER_TIMEOUT_SECONDS", 0.1)

    start = time.monotonic()
    result = watch.default_resolver("example.com")
    elapsed = time.monotonic() - start

    assert result == []
    assert elapsed < 1.0  # bounded by RESOLVER_TIMEOUT_SECONDS, not the 2s hang

    # Feeds straight into check_dns_resolution as an unresolved-host problem,
    # exactly like NXDOMAIN or any other resolution failure.
    problems = watch.check_dns_resolution(["example.com"], resolver=watch.default_resolver)
    assert len(problems) == 1
    assert problems[0]["check"] == "resolve"


# ── check_liveness ─────────────────────────────────────────────────────────

def test_liveness_ok_below_500(watch):
    assert watch.check_liveness(http_prober=lambda url: 200) is None
    assert watch.check_liveness(http_prober=lambda url: 404) is None


def test_liveness_5xx_is_problem(watch):
    result = watch.check_liveness(http_prober=lambda url: 502)
    assert result is not None
    assert result["check"] == "liveness"


def test_liveness_unreachable_is_problem(watch):
    result = watch.check_liveness(http_prober=lambda url: None)
    assert result is not None
    assert result["check"] == "liveness"


# ── end-to-end wiring: all green -> nothing sent ────────────────────────

def test_all_green_silent(watch, alert_recorder, monkeypatch):
    """When every check comes back healthy, main() must not call send_alert."""
    healthy_wan = "71.175.76.97"
    monkeypatch.setattr(watch, "default_resolver", lambda host: [healthy_wan])
    monkeypatch.setattr(watch, "default_wan_ip_getter", lambda: healthy_wan)
    monkeypatch.setattr(watch, "default_cert_fetcher", lambda host: _cert(90))
    monkeypatch.setattr(watch, "default_http_prober", lambda url: 200)

    watch.main()

    assert alert_recorder == []


def test_problems_trigger_alerts(watch, alert_recorder, monkeypatch):
    """Counterpart to test_all_green_silent: confirm the wiring actually
    calls send_alert (with the documented key format + renotify_hours=6)
    when something's wrong -- here, a live nova DNS drift."""
    monkeypatch.setattr(watch, "default_resolver", lambda host: ["9.9.9.9"])
    monkeypatch.setattr(watch, "default_wan_ip_getter", lambda: "1.1.1.1")
    monkeypatch.setattr(watch, "default_cert_fetcher", lambda host: _cert(90))
    monkeypatch.setattr(watch, "default_http_prober", lambda url: 200)

    watch.main()

    assert len(alert_recorder) == 1
    call = alert_recorder[0]
    assert call["alert_key"] == "dnswatch:nova.ahb123.com:drift"
    assert call["renotify_hours"] == 6
    assert "nova DNS drift: A=9.9.9.9 WAN=1.1.1.1" in call["message"]


def test_baza_handshake_failure_alone_does_not_alert(watch, alert_recorder, monkeypatch):
    """baza.ahb123.com's handshake failure is info-only -- must not alert by
    itself when everything else is healthy."""
    monkeypatch.setattr(watch, "default_resolver", lambda host: ["71.175.76.97"])
    monkeypatch.setattr(watch, "default_wan_ip_getter", lambda: "71.175.76.97")

    def cert_fetcher(host):
        if host == "baza.ahb123.com":
            raise ConnectionRefusedError("tunnel not up yet")
        return _cert(90)

    monkeypatch.setattr(watch, "default_cert_fetcher", cert_fetcher)
    monkeypatch.setattr(watch, "default_http_prober", lambda url: 200)

    watch.main()

    assert alert_recorder == []
