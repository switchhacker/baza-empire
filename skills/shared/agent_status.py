#!/usr/bin/env python3
"""Check status of all Baza agents (systemd services)."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))

AGENTS = ["simon-bately", "claw-batto", "phil-hass", "sam-axe", "duke-harmon", "rex-valor", "scout-reeves", "nova-sterling"]

results = {}
for agent in AGENTS:
    svc = f"baza-agent-{agent}"
    try:
        r = subprocess.run(["systemctl", "is-active", f"{svc}.service"], capture_output=True, text=True, timeout=5)
        results[agent] = r.stdout.strip()
    except Exception:
        results[agent] = "unknown"

active = [a for a, s in results.items() if s == "active"]
print(json.dumps({"agents": results, "active_count": len(active), "total": len(AGENTS)}))
