#!/usr/bin/env python3
"""
Simon Bately Skill: daily_briefing
Runs all briefing data sources and prints a combined morning briefing.
"""
import os
import sys
import json
import subprocess
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SHARED_DIR = os.path.join(BASE_DIR, "skills", "shared")
PYTHON = sys.executable

def run_skill(script_name, args={}):
    path = os.path.join(SHARED_DIR, script_name)
    if not os.path.exists(path):
        return f"[{script_name}] not found"
    env = os.environ.copy()
    env["SKILL_ARGS"] = json.dumps(args)
    env["EMPIRE_LOCATION"] = os.environ.get("EMPIRE_LOCATION", "Philadelphia, PA")
    result = subprocess.run(
        [PYTHON, path],
        capture_output=True, text=True, timeout=20, env=env
    )
    output = result.stdout.strip() or result.stderr.strip() or "(no output)"
    lines = [l for l in output.splitlines() if not l.startswith("===")]
    return "\n".join(lines).strip()

location = os.environ.get("EMPIRE_LOCATION", "Philadelphia, PA")
now = datetime.now().strftime("%A, %B %d %Y — %I:%M %p")

print(f"🌅 Good morning, Serge. Here's your {now} briefing.\n")

# Weather
print(f"🌤️  WEATHER — {location}")
print(run_skill("weather.py", {"location": location}))

# News
print("\n📰 NEWS")
print(run_skill("news.py", {"category": "business"}))

print("\n✅ Briefing complete.")
