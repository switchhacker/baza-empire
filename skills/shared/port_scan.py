#!/usr/bin/env python3
"""Check if a port is open on localhost."""
import os, json, socket

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
host = args.get("host", "localhost")
ports = args.get("ports", [80, 443, 5432, 6379, 8888, 11434, 11435, 4000, 8000])

if isinstance(ports, int):
    ports = [ports]

results = []
for port in ports:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        result = sock.connect_ex((host, int(port)))
        results.append({"port": port, "open": result == 0})
    except Exception:
        results.append({"port": port, "open": False})
    finally:
        sock.close()

open_ports = [r["port"] for r in results if r["open"]]
print(json.dumps({"host": host, "results": results, "open_ports": open_ports}))
