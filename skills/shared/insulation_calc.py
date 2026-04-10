#!/usr/bin/env python3
"""Skill: insulation_calc — R-value and material qty by climate zone.
Usage: ##SKILL:insulation_calc{"area":"walls","sqft":1200,"zone":4}##"""
import os, json, math
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
area = args.get("area","walls")
sqft = float(args.get("sqft",0))
zone = int(args.get("zone",4))
recs = {
    "walls": {1:"R-13",2:"R-13",3:"R-13",4:"R-13 to R-21",5:"R-20",6:"R-20 to R-30",7:"R-21 to R-30"},
    "attic": {1:"R-30",2:"R-30",3:"R-38",4:"R-38 to R-49",5:"R-49",6:"R-49 to R-60",7:"R-49 to R-60"},
    "floor": {1:"R-13",2:"R-13",3:"R-19",4:"R-25",5:"R-25 to R-30",6:"R-25 to R-30",7:"R-25 to R-30"},
}
rec = recs.get(area,recs["walls"]).get(zone,"R-19")
# Fiberglass batts: ~$0.50-1.00/sqft
batts = math.ceil(sqft / 77)  # 77 sqft per roll
print(f"Insulation Calculator")
print(f"  Area: {area} | {sqft:.0f} sqft | Zone {zone} (PA = zone 4-5)")
print(f"  Recommended: {rec}")
print(f"  Fiberglass batts: {batts} rolls (77 sqft/roll)")
print(f"  Est cost: ${sqft*0.75:,.0f}-${sqft*1.50:,.0f} (material + labor)")
