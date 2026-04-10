#!/usr/bin/env python3
"""Skill: drywall_calculator — Sheets of drywall for a room.
Usage: ##SKILL:drywall_calculator{"length":12,"width":10,"height":8}##"""
import os, json, math
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
l,w,h = float(args.get("length",0)), float(args.get("width",0)), float(args.get("height",8))
ceiling = args.get("ceiling", True)
wall_sqft = 2*(l+w)*h
ceil_sqft = l*w if ceiling else 0
total = wall_sqft + ceil_sqft
sheets_4x8 = math.ceil(total / 32 * 1.1)
sheets_4x12 = math.ceil(total / 48 * 1.1)
mud_buckets = math.ceil(total / 1000)
tape_rolls = math.ceil(total / 500)
print(f"Drywall Calculator")
print(f"  Room: {l}x{w}x{h}ft")
print(f"  Wall: {wall_sqft:.0f} sqft | Ceiling: {ceil_sqft:.0f} sqft")
print(f"  Total: {total:.0f} sqft (+10% waste)")
print(f"  4x8 sheets: {sheets_4x8} | 4x12 sheets: {sheets_4x12}")
print(f"  Joint compound: {mud_buckets} bucket(s)")
print(f"  Tape: {tape_rolls} roll(s)")
