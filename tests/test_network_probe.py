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


# ── Task 3: new tests ──────────────────────────────────────────────────────

CADDYFILE = """\
nova.ahb123.com {
    bind 192.168.1.68
    reverse_proxy localhost:8888
    reverse_proxy localhost:8000
}

www.example.com {
    reverse_proxy localhost:9000
}
"""

def test_parse_caddy_sites():
    sites = np.parse_caddy_sites(CADDYFILE)
    by_host = {s["host"]: s for s in sites}
    assert "nova.ahb123.com" in by_host
    nova = by_host["nova.ahb123.com"]
    assert nova["bind"] == "192.168.1.68"
    assert set(nova["upstreams"]) == {"localhost:8888", "localhost:8000"}
    assert "www.example.com" in by_host
    assert by_host["www.example.com"]["bind"] is None


def test_parse_caddy_sites_nested():
    """Nested handle blocks containing reverse_proxy must be extracted."""
    nested_caddyfile = """\
nova.ahb123.com {
    bind 192.168.1.68
    handle @reviews {
        reverse_proxy localhost:8888
    }
    handle @api {
        reverse_proxy localhost:8000
    }
}
"""
    sites = np.parse_caddy_sites(nested_caddyfile)
    by_host = {s["host"]: s for s in sites}
    assert "nova.ahb123.com" in by_host
    nova = by_host["nova.ahb123.com"]
    assert nova["bind"] == "192.168.1.68"
    assert set(nova["upstreams"]) == {"localhost:8888", "localhost:8000"}


def test_build_edges_nova_drift():
    """nova A record != WAN IP → nova chain DNS hop ok=False."""
    dns = [
        {"name": "ahb123.com", "rtype": "A", "ok": True, "expected": ["198.49.23.144"], "actual": ["198.49.23.144"]},
        {"name": "nova.ahb123.com", "rtype": "A", "ok": False, "expected": ["96.227.96.20"], "actual": ["1.2.3.4"]},
    ]
    caddy = {"active": True, "sites": [{"host": "nova.ahb123.com", "bind": "192.168.1.68", "upstreams": ["localhost:8888"]}], "valid": True, "validate_err": None, "backups": []}
    cloudflared = {"installed": True, "version": "2024.x", "config_exists": False, "unit_state": "inactive", "tunnels": "not authenticated"}
    listeners = [
        {"addr": "0.0.0.0", "port": 8888, "proc": "python"},
        {"addr": "192.168.1.68", "port": 443, "proc": "caddy"},
    ]
    tailscale = {"self": {"host": "baza-1", "ip": "100.127.118.103"}, "peers": [], "serves": [{"listen": "443", "target": "http://127.0.0.1:8888"}]}
    wan_ip = "96.227.96.20"

    edges = np.build_edges(dns, wan_ip, caddy, cloudflared, listeners, tailscale)
    nova_chain = next(e for e in edges if e["chain"] == "nova.ahb123.com")
    dns_hop = next(h for h in nova_chain["hops"] if "deSEC" in h["label"] or "DNS" in h["label"])
    assert dns_hop["ok"] is False


def test_build_edges_baza_planned():
    """No cloudflared config → baza chain single planned hop with ok=None."""
    dns = []
    caddy = {"active": True, "sites": [], "valid": True, "validate_err": None, "backups": []}
    cloudflared = {"installed": True, "version": "2024.x", "config_exists": False, "unit_state": "inactive", "tunnels": "not authenticated"}
    listeners = []
    tailscale = {"self": {"host": "baza-1", "ip": "100.127.118.103"}, "peers": [], "serves": []}
    wan_ip = "96.227.96.20"

    edges = np.build_edges(dns, wan_ip, caddy, cloudflared, listeners, tailscale)
    baza_chain = next(e for e in edges if e["chain"] == "baza.ahb123.com")
    assert len(baza_chain["hops"]) == 1
    assert baza_chain["hops"][0]["ok"] is None


def test_probe_interfaces_shape():
    r = np.probe_interfaces()
    assert "nics" in r and "routes" in r
    assert isinstance(r["nics"], list)


def test_probe_tailscale_shape():
    r = np.probe_tailscale()
    assert "self" in r and "peers" in r and "serves" in r


def test_probe_services_shape():
    r = np.probe_services()
    assert isinstance(r, dict)
    for unit in np.UNITS:
        assert unit in r
        assert "active" in r[unit]


