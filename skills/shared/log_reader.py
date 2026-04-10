#!/usr/bin/env python3
"""Read last N lines of a log file."""
import os, json
from collections import deque

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
filepath = args.get("file", "")
lines_count = int(args.get("lines", 50))
search = args.get("search", "")

if not filepath:
    print(json.dumps({"error": "No file path provided"}))
elif not os.path.exists(filepath):
    print(json.dumps({"error": f"File not found: {filepath}"}))
else:
    try:
        with open(filepath, "r", errors="replace") as f:
            if search:
                lines = [l.rstrip() for l in f if search.lower() in l.lower()]
                lines = lines[-lines_count:]
            else:
                lines = list(deque(f, maxlen=lines_count))
                lines = [l.rstrip() for l in lines]
        print(json.dumps({"file": filepath, "lines": lines, "count": len(lines)}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
