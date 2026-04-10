#!/usr/bin/env python3
"""Skill: scope_of_work — Generate scope of work document.
Usage: ##SKILL:scope_of_work{"project":"Bathroom Remodel","client":"John Smith","tasks":["demo existing tile","install new vanity","paint walls"]}##"""
import os, json
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
print(f"SCOPE OF WORK")
print(f"{'='*50}")
print(f"Project: {args.get('project','N/A')}")
print(f"Client: {args.get('client','N/A')}")
print(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
print(f"\nAll Home Building Co LLC ('Contractor') agrees to perform:")
for i, task in enumerate(args.get("tasks",[]), 1):
    print(f"  {i}. {task}")
print(f"\nExclusions: {args.get('exclusions','None specified')}")
print(f"Timeline: {args.get('timeline','To be determined')}")
print(f"Payment terms: {args.get('payment','50% deposit, 50% on completion')}")
print(f"\nContractor: All Home Building Co LLC")
print(f"Client: {args.get('client','____________')}")
