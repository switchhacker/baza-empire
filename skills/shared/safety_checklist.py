#!/usr/bin/env python3
"""Skill: safety_checklist — OSHA compliance checklist for job sites.
Usage: ##SKILL:safety_checklist{"type":"general"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
ctype = args.get("type","general")
checklists = {
  "general":["Hard hats worn on site","Safety glasses available","First aid kit stocked","Fire extinguisher accessible","Emergency numbers posted","Fall protection for heights >6ft","Electrical panels accessible","Proper ladder usage","Housekeeping — clear walkways","Tool inspection completed"],
  "electrical":["GFCI protection on all circuits","Lockout/tagout procedures","Wire gauge matches load","Panel clearance 36in minimum","Ground fault protection","Arc flash labels posted","NM cable properly secured","Junction boxes covered"],
  "roofing":["Fall protection system in place","Roof edge barriers installed","Ladder extends 3ft above roof","Weather check completed","Debris nets below work area","Hard hats mandatory","Toe boards installed","Safety harness inspected"],
}
items = checklists.get(ctype, checklists["general"])
print(f"Safety Checklist — {ctype.title()}")
print(f"{'='*40}")
for i, item in enumerate(items, 1):
    print(f"  [ ] {i}. {item}")
print(f"\nDate: ____________  Supervisor: ____________")
