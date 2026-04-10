#!/usr/bin/env python3
"""Skill: hourly_to_annual — Hourly rate ↔ annual salary conversion.
Usage: ##SKILL:hourly_to_annual{"hourly":35,"hours_per_week":40}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
if args.get("hourly"):
    hourly = float(args.get("hourly"))
    weekly_hrs = float(args.get("hours_per_week",40))
    annual = hourly * weekly_hrs * 52
    monthly = annual / 12
    weekly = hourly * weekly_hrs
    print(f"Hourly → Annual")
    print(f"  Hourly: ${hourly:.2f}")
    print(f"  Weekly ({weekly_hrs:.0f}h): ${weekly:,.2f}")
    print(f"  Monthly: ${monthly:,.2f}")
    print(f"  Annual: ${annual:,.2f}")
elif args.get("annual"):
    annual = float(args.get("annual"))
    weekly_hrs = float(args.get("hours_per_week",40))
    hourly = annual / 52 / weekly_hrs
    print(f"Annual → Hourly")
    print(f"  Annual: ${annual:,.2f}")
    print(f"  Hourly ({weekly_hrs:.0f}h/wk): ${hourly:.2f}")
else:
    print("Error: provide hourly or annual")
