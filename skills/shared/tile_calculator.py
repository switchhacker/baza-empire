#!/usr/bin/env python3
"""Skill: tile_calculator — Tiles needed for floor/wall.
Usage: ##SKILL:tile_calculator{"area_sqft":120,"tile_size":"12x12","waste_pct":10}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
area = float(args.get("area_sqft",0))
tile = args.get("tile_size","12x12")
waste = float(args.get("waste_pct",10))
tw,th = [float(x) for x in tile.split("x")]
tile_sqft = (tw*th)/144
tiles_needed = int(area / tile_sqft * (1 + waste/100)) + 1
boxes = -(-tiles_needed // int(args.get("per_box",12)))
print(f"Tile Calculator")
print(f"  Area: {area:.0f} sqft | Tile: {tile}in ({tile_sqft:.2f} sqft/tile)")
print(f"  Waste: {waste:.0f}%")
print(f"  Tiles needed: {tiles_needed}")
print(f"  Boxes ({args.get('per_box',12)}/box): {boxes}")
