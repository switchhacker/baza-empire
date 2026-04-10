#!/usr/bin/env python3
"""Skill: roof_pitch_calc — Calculate roof pitch, angle, multiplier.
Usage: ##SKILL:roof_pitch_calc{"rise":6,"run":12}##"""
import os, json, math
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
rise = float(args.get("rise",6))
run = float(args.get("run",12))
pitch = rise/run
angle = math.degrees(math.atan(pitch))
multiplier = math.sqrt(1 + pitch**2)
print(f"Roof Pitch Calculator")
print(f"  Rise: {rise}in per {run}in run")
print(f"  Pitch: {rise}/{run} = {rise:.0f}:12")
print(f"  Angle: {angle:.1f} degrees")
print(f"  Area multiplier: {multiplier:.3f}x")
print(f"  Walkable: {'Yes' if pitch <= 0.5 else 'Caution' if pitch <= 0.75 else 'No - harness required'}")
