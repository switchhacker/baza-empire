#!/usr/bin/env python3
"""Calculate countdown to a date."""
import os, json
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
target = args.get("date", "")
try:
    t = datetime.strptime(target, "%Y-%m-%d"); delta = t - datetime.now()
    print(json.dumps({"days": delta.days, "hours": delta.seconds//3600, "target": target, "past": delta.days < 0}))
except: print(json.dumps({"error": "Provide date in YYYY-MM-DD format"}))
