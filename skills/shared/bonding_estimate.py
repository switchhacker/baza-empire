#!/usr/bin/env python3
"""Skill: bonding_estimate — Bond cost estimate for projects.
Usage: ##SKILL:bonding_estimate{"project_value":500000,"type":"performance"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
value = float(args.get("project_value",0))
btype = args.get("type","performance")
rates = {"performance":{"pct":2.5,"desc":"guarantees work completion"},"payment":{"pct":2.5,"desc":"guarantees subcontractor/supplier payment"},"bid":{"pct":1.0,"desc":"guarantees bid commitment"},"license":{"pct":1.5,"desc":"required for contractor license"}}
r = rates.get(btype, rates["performance"])
cost = value * (r["pct"]/100)
print(f"Bond Estimate — {btype.title()}")
print(f"  Project value: ${value:,.2f}")
print(f"  Bond type: {btype} ({r['desc']})")
print(f"  Rate: ~{r['pct']:.1f}%")
print(f"  Est premium: ${cost:,.2f}")
print(f"  Note: actual rate depends on credit, experience, project risk")
