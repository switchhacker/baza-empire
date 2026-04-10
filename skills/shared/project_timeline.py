#!/usr/bin/env python3
"""Generate project timeline with milestones from phases."""
import os, json
from datetime import datetime, timedelta

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
start_date = args.get("start_date", datetime.now().strftime("%Y-%m-%d"))
phases = args.get("phases", [])  # [{"name": "Demo", "days": 5}, {"name": "Framing", "days": 10}]

timeline = []
current = datetime.strptime(start_date, "%Y-%m-%d")
for phase in phases:
    days = int(phase.get("days", 7))
    end = current + timedelta(days=days)
    timeline.append({
        "phase": phase.get("name", "Unnamed"),
        "start": current.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "duration_days": days
    })
    current = end + timedelta(days=1)

total_days = (current - datetime.strptime(start_date, "%Y-%m-%d")).days
print(json.dumps({
    "project_start": start_date,
    "project_end": (current - timedelta(days=1)).strftime("%Y-%m-%d"),
    "total_days": total_days,
    "phases": timeline
}))
