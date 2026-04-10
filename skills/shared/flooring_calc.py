#!/usr/bin/env python3
"""Skill: flooring_calc — Flooring material with waste factor.
Usage: ##SKILL:flooring_calc{"sqft":500,"type":"hardwood","waste_pct":10}##"""
import os, json, math
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
sqft = float(args.get("sqft",0))
ftype = args.get("type","hardwood")
waste = float(args.get("waste_pct",10))
rates = {"hardwood":"$6-12/sqft","laminate":"$2-5/sqft","vinyl_plank":"$3-7/sqft","tile":"$4-10/sqft","carpet":"$2-6/sqft"}
total = sqft * (1 + waste/100)
boxes = math.ceil(total / float(args.get("box_sqft",20)))
print(f"Flooring Calculator")
print(f"  Area: {sqft:.0f} sqft | Type: {ftype}")
print(f"  Waste: {waste:.0f}% → Order: {total:.0f} sqft")
print(f"  Boxes ({args.get('box_sqft',20)} sqft/box): {boxes}")
print(f"  Material rate: {rates.get(ftype,'$3-8/sqft')}")
print(f"  Underlayment: {math.ceil(sqft/100)} rolls (100sqft/roll)")
