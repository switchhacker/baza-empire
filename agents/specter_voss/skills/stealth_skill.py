#!/usr/bin/env python3
"""
Skill: stealth_skill
Specter Voss deploys a new skill to the main baza server.
Requires Serge's approval via Telegram.

Usage: ##SKILL:stealth_skill{"name":"my_skill","code":"#!/usr/bin/env python3\nprint('hello')","description":"Does a thing"}##
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "openclaw", "tools"))
from stealth_upgrade import deploy_skill

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
name = args.get("name", "")
code = args.get("code", "")
description = args.get("description", "")
agent_specific = args.get("agent", "")  # empty = shared skill

if not name or not code:
    print("Error: 'name' and 'code' are required")
    exit(1)

result = deploy_skill(name=name, code=code, description=description, agent_specific=agent_specific)
if result.get("success"):
    print(f"Skill '{name}' deployed successfully")
else:
    reason = result.get("reason", result.get("stderr", "unknown error"))
    print(f"Skill deploy failed: {reason}")
