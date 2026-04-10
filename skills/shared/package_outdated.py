#!/usr/bin/env python3
"""Skill: package_outdated — Check for outdated pip packages.
Usage: ##SKILL:package_outdated{}##"""
import subprocess
r = subprocess.run(["pip","list","--outdated","--format=json"], capture_output=True, text=True, timeout=30)
try:
    import json
    pkgs = json.loads(r.stdout)
    if pkgs:
        print(f"Outdated Packages ({len(pkgs)}):")
        for p in pkgs[:20]:
            print(f"  {p['name']:<25} {p['version']:>10} → {p['latest_version']}")
        if len(pkgs) > 20: print(f"  ...+{len(pkgs)-20} more")
    else:
        print("All packages up to date ✅")
except: print(r.stdout[:500] if r.stdout else "Could not check packages")
