#!/usr/bin/env python3
"""Skill: profit_margin — Revenue - costs = margin.
Usage: ##SKILL:profit_margin{"revenue":100000,"material":30000,"labor":35000,"overhead":15000}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
rev = float(args.get("revenue",0))
mat = float(args.get("material",0))
lab = float(args.get("labor",0))
ovh = float(args.get("overhead",0))
other = float(args.get("other",0))
total_cost = mat + lab + ovh + other
gross = rev - total_cost
margin = (gross/rev*100) if rev else 0
print(f"Profit Margin Analysis")
print(f"  Revenue: ${rev:,.2f}")
print(f"  Material: ${mat:,.2f}")
print(f"  Labor: ${lab:,.2f}")
print(f"  Overhead: ${ovh:,.2f}")
if other: print(f"  Other: ${other:,.2f}")
print(f"  Total costs: ${total_cost:,.2f}")
print(f"  Gross profit: ${gross:,.2f}")
print(f"  Margin: {margin:.1f}%")
print(f"  Health: {'Healthy' if margin >= 20 else 'Thin' if margin >= 10 else 'Danger'}")
