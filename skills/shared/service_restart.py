#!/usr/bin/env python3
"""Restart a Baza systemd service."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
service = args.get("service", "")

ALLOWED = {
    "dashboard": "baza-dashboard",
    "agents": "baza-agents",
    "tool-server": "baza-tool-server",
    "task-runner": "baza-task-runner",
}

if service not in ALLOWED:
    print(json.dumps({"error": f"Unknown service '{service}'. Allowed: {list(ALLOWED.keys())}"}))
    exit()

svc = ALLOWED[service]
try:
    subprocess.run(["systemctl", "restart", f"{svc}.service"], capture_output=True, text=True, timeout=15)
    result = subprocess.run(["systemctl", "is-active", f"{svc}.service"], capture_output=True, text=True, timeout=5)
    status = result.stdout.strip()
    print(json.dumps({"service": svc, "status": status, "result": "restarted"}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
