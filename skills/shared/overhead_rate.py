#!/usr/bin/env python3
"""Skill: overhead_rate — Calculate overhead rate per job.
Usage: ##SKILL:overhead_rate{"annual_overhead":120000,"annual_revenue":600000}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
overhead = float(args.get("annual_overhead",0))
revenue = float(args.get("annual_revenue",0))
if revenue <= 0: print("Error: revenue required"); exit()
rate = (overhead / revenue) * 100
per_dollar = overhead / revenue
print(f"Overhead Rate Calculator")
print(f"  Annual overhead: ${overhead:,.2f}")
print(f"  Annual revenue: ${revenue:,.2f}")
print(f"  Overhead rate: {rate:.1f}%")
print(f"  Per revenue dollar: ${per_dollar:.2f}")
print(f"  Target: {'Good (<20%)' if rate < 20 else 'Average (20-30%)' if rate < 30 else 'High (>30%) — review costs'}")
