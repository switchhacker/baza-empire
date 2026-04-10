#!/usr/bin/env python3
"""Install a pip package in the venv."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
package = args.get("package", "")
upgrade = args.get("upgrade", False)

VENV_PIP = "/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/pip"

if not package:
    print(json.dumps({"error": "No package name provided"}))
else:
    try:
        cmd = [VENV_PIP, "install"]
        if upgrade:
            cmd.append("--upgrade")
        cmd.append(package)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print(json.dumps({
            "package": package, "success": result.returncode == 0,
            "output": result.stdout.strip()[-500:],
            "error": result.stderr.strip()[-500:] if result.returncode != 0 else ""
        }))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
