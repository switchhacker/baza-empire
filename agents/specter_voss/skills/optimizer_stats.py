#!/usr/bin/env python3
"""
Skill: optimizer_stats
Reports token savings from the context optimizer over a time window.
Reads agent journal logs (which carry the optimizer log line) from baza.

Usage:
    SKILL_ARGS='{}'                # last 24h
    SKILL_ARGS='{"hours":48}'      # custom window
"""
import os
import json
import sys
import subprocess
import re

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
hours = int(args.get("hours", 24))

BAZA_HOST = os.environ.get("BAZA_MAIN_HOST", "100.127.118.103")
BAZA_USER = os.environ.get("BAZA_MAIN_USER", "switchhacker")

# Pull optimizer log lines from journal
remote_cmd = (
    f"sudo journalctl -u 'baza-agent-*' --since '{hours} hours ago' --no-pager 2>&1 "
    f"| grep -E 'optimizer (saved|downgraded)' | tail -200"
)

try:
    proc = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no",
         f"{BAZA_USER}@{BAZA_HOST}", remote_cmd],
        capture_output=True, text=True, timeout=15,
    )
    lines = proc.stdout.strip().split("\n") if proc.stdout.strip() else []
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)

per_agent = {}
total_before = 0
total_after = 0
downgrades = 0

# Match: [agent_id] optimizer saved 23% (1234→987 tokens)
RE_SAVED = re.compile(r"\[(?P<agent>[a-z_]+)\] optimizer saved (?P<pct>\d+(?:\.\d+)?)% \((?P<before>\d+)→(?P<after>\d+) tokens\)")
RE_DOWN  = re.compile(r"\[(?P<agent>[a-z_]+)\] optimizer downgraded (?P<from>\S+) → (?P<to>\S+)")

for line in lines:
    m = RE_SAVED.search(line)
    if m:
        agent = m["agent"]
        before = int(m["before"])
        after = int(m["after"])
        per_agent.setdefault(agent, {"events": 0, "before": 0, "after": 0, "downgrades": 0})
        per_agent[agent]["events"] += 1
        per_agent[agent]["before"] += before
        per_agent[agent]["after"] += after
        total_before += before
        total_after += after
        continue
    m = RE_DOWN.search(line)
    if m:
        agent = m["agent"]
        per_agent.setdefault(agent, {"events": 0, "before": 0, "after": 0, "downgrades": 0})
        per_agent[agent]["downgrades"] += 1
        downgrades += 1

print(f"📊 OPTIMIZER STATS — last {hours}h")
print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
if not per_agent:
    print("No optimizer events recorded yet (agents may need a few requests).")
    sys.exit(0)

print(f"\nTotal events: {sum(p['events'] for p in per_agent.values())}")
saved = total_before - total_after
saved_pct = round(saved * 100 / total_before, 1) if total_before else 0
print(f"Tokens before: {total_before:,}")
print(f"Tokens after:  {total_after:,}")
print(f"Saved:         {saved:,} ({saved_pct}%)")
print(f"Downgrades:    {downgrades}")

print("\nPer-agent breakdown:")
for agent in sorted(per_agent.keys()):
    p = per_agent[agent]
    if p["events"] == 0 and p["downgrades"] == 0:
        continue
    pct = round((p["before"] - p["after"]) * 100 / p["before"], 1) if p["before"] else 0
    print(f"  {agent:18} events={p['events']:3} saved={p['before'] - p['after']:>7,} ({pct}%)  downgrades={p['downgrades']}")

print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print("(set BAZA_OPTIMIZER_OFF=1 on any agent to disable)")
