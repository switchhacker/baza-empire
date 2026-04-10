#!/usr/bin/env python3
"""List all cron jobs on the system."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
user = args.get("user", "")

crons = []
try:
    cmd = ["crontab", "-l"]
    if user:
        cmd = ["crontab", "-u", user, "-l"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            crons.append({"raw": line, "type": "user_cron"})
except Exception:
    pass

# System timers
try:
    result = subprocess.run(["systemctl", "list-timers", "--no-pager"],
                          capture_output=True, text=True, timeout=10)
    for line in result.stdout.strip().split("\n")[1:]:
        if line.strip() and "timer" in line.lower():
            crons.append({"raw": line.strip(), "type": "systemd_timer"})
except Exception:
    pass

print(json.dumps({"cron_jobs": crons, "count": len(crons)}))
