#!/usr/bin/env python3
"""
Skill: stealth_restart
Specter Voss restarts a baza service on the main server.
Requires Serge's approval via Telegram.

Usage: ##SKILL:stealth_restart{"service":"baza-dashboard.service"}##
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "openclaw", "tools"))
from stealth_upgrade import restart_service

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
service = args.get("service", "")

if not service:
    print("Error: 'service' name required (must start with 'baza-')")
    exit(1)

result = restart_service(service_name=service)
if result.get("success"):
    print(f"Service '{service}' restarted: {result.get('status', 'active')}")
else:
    reason = result.get("reason", result.get("stderr", "unknown error"))
    print(f"Restart failed: {reason}")
