#!/usr/bin/env python3
"""Check status of a specific systemd service."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
service = args.get("service", "")

if not service:
    print(json.dumps({"error": "No service name provided"}))
else:
    try:
        result = subprocess.run(["systemctl", "status", service],
                              capture_output=True, text=True, timeout=10)
        active = "active" in result.stdout.split("\n")[2] if len(result.stdout.split("\n")) > 2 else False
        lines = result.stdout.strip().split("\n")
        print(json.dumps({
            "service": service,
            "active": active,
            "status_line": lines[2].strip() if len(lines) > 2 else "unknown",
            "details": "\n".join(lines[:10])
        }))
    except Exception as e:
        print(json.dumps({"service": service, "error": str(e)}))
