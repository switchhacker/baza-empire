#!/usr/bin/env python3
"""Skill: disk_alert — Check disk usage, alert if over threshold.
Usage: ##SKILL:disk_alert{"threshold":80}##"""
import os, json, subprocess
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
threshold = int(args.get("threshold",80))
r = subprocess.run(["df","-h","--output=source,pcent,avail,target"], capture_output=True, text=True, timeout=5)
alerts = []
for line in r.stdout.strip().split("\n")[1:]:
    parts = line.split()
    if len(parts) >= 4 and "%" in parts[1]:
        pct = int(parts[1].replace("%",""))
        if pct >= threshold:
            alerts.append(f"🔴 {parts[3]} at {pct}% (avail: {parts[2]})")
        elif pct >= threshold - 10:
            alerts.append(f"🟡 {parts[3]} at {pct}% (avail: {parts[2]})")
if alerts:
    print(f"Disk Alerts (threshold: {threshold}%)")
    for a in alerts: print(f"  {a}")
else:
    print(f"All disks below {threshold}% ✅")
