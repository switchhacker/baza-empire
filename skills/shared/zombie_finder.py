#!/usr/bin/env python3
"""Skill: zombie_finder — Find zombie/defunct processes.
Usage: ##SKILL:zombie_finder{}##"""
import subprocess
r = subprocess.run(["ps","aux"], capture_output=True, text=True, timeout=5)
zombies = [l for l in r.stdout.split("\n") if " Z " in l or "defunct" in l]
if zombies:
    print(f"Found {len(zombies)} zombie process(es):")
    for z in zombies: print(f"  {z[:120]}")
else:
    print("No zombie processes found ✅")
