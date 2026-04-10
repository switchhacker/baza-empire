#!/usr/bin/env python3
"""Restart a systemd service."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
service = args.get("service", "")
action = args.get("action", "restart")  # restart, start, stop

ALLOWED = ["baza-agents", "baza-task-runner", "baza-tool-server", "baza-dashboard",
           "ollama", "ollama-nvidia", "redis", "postgresql"]

if not service:
    print(json.dumps({"error": "No service name provided"}))
elif not any(service.startswith(a) for a in ALLOWED):
    print(json.dumps({"error": f"Service '{service}' not in allowed list", "allowed": ALLOWED}))
else:
    try:
        result = subprocess.run(["sudo", "systemctl", action, service],
                              capture_output=True, text=True, timeout=30)
        # Check new status
        status = subprocess.run(["systemctl", "is-active", service],
                              capture_output=True, text=True, timeout=5)
        print(json.dumps({
            "service": service, "action": action,
            "success": result.returncode == 0,
            "new_status": status.stdout.strip(),
            "stderr": result.stderr.strip() if result.returncode != 0 else ""
        }))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
