#!/usr/bin/env python3
import os, json, socket, urllib.request
# Force IPv6 — IPv4 unreachable on this host
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET6] or _orig(*a, **k)
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
coins = args.get("coins",["monero","ravencoin","bitcoin"])
url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(coins)}&vs_currencies=usd&include_24hr_change=true"
try:
    req = urllib.request.Request(url, headers={"User-Agent":"BazaEmpire/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    names = {"monero":"XMR","ravencoin":"RVN","bitcoin":"BTC","ethereum":"ETH"}
    print("=== Crypto Prices ===")
    for cid, info in data.items():
        s=names.get(cid,cid.upper()); p=info.get("usd",0); c=info.get("usd_24h_change",0)
        print(f"  {s}: ${p:,.4f}  {'▲' if c>=0 else '▼'} {abs(c):.2f}% 24h")
except Exception as e:
    print(f"Error: {e}")
