#!/usr/bin/env python3
"""Skill: roi_calculator — Return on investment calculator.
Usage: ##SKILL:roi_calculator{"investment":50000,"revenue":75000,"period":"1 year"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
inv = float(args.get("investment",0))
rev = float(args.get("revenue",0))
period = args.get("period","1 year")
if inv <= 0: print("Error: investment must be > 0"); exit()
profit = rev - inv
roi = (profit / inv) * 100
print(f"ROI Calculator")
print(f"  Investment: ${inv:,.2f}")
print(f"  Revenue: ${rev:,.2f}")
print(f"  Profit: ${profit:,.2f}")
print(f"  ROI: {roi:.1f}%")
print(f"  Period: {period}")
print(f"  Verdict: {'Profitable' if roi > 0 else 'Break-even' if roi == 0 else 'Loss'}")
