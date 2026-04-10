#!/usr/bin/env python3
"""Skill: client_onboard — Client onboarding checklist.
Usage: ##SKILL:client_onboard{"client":"John Smith","project":"Kitchen Remodel"}##"""
import os, json
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
print(f"CLIENT ONBOARDING CHECKLIST")
print(f"{'='*50}")
print(f"Client: {args.get('client','N/A')}")
print(f"Project: {args.get('project','N/A')}")
print(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
items = [
    "[ ] Welcome email/call completed",
    "[ ] Contract signed",
    "[ ] Deposit received",
    "[ ] Insurance certificate sent",
    "[ ] Project timeline shared",
    "[ ] Material selections confirmed",
    "[ ] Permit applications filed",
    "[ ] Subcontractors scheduled",
    "[ ] Pre-construction photos taken",
    "[ ] Emergency contact collected",
    "[ ] HOA approval (if applicable)",
    "[ ] Neighbor notification (if applicable)",
    "[ ] Dumpster ordered",
    "[ ] Port-a-potty ordered (if needed)",
    "[ ] Kickoff meeting scheduled",
]
for item in items:
    print(f"  {item}")
