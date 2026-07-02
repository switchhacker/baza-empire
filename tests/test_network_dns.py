"""Tests for dashboard/network_dns.py — Task 8: deSEC DNS panel.

All HTTP is monkeypatched via the `http` injectable; the real deSEC API
is never contacted.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))

import pytest
import network_dns
from network_dns import desec_rrsets, desec_set_rrset, DESEC_DOMAIN
import network_routes


# ── helpers ────────────────────────────────────────────────────────────────

def make_http(status, payload):
    """Return a fake http callable that returns (status, payload)."""
    def _fake(method, url, headers=None, body=None):
        return (status, payload)
    return _fake


# ── DESEC_DOMAIN constant ──────────────────────────────────────────────────

def test_desec_domain_value():
    assert DESEC_DOMAIN == "nova.ahb123.com"


# ── desec_rrsets ───────────────────────────────────────────────────────────

def test_desec_rrsets_sends_correct_url_and_token_header():
    """desec_rrsets must GET the right URL with Authorization: Token <tok>."""
    calls = []

    def _http(method, url, headers=None, body=None):
        calls.append({"method": method, "url": url, "headers": headers or {}})
        return (200, [{"subname": "@", "type": "A", "ttl": 300, "records": ["1.2.3.4"]}])

    tok = "my-secret-token"
    result = desec_rrsets(tok, http=_http)

    assert len(calls) == 1
    c = calls[0]
    assert c["method"].upper() == "GET"
    assert "nova.ahb123.com" in c["url"]
    assert "rrsets" in c["url"]
    assert c["headers"].get("Authorization") == f"Token {tok}"
    # token must NOT appear in the URL itself
    assert tok not in c["url"]


def test_desec_rrsets_returns_list():
    payload = [
        {"subname": "@", "type": "A", "ttl": 300, "records": ["1.2.3.4"]},
        {"subname": "_dmarc", "type": "TXT", "ttl": 3600, "records": ["v=DMARC1"]},
    ]
    result = desec_rrsets("tok", http=make_http(200, payload))
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["subname"] == "@"
    assert result[0]["type"] == "A"


def test_desec_rrsets_http_error_returns_empty():
    """Non-2xx response should not raise — return empty list."""
    result = desec_rrsets("tok", http=make_http(401, {"detail": "unauthorized"}))
    assert result == []


# ── desec_set_rrset ────────────────────────────────────────────────────────

def test_desec_set_rrset_puts_correct_body():
    """desec_set_rrset must PUT the right URL with Authorization header and body."""
    calls = []

    def _http(method, url, headers=None, body=None):
        calls.append({"method": method, "url": url, "headers": headers or {}, "body": body})
        return (200, {"subname": "@", "type": "A", "ttl": 300, "records": ["1.2.3.4"]})

    tok = "tok123"
    result = desec_set_rrset(tok, "@", "A", 300, ["1.2.3.4"], http=_http)

    assert len(calls) == 1
    c = calls[0]
    assert c["method"].upper() == "PUT"
    assert "nova.ahb123.com" in c["url"]
    assert "rrsets" in c["url"]
    assert "@" in c["url"] or "%40" in c["url"]
    assert "A" in c["url"]
    assert c["headers"].get("Authorization") == f"Token {tok}"
    # body must contain the right fields
    body = c["body"]
    assert body["subname"] == "@"
    assert body["type"] == "A"
    assert body["ttl"] == 300
    assert body["records"] == ["1.2.3.4"]
    # token must NOT appear in the body
    assert tok not in str(body)


def test_desec_set_rrset_bad_ipv4_raises_value_error():
    """An A record with a non-IPv4 value must raise ValueError before HTTP call."""
    called = []

    def _http(method, url, headers=None, body=None):
        called.append(True)
        return (200, {})

    with pytest.raises(ValueError, match="Invalid IPv4"):
        desec_set_rrset("tok", "@", "A", 300, ["96.227.96"], http=_http)

    # HTTP should NOT have been called
    assert not called


def test_desec_set_rrset_bad_ipv4_another_case():
    with pytest.raises(ValueError):
        desec_set_rrset("tok", "@", "A", 300, ["not-an-ip"], http=make_http(200, {}))


def test_desec_set_rrset_valid_ipv4_does_not_raise():
    result = desec_set_rrset("tok", "@", "A", 300, ["192.168.1.1"],
                             http=make_http(200, {"subname": "@", "type": "A"}))
    assert isinstance(result, dict)


def test_desec_set_rrset_ttl_below_60_raises():
    with pytest.raises(ValueError, match="ttl"):
        desec_set_rrset("tok", "@", "A", 59, ["1.2.3.4"], http=make_http(200, {}))


def test_desec_set_rrset_invalid_rtype_raises():
    with pytest.raises(ValueError, match="rtype"):
        desec_set_rrset("tok", "@", "INVALID", 300, ["1.2.3.4"], http=make_http(200, {}))


def test_desec_set_rrset_txt_records_allowed():
    """TXT records don't need IPv4 validation."""
    result = desec_set_rrset("tok", "_dmarc", "TXT", 300, ["v=DMARC1"],
                             http=make_http(200, {"subname": "_dmarc", "type": "TXT"}))
    assert isinstance(result, dict)


