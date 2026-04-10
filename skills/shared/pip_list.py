#!/usr/bin/env python3
"""List installed pip packages."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
search = args.get("search", "")

VENV_PIP = "/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/pip"

try:
    result = subprocess.run([VENV_PIP, "list", "--format=json"],
                          capture_output=True, text=True, timeout=30)
    packages = json.loads(result.stdout)
    if search:
        packages = [p for p in packages if search.lower() in p["name"].lower()]
    print(json.dumps({"packages": packages, "count": len(packages)}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
