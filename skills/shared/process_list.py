#!/usr/bin/env python3
"""List top processes by CPU/memory usage."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
sort_by = args.get("sort", "cpu")  # cpu or memory
limit = int(args.get("limit", 15))

try:
    sort_key = "-%cpu" if sort_by == "cpu" else "-%mem"
    result = subprocess.run(
        ["ps", "aux", "--sort", sort_key],
        capture_output=True, text=True, timeout=10)
    lines = result.stdout.strip().split("\n")
    processes = []
    for line in lines[1:limit+1]:
        parts = line.split(None, 10)
        if len(parts) >= 11:
            processes.append({
                "user": parts[0], "pid": parts[1], "cpu": float(parts[2]),
                "mem": float(parts[3]), "vsz": parts[4], "rss": parts[5],
                "command": parts[10][:80]
            })
    print(json.dumps({"sort": sort_by, "processes": processes}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
