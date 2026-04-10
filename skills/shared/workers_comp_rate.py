#!/usr/bin/env python3
"""Skill: workers_comp_rate — Estimate workers comp cost.
Usage: ##SKILL:workers_comp_rate{"annual_payroll":200000,"class":"carpentry"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
payroll = float(args.get("annual_payroll",0))
wc_class = args.get("class","general").lower()
rates_per_100 = {"carpentry":12.50,"electrical":6.80,"plumbing":5.90,"roofing":25.00,"general":8.50,"masonry":15.00,"painting":7.50,"office":0.50,"demolition":18.00}
rate = rates_per_100.get(wc_class, 8.50)
cost = payroll / 100 * rate
print(f"Workers Comp Estimate")
print(f"  Annual payroll: ${payroll:,.2f}")
print(f"  Class: {wc_class}")
print(f"  Rate: ${rate:.2f} per $100 payroll")
print(f"  Annual premium: ${cost:,.2f}")
print(f"  Monthly: ${cost/12:,.2f}")
