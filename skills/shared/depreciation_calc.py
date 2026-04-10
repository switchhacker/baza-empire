#!/usr/bin/env python3
"""Skill: depreciation_calc — Equipment straight-line depreciation.
Usage: ##SKILL:depreciation_calc{"cost":25000,"salvage":2000,"life_years":7}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
cost = float(args.get("cost",0))
salvage = float(args.get("salvage",0))
years = int(args.get("life_years",5))
annual = (cost - salvage) / years
print(f"Depreciation Schedule (Straight-Line)")
print(f"  Cost: ${cost:,.2f}")
print(f"  Salvage: ${salvage:,.2f}")
print(f"  Life: {years} years")
print(f"  Annual depreciation: ${annual:,.2f}")
print(f"\n  Year  Book Value    Depreciation")
bv = cost
for y in range(1, years+1):
    bv -= annual
    print(f"  {y:>4}  ${bv:>10,.2f}  ${annual:>10,.2f}")
