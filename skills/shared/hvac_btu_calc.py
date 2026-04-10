#!/usr/bin/env python3
"""Skill: hvac_btu_calc — BTU sizing for rooms.
Usage: ##SKILL:hvac_btu_calc{"sqft":300,"ceiling_height":8,"windows":2,"sun":"south"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
sqft = float(args.get("sqft",0))
base_btu = sqft * 20
height = float(args.get("height",8))
if height > 8: base_btu *= 1.1 * ((height-8)/2 + 1)
windows = int(args.get("windows",0))
base_btu += windows * 1000
sun = args.get("sun","").lower()
if sun in ("south","west"): base_btu *= 1.1
occupants = int(args.get("occupants",2))
base_btu += max(0, occupants - 2) * 600
if args.get("kitchen",False): base_btu += 4000
print(f"HVAC BTU Calculator")
print(f"  Room: {sqft:.0f} sqft, {height}ft ceiling")
print(f"  Windows: {windows} | Sun exposure: {sun or 'none'}")
print(f"  Required: {base_btu:,.0f} BTU/hr")
print(f"  Tons: {base_btu/12000:.1f}")
print(f"  Recommended unit: {base_btu/12000:.0f}-ton")
