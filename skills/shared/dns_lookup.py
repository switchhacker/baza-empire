#!/usr/bin/env python3
"""DNS lookup for a domain."""
import os, json, socket, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
domain = args.get("domain", "")
record_type = args.get("type", "A")

if not domain:
    print(json.dumps({"error": "No domain provided"}))
else:
    result = {"domain": domain, "records": []}
    # Basic resolution
    try:
        ips = socket.getaddrinfo(domain, None)
        seen = set()
        for ip in ips:
            addr = ip[4][0]
            if addr not in seen:
                seen.add(addr)
                result["records"].append({"type": "A" if "." in addr else "AAAA", "value": addr})
    except socket.gaierror as e:
        result["error"] = str(e)
    # Try dig for specific records
    try:
        r = subprocess.run(["dig", "+short", record_type, domain],
                         capture_output=True, text=True, timeout=10)
        if r.stdout.strip():
            for line in r.stdout.strip().split("\n"):
                result["records"].append({"type": record_type, "value": line.strip()})
    except Exception:
        pass
    print(json.dumps(result))