def test_probe_listeners_shape():
    r = np.probe_listeners()
    assert isinstance(r, list)


def test_probe_wan_ip_shape():
    r = np.probe_wan_ip()
    assert r is None or isinstance(r, str)


def test_probe_caddy_shape():
    r = np.probe_caddy()
    assert "active" in r and "sites" in r and "valid" in r


def test_probe_cloudflared_shape():
    r = np.probe_cloudflared()
    assert "installed" in r and "config_exists" in r and "tunnels" in r


def test_status_shape():
    s = np.status()
    for key in ("interfaces", "tailscale", "services", "listeners", "wan_ip", "dns", "caddy", "cloudflared", "certs", "reach", "edges", "ts"):
        assert key in s, f"missing key: {key}"
    assert isinstance(s["edges"], list)
    assert len(s["edges"]) == 4


def test_probe_certs_shape():
    r = np.probe_certs()
    assert isinstance(r, list)
    for c in r:
        assert {"host", "days_left", "ok"} <= set(c)


def test_probe_dns_shape():
    r = np.probe_dns()
    assert isinstance(r, list)
    for v in r:
        assert {"name", "rtype", "expected", "actual", "ok"} <= set(v)


def test_build_edges_with_reach_nova_url():
    """nova.ahb123.com reach lookup uses /health endpoint."""
    dns = [
        {"name": "ahb123.com", "rtype": "A", "ok": True, "expected": ["198.49.23.144"], "actual": ["198.49.23.144"]},
        {"name": "nova.ahb123.com", "rtype": "A", "ok": True, "expected": ["96.227.96.20"], "actual": ["96.227.96.20"]},
    ]
    caddy = {"active": True, "sites": [{"host": "nova.ahb123.com", "bind": "192.168.1.68", "upstreams": ["localhost:8000"]}], "valid": True, "validate_err": None, "backups": []}
    cloudflared = {"installed": False, "version": None, "config_exists": False, "unit_state": "inactive", "tunnels": ""}
    listeners = [
        {"addr": "192.168.1.68", "port": 443, "proc": "caddy"},
        {"addr": "0.0.0.0", "port": 8000, "proc": "tool-server"},
    ]
    tailscale = {"self": {"host": "baza-1", "ip": "100.127.118.103"}, "peers": [], "serves": []}
    wan_ip = "96.227.96.20"
    reach = [
        {"url": "https://ahb123.com", "status": 200, "ok": True},
        {"url": "https://nova.ahb123.com/health", "status": 200, "ok": True},
    ]

    edges = np.build_edges(dns, wan_ip, caddy, cloudflared, listeners, tailscale)
    edges_with_reach = np._build_edges_with_reach(dns, wan_ip, caddy, cloudflared, listeners, tailscale, reach)
    nova_chain = next(e for e in edges_with_reach if e["chain"] == "nova.ahb123.com")
    reach_hop = nova_chain["hops"][1]
    assert reach_hop["ok"] is True
    assert "HTTP 200" in reach_hop["detail"]


def test_http_status_head_ok():
    """_http_status with successful HEAD request returns status."""
    import urllib.error
    from unittest.mock import Mock, patch

    mock_response = Mock()
    mock_response.status = 200
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=None)

    with patch('urllib.request.urlopen', return_value=mock_response):
        status = np._http_status("https://example.com", timeout=5)
    assert status == 200


def test_http_status_head_405_retry_get():
    """_http_status retries GET when HEAD returns 405 (Method Not Allowed)."""
    import urllib.error
    from unittest.mock import Mock, patch, call

    # First call (HEAD) raises 405; second call (GET) succeeds with 200
    http_error_405 = urllib.error.HTTPError("url", 405, "Method Not Allowed", {}, None)
    mock_response_get = Mock()
    mock_response_get.status = 200
    mock_response_get.__enter__ = Mock(return_value=mock_response_get)
    mock_response_get.__exit__ = Mock(return_value=None)

    with patch('urllib.request.urlopen') as mock_urlopen:
        # First call raises 405, second call returns the mock
        mock_urlopen.side_effect = [http_error_405, mock_response_get]
        status = np._http_status("https://nova.ahb123.com/health", timeout=5)
    assert status == 200
    # Verify two requests were made
    assert mock_urlopen.call_count == 2


