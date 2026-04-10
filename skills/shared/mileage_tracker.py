#!/usr/bin/env python3
"""Skill: mileage_tracker — IRS mileage rate calculator.
Usage: ##SKILL:mileage_tracker{"miles":150,"purpose":"job site visit"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
miles = float(args.get("miles",0))
rate = float(args.get("rate", 0.70))  # 2025 IRS rate
purpose = args.get("purpose","business")
deduction = miles * rate
print(f"Mileage Deduction")
print(f"  Miles: {miles:.1f}")
print(f"  IRS rate: ${rate}/mile")
print(f"  Deduction: ${deduction:.2f}")
print(f"  Purpose: {purpose}")
from datetime import datetime
print(f"  Date: {datetime.now().strftime('%Y-%m-%d')}")
