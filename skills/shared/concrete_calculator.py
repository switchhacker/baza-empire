#!/usr/bin/env python3
"""Skill: concrete_calculator — Cubic yards for slabs/footings.
Usage: ##SKILL:concrete_calculator{"length_ft":20,"width_ft":10,"depth_in":4}##"""
import os, json, math
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
l = float(args.get("length_ft",0))
w = float(args.get("width_ft",0))
d = float(args.get("depth_in",4))
waste = float(args.get("waste_pct",10))
cu_ft = l * w * (d/12)
cu_yd = cu_ft / 27 * (1 + waste/100)
bags_80lb = math.ceil(cu_ft * (1 + waste/100) / 0.6)
bags_60lb = math.ceil(cu_ft * (1 + waste/100) / 0.45)
print(f"Concrete Calculator")
print(f"  Slab: {l}x{w}ft, {d}in deep")
print(f"  Volume: {cu_ft:.1f} cu ft = {cu_yd:.2f} cu yd (+{waste:.0f}% waste)")
print(f"  Order: {math.ceil(cu_yd*2)/2:.1f} cu yd (round to nearest 0.5)")
print(f"  OR bags: {bags_80lb} x 80lb | {bags_60lb} x 60lb")
print(f"  Est cost: ${cu_yd*150:.0f}-${cu_yd*200:.0f} (delivered)")
