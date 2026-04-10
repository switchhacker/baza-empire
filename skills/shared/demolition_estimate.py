#!/usr/bin/env python3
"""Skill: demolition_estimate — Demo cost estimator by room type.
Usage: ##SKILL:demolition_estimate{"room":"kitchen","sqft":200}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
room = args.get("room","general").lower()
sqft = float(args.get("sqft",0))
rates = {"kitchen":{"demo":8,"haul":3},"bathroom":{"demo":10,"haul":4},"basement":{"demo":5,"haul":2},"general":{"demo":6,"haul":2},"deck":{"demo":4,"haul":3},"roof":{"demo":3,"haul":2}}
r = rates.get(room, rates["general"])
demo_cost = sqft * r["demo"]
haul_cost = sqft * r["haul"]
dumpster = "10yd" if sqft < 200 else "20yd" if sqft < 500 else "30yd"
print(f"Demolition Estimate — {room.title()}")
print(f"  Area: {sqft:.0f} sqft")
print(f"  Demo labor: ${demo_cost:,.0f} (${r['demo']}/sqft)")
print(f"  Haul/disposal: ${haul_cost:,.0f} (${r['haul']}/sqft)")
print(f"  Total: ${demo_cost+haul_cost:,.0f}")
print(f"  Dumpster: {dumpster} recommended")
print(f"  Duration: {max(1, int(sqft/200))} day(s)")
