#!/usr/bin/env python3
"""Skill: staircase_calc — Rise/run/tread for stairs.
Usage: ##SKILL:staircase_calc{"total_rise_in":108,"total_run_in":144}##"""
import os, json, math
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
rise = float(args.get("total_rise_in",108))
num_risers = round(rise / 7.5)
riser_height = rise / num_risers
tread_depth = float(args.get("tread_depth",10))
num_treads = num_risers - 1
total_run = num_treads * tread_depth
angle = math.degrees(math.atan(rise / total_run)) if total_run else 0
code_ok = 4 <= riser_height <= 7.75 and tread_depth >= 10
print(f"Staircase Calculator")
print(f"  Total rise: {rise:.1f}in ({rise/12:.1f}ft)")
print(f"  Risers: {num_risers} at {riser_height:.2f}in each")
print(f"  Treads: {num_treads} at {tread_depth:.1f}in deep")
print(f"  Total run: {total_run:.1f}in ({total_run/12:.1f}ft)")
print(f"  Angle: {angle:.1f}°")
print(f"  Code compliant: {'✅ Yes' if code_ok else '❌ No — adjust riser/tread'}")