def test_desec_set_rrset_multiple_valid_ipv4():
    result = desec_set_rrset("tok", "@", "A", 300, ["1.2.3.4", "5.6.7.8"],
                             http=make_http(200, {"subname": "@", "type": "A"}))
    assert isinstance(result, dict)


# ── routes ─────────────────────────────────────────────────────────────────

def _make_app(tmp_path, monkeypatch):
    from flask import Flask
    monkeypatch.setattr(network_routes.network_db, "DEFAULT_DB", str(tmp_path / "n.db"))
    # patch DEFAULT_DB in network_dns too (if it uses it)
    try:
        import network_dns as nd
        if hasattr(nd, "network_db"):
            monkeypatch.setattr(nd.network_db, "DEFAULT_DB", str(tmp_path / "n.db"))
    except Exception:
        pass
    app = Flask(
        "t",
        template_folder=os.path.join(REPO_ROOT, "dashboard", "templates"),
        static_folder=os.path.join(REPO_ROOT, "dashboard", "static"),
    )
    network_routes.init_network()
    app.register_blueprint(network_routes.network_bp)
    return app.test_client()


def test_route_get_token_returns_set_false_when_missing(tmp_path, monkeypatch):
    c = _make_app(tmp_path, monkeypatch)
    r = c.get("/api/network/token/desec")
    assert r.status_code == 200
    j = r.get_json()
    assert j == {"set": False}
    # must not leak any token value
    assert "token" not in j or j.get("token") is None


def test_route_post_token_then_get_returns_set_true(tmp_path, monkeypatch):
    c = _make_app(tmp_path, monkeypatch)
    # POST the token
    pr = c.post("/api/network/token/desec", json={"token": "supersecret"})
    assert pr.status_code == 200
    assert pr.get_json().get("ok") is True
    # GET must return {"set": True} — never the token value itself
    gr = c.get("/api/network/token/desec")
    assert gr.status_code == 200
    j = gr.get_json()
    assert j == {"set": True}
    # The token string must not appear anywhere in the response
    raw = gr.data.decode()
    assert "supersecret" not in raw


def test_route_get_desec_no_token(tmp_path, monkeypatch):
    c = _make_app(tmp_path, monkeypatch)
    r = c.get("/api/network/desec")
    assert r.status_code == 400
    j = r.get_json()
    assert "error" in j
    assert "token" in j["error"].lower() or "no token" in j["error"].lower()


def test_route_get_desec_with_token(tmp_path, monkeypatch):
    c = _make_app(tmp_path, monkeypatch)
    # store a token first
    c.post("/api/network/token/desec", json={"token": "tok123"})

    # patch network_dns.desec_rrsets
    fake_rrsets = [{"subname": "@", "type": "A", "ttl": 300, "records": ["1.2.3.4"]}]
    monkeypatch.setattr(network_routes.network_dns, "desec_rrsets",
                        lambda tok, http=None: fake_rrsets)
    r = c.get("/api/network/desec")
    assert r.status_code == 200
    j = r.get_json()
    assert isinstance(j, list)
    assert j[0]["subname"] == "@"
    # token must not appear in response
    assert "tok123" not in r.data.decode()


def test_route_post_desec_valid(tmp_path, monkeypatch):
    c = _make_app(tmp_path, monkeypatch)
    c.post("/api/network/token/desec", json={"token": "tok999"})

    monkeypatch.setattr(network_routes.network_dns, "desec_set_rrset",
                        lambda tok, subname, rtype, ttl, records, http=None: {
                            "subname": subname, "type": rtype, "ttl": ttl, "records": records
                        })
    r = c.post("/api/network/desec",
               json={"subname": "@", "rtype": "A", "ttl": 300, "records": ["1.2.3.4"]})
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("subname") == "@"
    # token must not appear
    assert "tok999" not in r.data.decode()


def test_route_post_desec_validation_error(tmp_path, monkeypatch):
    c = _make_app(tmp_path, monkeypatch)
    c.post("/api/network/token/desec", json={"token": "tok999"})

    def bad_set(*a, **kw):
        raise ValueError("Invalid IPv4 address: 'not-an-ip'")
    monkeypatch.setattr(network_routes.network_dns, "desec_set_rrset", bad_set)
    r = c.post("/api/network/desec",
               json={"subname": "@", "rtype": "A", "ttl": 300, "records": ["not-an-ip"]})
    assert r.status_code == 400
    j = r.get_json()
    assert "error" in j


# ── subname path-injection validation ─────────────────────────────────────

def test_desec_set_rrset_path_injection_slash_raises():
    """Subname with path-traversal chars must raise ValueError before HTTP call."""
    called = []

    def _http(method, url, headers=None, body=None):
        called.append(True)
        return (200, {})

    with pytest.raises(ValueError, match="invalid subname"):
        desec_set_rrset("tok", "@/../../domains/evil", "A", 300, ["1.2.3.4"], http=_http)

    # HTTP must NOT have been called
    assert not called


