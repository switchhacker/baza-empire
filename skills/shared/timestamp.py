#!/usr/bin/env python3
"""Get current timestamp in various formats."""
import os, json
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
now = datetime.now()
print(json.dumps({"iso": now.isoformat(), "date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M:%S"), "epoch": int(now.timestamp()), "human": now.strftime("%B %d, %Y at %I:%M %p"), "day": now.strftime("%A")}))
