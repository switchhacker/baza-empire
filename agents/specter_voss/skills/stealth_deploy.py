#!/usr/bin/env python3
"""
Skill: stealth_deploy
Specter Voss deploys code updates to main baza server.
Requires Serge's approval via Telegram before executing.

Usage: ##SKILL:stealth_deploy{"branch":"main"}##
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "openclaw", "tools"))
from stealth_upgrade import deploy_code

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
branch = args.get("branch", "main")

result = deploy_code(branch=branch)
if result.get("success"):
    print(f"Deploy successful: {result.get('output', 'done')[:500]}")
else:
    reason = result.get("reason", result.get("stderr", "unknown error"))
    print(f"Deploy failed: {reason}")
