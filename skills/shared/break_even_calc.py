#!/usr/bin/env python3
"""Skill: break_even_calc — Break-even analysis.
Usage: ##SKILL:break_even_calc{"fixed_costs":5000,"price_per_unit":150,"cost_per_unit":80}##"""
import os, json, math
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
fixed = float(args.get("fixed_costs",0))
price = float(args.get("price_per_unit",0))
cost = float(args.get("cost_per_unit",0))
if price <= cost: print("Error: price must exceed unit cost"); exit()
margin = price - cost
units = math.ceil(fixed / margin)
revenue = units * price
print(f"Break-Even Analysis")
print(f"  Fixed costs: ${fixed:,.2f}")
print(f"  Price/unit: ${price:,.2f}")
print(f"  Cost/unit: ${cost:,.2f}")
print(f"  Margin/unit: ${margin:,.2f}")
print(f"  Break-even: {units} units")
print(f"  Revenue at break-even: ${revenue:,.2f}")