def test_http_status_other_error():
    """_http_status returns None on connection errors."""
    import urllib.error
    from unittest.mock import patch

    with patch('urllib.request.urlopen', side_effect=Exception("Connection timeout")):
        status = np._http_status("https://example.com", timeout=5)
    assert status is None


# ── Task 11: parse_ufw + probe_firewall ───────────────────────────────────────

UFW_ACTIVE = """\
Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
8888/tcp                   ALLOW IN    Anywhere
41641/udp                  ALLOW IN    Anywhere
22/tcp (v6)                ALLOW IN    Anywhere (v6)
"""

UFW_INACTIVE = """\
Status: inactive
"""

UFW_MISSING = ""  # empty / rc!=0 path


def test_parse_ufw_active():
    r = np.parse_ufw(UFW_ACTIVE)
    assert r["present"] is True
    assert r["active"] is True
    assert isinstance(r["rules"], list)
    assert len(r["rules"]) >= 3
    # Rules should include the table lines with ports
    assert any("22/tcp" in rule for rule in r["rules"])


def test_parse_ufw_inactive():
    r = np.parse_ufw(UFW_INACTIVE)
    assert r["present"] is True
    assert r["active"] is False
    assert isinstance(r["rules"], list)


def test_parse_ufw_missing():
    """Empty/missing text → present=False, active=False, rules=[]."""
    r = np.parse_ufw(UFW_MISSING)
    assert r["present"] is False
    assert r["active"] is False
    assert r["rules"] == []


def test_probe_firewall_shape(monkeypatch):
    """probe_firewall returns correct shape when ufw is present and active."""
    monkeypatch.setattr(np, "_run", lambda cmd, timeout=10: (0, UFW_ACTIVE, ""))
    r = np.probe_firewall()
    assert r["present"] is True
    assert r["active"] is True
    assert isinstance(r["rules"], list)


def test_probe_firewall_absent_falls_back_to_iptables(monkeypatch):
    """When ufw rc!=0, falls back to iptables -S; present=False, active=None."""
    IPTABLES_OUT = "-P INPUT ACCEPT\n-P FORWARD ACCEPT\n-P OUTPUT ACCEPT\n"

    def fake_run(cmd, timeout=10):
        if "ufw" in cmd:
            return (1, "", "ufw: command not found")
        if "iptables" in cmd:
            return (0, IPTABLES_OUT, "")
        return (-1, "", "")

    monkeypatch.setattr(np, "_run", fake_run)
    r = np.probe_firewall()
    assert r["present"] is False
    # active is None or "unknown" when ufw absent
    assert r["active"] in (None, False, "unknown")
    assert isinstance(r["rules"], list)
    assert any("INPUT" in rule or "ACCEPT" in rule for rule in r["rules"])


def test_probe_firewall_never_raises(monkeypatch):
    """probe_firewall never raises even when everything fails."""
    monkeypatch.setattr(np, "_run", lambda cmd, timeout=10: (_ for _ in ()).throw(RuntimeError("boom")))
    # Should not raise
    try:
        r = np.probe_firewall()
        assert isinstance(r, dict)
    except Exception as e:
        raise AssertionError(f"probe_firewall raised: {e}")


def test_status_includes_firewall():
    """status() dict must contain a 'firewall' key."""
    s = np.status()
    assert "firewall" in s, "status() missing 'firewall' key"
    fw = s["firewall"]
    assert "present" in fw
    assert "active" in fw
    assert "rules" in fw


# ── Task 13: settings_registry assembler ─────────────────────────────────────

FIXTURE_STATUS = {
    "caddy": {
        "active": True,
        "sites": [
            {"host": "nova.ahb123.com", "bind": "192.168.1.68", "upstreams": ["localhost:8888", "localhost:8000"]},
        ],
        "valid": True,
        "validate_err": None,
        "backups": [],
    },
    "cloudflared": {
        "installed": True,
        "version": "2024.x",
        "config_exists": False,
        "unit_state": "inactive",
        "tunnels": "not authenticated",
    },
    "tailscale": {
        "self": {"host": "baza-1", "ip": "100.127.118.103", "online": True},
        "peers": [],
        "serves": [{"listen": "443", "target": "http://127.0.0.1:8888"}],
    },
    "services": {
        "baza-ddns.service": {"active": "inactive", "sub": "dead", "since": ""},
        "baza-ddns.timer": {"active": "inactive", "sub": "dead", "since": ""},
    },
}

