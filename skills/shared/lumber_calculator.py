#!/usr/bin/env python3
"""Skill: lumber_calculator — Board feet and stud counts.
Usage: ##SKILL:lumber_calculator{"wall_length_ft":40,"height":8,"stud_spacing":16}##"""
import os, json, math
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
length = float(args.get("wall_length_ft",0))
height = float(args.get("height",8))
spacing = float(args.get("stud_spacing",16))
studs = math.ceil(length * 12 / spacing) + 1
plates = math.ceil(length / 8) * 3  # top, bottom, double top
headers = int(args.get("headers",0))
print(f"Lumber Calculator")
print(f"  Wall: {length}ft long, {height}ft high, {spacing}in OC")
print(f"  Studs (2x4x{int(height*12)}): {studs}")
print(f"  Plates (2x4x8): {plates}")
if headers: print(f"  Headers: {headers}")
print(f"  Total 2x4s: {studs + plates + headers*2}")
board_feet = (studs * 1.5 * 3.5 * height + plates * 1.5 * 3.5 * 8) / 144
print(f"  Board feet: {board_feet:.0f}")
