#!/usr/bin/env python3
"""Skill: waste_dumpster_calc — Dumpster size from project specs.
Usage: ##SKILL:waste_dumpster_calc{"project":"kitchen_remodel","sqft":200}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
project = args.get("project","general")
sqft = float(args.get("sqft",0))
sizes = {"10yd":{"cost":"$350-500","fits":"small bathroom, single room demo"},"20yd":{"cost":"$450-650","fits":"kitchen remodel, multi-room"},"30yd":{"cost":"$550-800","fits":"full gut, roofing, large renovation"},"40yd":{"cost":"$700-1000","fits":"new construction, total demo"}}
if sqft <= 150: rec = "10yd"
elif sqft <= 400: rec = "20yd"
elif sqft <= 800: rec = "30yd"
else: rec = "40yd"
print(f"Dumpster Calculator — {project}")
print(f"  Project: {sqft:.0f} sqft → Recommended: {rec}")
print(f"  Cost: {sizes[rec]['cost']}")
print(f"  Fits: {sizes[rec]['fits']}")
print(f"\nAll sizes:")
for s, d in sizes.items():
    flag = " ← RECOMMENDED" if s == rec else ""
    print(f"  {s}: {d['cost']} ({d['fits']}){flag}")
