#!/usr/bin/env python3
"""Skill: systemd_failed — List failed systemd services.
Usage: ##SKILL:systemd_failed{}##"""
import subprocess
r = subprocess.run(["systemctl","--failed","--no-pager","--no-legend"], capture_output=True, text=True, timeout=5)
lines = [l.strip() for l in r.stdout.strip().split("\n") if l.strip()]
if lines:
    print(f"Failed Services ({len(lines)}):")
    for l in lines: print(f"  🔴 {l[:100]}")
else:
    print("No failed services ✅")
