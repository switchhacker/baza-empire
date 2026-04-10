#!/usr/bin/env python3
"""Skill: memory_profile — Top memory consumers.
Usage: ##SKILL:memory_profile{"top":10}##"""
import os, json, subprocess
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
n = int(args.get("top",10))
r = subprocess.run(["ps","aux","--sort=-%mem"], capture_output=True, text=True, timeout=5)
lines = r.stdout.strip().split("\n")
print(f"Top {n} Memory Consumers")
print(f"{'USER':<10} {'%MEM':>5} {'RSS':>8} {'COMMAND'}")
for line in lines[1:n+1]:
    parts = line.split(None, 10)
    if len(parts) >= 11:
        print(f"{parts[0]:<10} {parts[3]:>5} {int(parts[5])//1024:>6}MB {parts[10][:60]}")
