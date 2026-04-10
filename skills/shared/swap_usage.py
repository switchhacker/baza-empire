#!/usr/bin/env python3
"""Skill: swap_usage — Check swap usage and top consumers.
Usage: ##SKILL:swap_usage{}##"""
import subprocess
r = subprocess.run(["free","-h"], capture_output=True, text=True, timeout=5)
for line in r.stdout.strip().split("\n"):
    if "Swap" in line:
        print(f"Swap: {line.strip()}")
        break
r2 = subprocess.run("for f in /proc/*/status; do awk '/VmSwap/{s=$2} /Name/{n=$2} END{if(s>0) printf "%8d KB %s\n",s,n}' $f 2>/dev/null; done | sort -rn | head -5", shell=True, capture_output=True, text=True, timeout=10)
if r2.stdout.strip():
    print("Top swap consumers:")
    for line in r2.stdout.strip().split("\n"):
        print(f"  {line.strip()}")
else:
    print("No significant swap consumers")
