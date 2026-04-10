#!/usr/bin/env python3
"""Skill: active_connections — Show active network connections.
Usage: ##SKILL:active_connections{"filter":"ESTABLISHED"}##"""
import os, json, subprocess
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
filt = args.get("filter","ESTABLISHED")
r = subprocess.run(["ss","-tunap","state",filt.lower()], capture_output=True, text=True, timeout=5)
lines = r.stdout.strip().split("\n")
print(f"Active Connections ({filt}): {max(0,len(lines)-1)}")
for line in lines[:21]:
    print(f"  {line[:120]}")
if len(lines) > 21: print(f"  ...+{len(lines)-21} more")