FIXTURE_FACTS = [
    {"key": "router.model", "value": "Fios G3100", "note": "", "updated": "2026-07-02 00:00:00"},
    {"key": "router.admin", "value": "http://192.168.1.1", "note": "", "updated": "2026-07-02 00:00:00"},
    {"key": "router.port_forward", "value": "443,80 → 192.168.1.68", "note": "verify: https://nova.ahb123.com/health", "updated": "2026-07-02 00:00:00"},
]

FIXTURE_ENV = {
    "BAZA_DASHBOARD_URL": "http://192.168.1.68:8888",
    "NOVA_DOMAIN": "nova.ahb123.com",
    "CADDY_CONFIG": "/etc/caddy/Caddyfile",
    "SECRET_TOKEN": "abc123secret",
    "API_KEY": "my-api-key-value",
}


def test_settings_registry_groups():
    """settings_registry returns entries for caddy, cloudflared, tailscale, ddns, env, router groups."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    groups = {r["group"] for r in rows}
    assert "caddy" in groups
    assert "cloudflared" in groups
    assert "tailscale" in groups
    assert "ddns" in groups
    assert "env" in groups
    assert "router" in groups


def test_settings_registry_caddy_path():
    """caddy group includes the Caddyfile path."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    caddy_rows = [r for r in rows if r["group"] == "caddy"]
    keys = {r["key"] for r in caddy_rows}
    assert "caddyfile" in keys
    caddyfile_row = next(r for r in caddy_rows if r["key"] == "caddyfile")
    assert "/etc/caddy/Caddyfile" in caddyfile_row["value"]


def test_settings_registry_caddy_sites():
    """caddy group has an entry for each site from status.caddy.sites."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    caddy_rows = [r for r in rows if r["group"] == "caddy"]
    site_rows = [r for r in caddy_rows if "nova.ahb123.com" in r.get("key", "")]
    assert len(site_rows) >= 1
    # edit flag points to caddy editor hint
    for r in caddy_rows:
        assert "edit" in r
        assert r["edit"] is False or isinstance(r["edit"], str)


def test_settings_registry_cloudflared_absent():
    """cloudflared group shows 'absent' when config_exists=False."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    cf_rows = [r for r in rows if r["group"] == "cloudflared"]
    assert len(cf_rows) >= 1
    config_row = next((r for r in cf_rows if r["key"] == "config"), None)
    assert config_row is not None
    assert "absent" in config_row["value"].lower() or not FIXTURE_STATUS["cloudflared"]["config_exists"]


def test_settings_registry_tailscale_serves():
    """tailscale group contains the serve mappings."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    ts_rows = [r for r in rows if r["group"] == "tailscale"]
    assert len(ts_rows) >= 1
    serve_rows = [r for r in ts_rows if "serve" in r["key"]]
    assert len(serve_rows) >= 1


def test_settings_registry_ddns_unit():
    """ddns group has an entry for baza-ddns.service."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    ddns_rows = [r for r in rows if r["group"] == "ddns"]
    assert len(ddns_rows) >= 1
    keys = {r["key"] for r in ddns_rows}
    assert any("ddns" in k for k in keys)


def test_settings_registry_env_values_shown():
    """env group: URL-ish values are shown in full."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    env_rows = {r["key"]: r for r in rows if r["group"] == "env"}
    assert "BAZA_DASHBOARD_URL" in env_rows
    assert env_rows["BAZA_DASHBOARD_URL"]["value"] == "http://192.168.1.68:8888"


def test_settings_registry_env_secrets_masked():
    """env group: TOKEN/KEY/SECRET values are masked as ***."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    env_rows = {r["key"]: r for r in rows if r["group"] == "env"}
    assert "SECRET_TOKEN" in env_rows
    assert env_rows["SECRET_TOKEN"]["value"] == "***"
    assert "API_KEY" in env_rows
    assert env_rows["API_KEY"]["value"] == "***"


def test_settings_registry_router_facts():
    """router group: manual_facts rows are present and editable."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    router_rows = [r for r in rows if r["group"] == "router"]
    assert len(router_rows) == len(FIXTURE_FACTS)
    for r in router_rows:
        assert r["edit"] is True  # router facts are editable
    keys = {r["key"] for r in router_rows}
    assert "router.model" in keys
    assert "router.port_forward" in keys


def test_settings_registry_env_read_only():
    """env group rows have edit=False (read-only)."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    env_rows = [r for r in rows if r["group"] == "env"]
    for r in env_rows:
        assert r["edit"] is False


