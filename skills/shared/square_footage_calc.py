#!/usr/bin/env python3
"""
Skill: square_footage_calc
Calculate square footage from length × width dimensions. Supports multiple rooms.
Usage: ##SKILL:square_footage_calc{"rooms":[{"name":"Living Room","length":20,"width":15},{"name":"Kitchen","length":12,"width":10}]}##
"""
import os, json, sys

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
rooms = args.get("rooms", [])

if not rooms:
    length = float(args.get("length", 0))
    width = float(args.get("width", 0))
    if length <= 0 or width <= 0:
        print("Error: Provide 'length' and 'width' or a 'rooms' list.")
        sys.exit(1)
    rooms = [{"name": "Room", "length": length, "width": width}]

total_sqft = 0
lines = ["=== Square Footage Calculator ===", ""]

for room in rooms:
    name = room.get("name", "Room")
    l = float(room.get("length", 0))
    w = float(room.get("width", 0))
    sqft = l * w
    total_sqft += sqft
    lines.append(f"  {name}: {l} ft × {w} ft = {sqft:,.1f} sq ft")

lines.append("")
lines.append(f"  TOTAL: {total_sqft:,.1f} sq ft")
lines.append(f"  ({total_sqft / 9:,.1f} sq yards)")
lines.append(f"  ({total_sqft * 0.0929:,.1f} sq meters)")

print("\n".join(lines))
