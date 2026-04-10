#!/usr/bin/env python3
"""Skill: window_measure — Window sizing from rough opening.
Usage: ##SKILL:window_measure{"width_in":36,"height_in":48,"type":"double_hung"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
w = float(args.get("width_in",0))
h = float(args.get("height_in",0))
wtype = args.get("type","double_hung")
window_w = w - 0.5
window_h = h - 0.5
sqft = (w * h) / 144
types = {"double_hung":"$300-800","casement":"$350-900","sliding":"$250-700","picture":"$400-1200","bay":"$1500-3500"}
print(f"Window Measurement")
print(f"  Rough opening: {w:.1f} x {h:.1f} in")
print(f"  Window size: {window_w:.1f} x {window_h:.1f} in")
print(f"  Area: {sqft:.1f} sqft")
print(f"  Type: {wtype}")
print(f"  Price range: {types.get(wtype,'$300-800')}")
print(f"  Shim space: 0.25in each side")
print(f"  U-factor target: ≤0.30 (Energy Star)")
