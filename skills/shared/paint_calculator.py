#!/usr/bin/env python3
"""
Skill: paint_calculator
Calculate gallons of paint needed for walls and/or ceiling.
Usage: ##SKILL:paint_calculator{"length":20,"width":15,"height":8,"coats":2,"include_ceiling":true}##
"""
import os, json, sys, math

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
length = float(args.get("length", 0))
width = float(args.get("width", 0))
height = float(args.get("height", 8))
coats = int(args.get("coats", 2))
include_ceiling = args.get("include_ceiling", False)
doors = int(args.get("doors", 1))
windows = int(args.get("windows", 2))

if length <= 0 or width <= 0:
    print("Error: Provide 'length' and 'width' in feet.")
    sys.exit(1)

COVERAGE_PER_GALLON = 350  # sq ft per gallon (industry standard)
DOOR_SQFT = 21  # standard 3x7 door
WINDOW_SQFT = 15  # average window

wall_sqft = 2 * (length + width) * height
subtract = (doors * DOOR_SQFT) + (windows * WINDOW_SQFT)
net_wall = max(wall_sqft - subtract, 0)

ceiling_sqft = length * width if include_ceiling else 0
total_sqft = (net_wall + ceiling_sqft) * coats
gallons_needed = total_sqft / COVERAGE_PER_GALLON
gallons_rounded = math.ceil(gallons_needed)

lines = [
    "=== Paint Calculator ===",
    f"  Room: {length} ft × {width} ft × {height} ft high",
    f"  Walls (gross): {wall_sqft:,.0f} sq ft",
    f"  Subtract {doors} door(s) + {windows} window(s): -{subtract:,.0f} sq ft",
    f"  Walls (net): {net_wall:,.0f} sq ft",
]
if include_ceiling:
    lines.append(f"  Ceiling: {ceiling_sqft:,.0f} sq ft")
lines += [
    f"  Coats: {coats}",
    f"  Total paintable area: {total_sqft:,.0f} sq ft",
    f"  Coverage rate: {COVERAGE_PER_GALLON} sq ft/gallon",
    "",
    f"  GALLONS NEEDED: {gallons_rounded} gallons ({gallons_needed:.1f} exact)",
    "",
    "  Tip: Add 10% for textured surfaces or dark-over-light color changes.",
]
print("\n".join(lines))
