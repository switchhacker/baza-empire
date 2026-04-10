#!/usr/bin/env python3
"""Skill: daily_field_log — Generate daily construction log entry.
Usage: ##SKILL:daily_field_log{"project":"Kitchen Remodel","crew_size":3,"work_done":"demo cabinets","weather":"clear"}##"""
import os, json
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
print(f"DAILY FIELD LOG")
print(f"{'='*50}")
print(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
print(f"Project: {args.get('project','N/A')}")
print(f"Weather: {args.get('weather','N/A')}")
print(f"Crew: {args.get('crew_size','N/A')} workers")
print(f"Hours: {args.get('hours','8')}h ({args.get('start','7:00 AM')}-{args.get('end','3:30 PM')})")
print(f"\nWork Completed:")
print(f"  {args.get('work_done','N/A')}")
if args.get("materials"):
    print(f"\nMaterials Used:")
    print(f"  {args.get('materials')}")
if args.get("issues"):
    print(f"\nIssues/Delays:")
    print(f"  {args.get('issues')}")
print(f"\nTomorrow's Plan: {args.get('tomorrow','TBD')}")
print(f"\nSupervisor: ____________  Date: ____________")
