#!/usr/bin/env python3
"""Skill: container_logs — Read docker container logs.
Usage: ##SKILL:container_logs{"container":"n8n","lines":50}##"""
import os, json, subprocess
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
name = args.get("container","")
lines = int(args.get("lines",50))
if not name: print("Error: container name required"); exit(1)
try:
    r = subprocess.run(["docker","logs","--tail",str(lines),name], capture_output=True, text=True, timeout=10)
    print(r.stdout[-3000:] if r.returncode==0 else f"Error: {r.stderr[:500]}")
except Exception as e: print(f"Error: {e}")