def test_desec_set_rrset_path_injection_dotdot_raises():
    """Subname starting with .. must raise ValueError before HTTP call."""
    called = []

    def _http(method, url, headers=None, body=None):
        called.append(True)
        return (200, {})

    with pytest.raises(ValueError, match="invalid subname"):
        desec_set_rrset("tok", "../evil", "A", 300, ["1.2.3.4"], http=_http)

    assert not called


def test_desec_set_rrset_root_sentinel_at_works():
    """Root sentinel '@' must be accepted and PUT to a URL ending /rrsets/@/A/."""
    calls = []

    def _http(method, url, headers=None, body=None):
        calls.append({"method": method, "url": url})
        return (200, {"subname": "@", "type": "A", "ttl": 300, "records": ["1.2.3.4"]})

    result = desec_set_rrset("tok", "@", "A", 300, ["1.2.3.4"], http=_http)

    assert len(calls) == 1
    assert calls[0]["method"].upper() == "PUT"
    assert calls[0]["url"].endswith("/rrsets/@/A/")
    assert isinstance(result, dict)


# ── Task 9: cf_zone_status ─────────────────────────────────────────────────

from network_dns import cf_zone_status


def test_cf_zone_status_correct_url_and_auth_header():
    """cf_zone_status must GET the correct Cloudflare zones URL with Bearer auth."""
    calls = []

    def _http(method, url, headers=None, body=None):
        calls.append({"method": method, "url": url, "headers": headers or {}})
        return (200, {"result": [], "success": True})

    tok = "my-cf-token"
    result = cf_zone_status(tok, http=_http)

    assert len(calls) == 1
    c = calls[0]
    assert c["method"].upper() == "GET"
    assert "api.cloudflare.com" in c["url"]
    assert "zones" in c["url"]
    assert "ahb123.com" in c["url"]
    assert c["headers"].get("Authorization") == f"Bearer {tok}"
    # token must not appear in the URL
    assert tok not in c["url"]


def test_cf_zone_status_not_found_returns_found_false():
    """Empty result list → found=False, empty status and name_servers."""
    def _http(method, url, headers=None, body=None):
        return (200, {"result": [], "success": True})

    result = cf_zone_status("tok", http=_http)

    assert result["found"] is False
    assert result["status"] == ""
    assert result["name_servers"] == []


def test_cf_zone_status_found_parses_zone():
    """When result[0] is present, found=True, status and name_servers populated."""
    zone = {
        "name": "ahb123.com",
        "status": "active",
        "name_servers": ["ns1.cloudflare.com", "ns2.cloudflare.com"],
    }

    def _http(method, url, headers=None, body=None):
        return (200, {"result": [zone], "success": True})

    result = cf_zone_status("tok", http=_http)

    assert result["found"] is True
    assert result["status"] == "active"
    assert "ns1.cloudflare.com" in result["name_servers"]


def test_cf_zone_status_http_error_returns_not_found():
    """Non-2xx response → found=False gracefully (no raise)."""
    def _http(method, url, headers=None, body=None):
        return (403, {"errors": [{"message": "forbidden"}]})

    result = cf_zone_status("tok", http=_http)

    assert result["found"] is False
    assert isinstance(result["name_servers"], list)


def test_cf_zone_status_missing_result_key():
    """Payload without 'result' key → found=False without raising."""
    def _http(method, url, headers=None, body=None):
        return (200, {"success": True})

    result = cf_zone_status("tok", http=_http)

    assert result["found"] is False


# ── Task 9: GET /api/network/cloudflare route ──────────────────────────────

def test_route_cloudflare_no_token(tmp_path, monkeypatch):
    """GET /api/network/cloudflare with no token → 400 with 'error' key."""
    c = _make_app(tmp_path, monkeypatch)
    r = c.get("/api/network/cloudflare")
    assert r.status_code == 400
    j = r.get_json()
    assert "error" in j


def test_route_cloudflare_with_token_found(tmp_path, monkeypatch):
    """GET /api/network/cloudflare with token → returns cf_zone_status result."""
    c = _make_app(tmp_path, monkeypatch)
    c.post("/api/network/token/cloudflare", json={"token": "cf-tok"})

    monkeypatch.setattr(
        network_routes.network_dns, "cf_zone_status",
        lambda tok, http=None: {"found": True, "status": "active", "name_servers": ["ns1.cf.com"]}
    )
    r = c.get("/api/network/cloudflare")
    assert r.status_code == 200
    j = r.get_json()
    assert j["found"] is True
    assert j["status"] == "active"
    # token must never leak into response
    assert "cf-tok" not in r.data.decode()


def test_route_cloudflare_with_token_not_found(tmp_path, monkeypatch):
    """GET /api/network/cloudflare with token but zone not on CF → found=False, 200."""
    c = _make_app(tmp_path, monkeypatch)
    c.post("/api/network/token/cloudflare", json={"token": "cf-tok"})

    monkeypatch.setattr(
        network_routes.network_dns, "cf_zone_status",
        lambda tok, http=None: {"found": False, "status": "", "name_servers": []}
    )
    r = c.get("/api/network/cloudflare")
    assert r.status_code == 200
    j = r.get_json()
    assert j["found"] is False
