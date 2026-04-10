#!/usr/bin/env python3
"""Create calendar events from project dates/phases."""
import os, json
from datetime import datetime, timedelta

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
project = args.get("project", "Project")
start = args.get("start_date", datetime.now().strftime("%Y-%m-%d"))
phases = args.get("phases", [])  # [{"name": "Demo", "days": 3}]

events = []
current = datetime.strptime(start, "%Y-%m-%d")
for phase in phases:
    days = int(phase.get("days", 5))
    end = current + timedelta(days=days - 1)
    events.append({
        "title": f"{project} - {phase.get('name', 'Phase')}",
        "start": current.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "days": days,
        "all_day": True
    })
    current = end + timedelta(days=1)
    # Skip weekends
    while current.weekday() >= 5:
        current += timedelta(days=1)

print(json.dumps({
    "project": project,
    "total_events": len(events),
    "project_start": start,
    "project_end": current.strftime("%Y-%m-%d"),
    "events": events
}))
