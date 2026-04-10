#!/usr/bin/env python3
"""Skill: sales_tax_calc — Calculate sales tax.
Usage: ##SKILL:sales_tax_calc{"amount":5000,"state":"PA"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
amount = float(args.get("amount",0))
state = args.get("state","PA").upper()
rates = {"PA":0.06,"NJ":0.06625,"NY":0.08,"DE":0,"CA":0.0725,"FL":0.06,"TX":0.0625,"IL":0.0625}
rate = rates.get(state, 0.06)
tax = amount * rate
print(f"Sales Tax Calculator")
print(f"  Amount: ${amount:,.2f}")
print(f"  State: {state} ({rate*100:.3f}%)")
print(f"  Tax: ${tax:,.2f}")
print(f"  Total: ${amount+tax:,.2f}")
if state == "PA":
    print(f"  Note: PA exempts most construction labor; tax applies to materials only")