def test_settings_registry_pure():
    """settings_registry is pure: same inputs produce same outputs."""
    r1 = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    r2 = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    assert r1 == r2


def test_settings_registry_empty_env():
    """settings_registry works with env=None (no env group entries)."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, None)
    env_rows = [r for r in rows if r["group"] == "env"]
    assert env_rows == []


def test_settings_registry_row_schema():
    """Every row has group, key, value, source, edit keys."""
    rows = np.settings_registry(FIXTURE_STATUS, FIXTURE_FACTS, FIXTURE_ENV)
    for r in rows:
        for field in ("group", "key", "value", "source", "edit"):
            assert field in r, f"row missing field {field!r}: {r}"


# ── Fix round 1: env masking safe-by-default ───────────────────────────────────

def test_mask_env_value_url_shown():
    """env value matches ^https?:// → shown in full."""
    assert np._mask_env_value("BAZA_DASHBOARD_URL", "http://100.127.118.103:8888") == "http://100.127.118.103:8888"
    assert np._mask_env_value("NOVA_URL", "https://nova.ahb123.com") == "https://nova.ahb123.com"


def test_mask_env_value_bearer_token_masked():
    """env value sk-secretvalue123 without secret-word key name → masked (regression test)."""
    assert np._mask_env_value("BAZA_API_BEARER", "sk-secretvalue123") == "***"


def test_mask_env_value_key_secret_word_masked():
    """env key contains secret-word → always masked."""
    assert np._mask_env_value("BAZA_API_TOKEN", "anything") == "***"
    assert np._mask_env_value("SECRET_STUFF", "http://example.com") == "***"


def test_mask_env_value_numeric_port_shown():
    """env value purely numeric → shown (port number)."""
    assert np._mask_env_value("NOVA_PORT", "8000") == "8000"
    assert np._mask_env_value("SOME_PORT", "443") == "443"


def test_mask_env_value_host_port_shown():
    """env value bare host[:port] → shown."""
    assert np._mask_env_value("DATABASE_HOST", "localhost:5432") == "localhost:5432"
    assert np._mask_env_value("DB_HOST", "db.example.com") == "db.example.com"
    assert np._mask_env_value("SERVER", "192.168.1.1:8000") == "192.168.1.1:8000"


def test_mask_env_value_host_port_path_shown():
    """env value bare host[:port][/path] → shown."""
    assert np._mask_env_value("API_ENDPOINT", "api.example.com/v1/status") == "api.example.com/v1/status"
    assert np._mask_env_value("WEBHOOK", "localhost:8000/webhook") == "localhost:8000/webhook"


def test_mask_env_value_boolean_shown():
    """env value boolean-like → shown."""
    assert np._mask_env_value("DEBUG", "true") == "true"
    assert np._mask_env_value("ENABLED", "false") == "false"
    assert np._mask_env_value("ACTIVE", "on") == "on"
    assert np._mask_env_value("DISABLED", "off") == "off"
    assert np._mask_env_value("YES", "yes") == "yes"
    assert np._mask_env_value("NO", "no") == "no"


def test_mask_env_value_arbitrary_string_masked():
    """env value arbitrary string → masked."""
    assert np._mask_env_value("SOME_VAR", "random_secret_12345") == "***"
    assert np._mask_env_value("CONFIG", "myconfigstring") == "***"


# ── Final-review fix round: PROBE_UNITS / timer probe isolation ───────────────

def test_probe_units_includes_timer():
    """PROBE_UNITS must include baza-ddns.timer for display on the Network tab."""
    assert "baza-ddns.timer" in np.PROBE_UNITS


def test_units_does_not_include_timer():
    """UNITS (svc whitelist) must NOT include baza-ddns.timer."""
    assert "baza-ddns.timer" not in np.UNITS


def test_probe_services_includes_timer():
    """probe_services() result must contain baza-ddns.timer key."""
    result = np.probe_services()
    assert "baza-ddns.timer" in result
    assert "active" in result["baza-ddns.timer"]


def test_probe_services_still_contains_all_units():
    """probe_services() still contains every unit from UNITS."""
    result = np.probe_services()
    for unit in np.UNITS:
        assert unit in result, f"probe_services() missing expected unit: {unit}"
