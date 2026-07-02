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
