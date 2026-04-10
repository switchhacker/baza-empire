#!/usr/bin/env python3
"""Skill: plumbing_fixture_count — Fixture count for permit applications.
Usage: ##SKILL:plumbing_fixture_count{"fixtures":["toilet","sink","shower","dishwasher"]}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
fixtures = args.get("fixtures",[])
units = {"toilet":4,"sink":1,"lavatory":1,"bathtub":2,"shower":2,"dishwasher":2,"washing_machine":2,"floor_drain":1,"hose_bib":1,"water_heater":0,"urinal":4,"mop_sink":3}
total = 0
print(f"Plumbing Fixture Count")
for f in fixtures:
    fu = units.get(f.lower().replace(" ","_"),1)
    total += fu
    print(f"  {f}: {fu} fixture units")
print(f"\nTotal: {total} fixture units")
print(f"Min drain size: {'2in' if total <= 6 else '3in' if total <= 20 else '4in'}")
print(f"Permit required: {'Yes' if total > 0 else 'Check local codes'}")
