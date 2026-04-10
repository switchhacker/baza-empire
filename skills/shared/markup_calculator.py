#!/usr/bin/env python3
"""Skill: markup_calculator — Cost + markup = price.
Usage: ##SKILL:markup_calculator{"material":5000,"labor":3000,"markup_pct":30}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
material = float(args.get("material",0))
labor = float(args.get("labor",0))
overhead = float(args.get("overhead",0))
markup = float(args.get("markup_pct",30))
cost = material + labor + overhead
profit = cost * (markup/100)
price = cost + profit
print(f"Markup Calculator")
print(f"  Material: ${material:,.2f}")
print(f"  Labor: ${labor:,.2f}")
if overhead: print(f"  Overhead: ${overhead:,.2f}")
print(f"  Total cost: ${cost:,.2f}")
print(f"  Markup: {markup:.0f}% = ${profit:,.2f}")
print(f"  Sell price: ${price:,.2f}")
print(f"  Margin: {(profit/price*100):.1f}%")
