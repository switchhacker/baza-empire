#!/usr/bin/env python3
"""Skill: door_schedule — Door schedule generator.
Usage: ##SKILL:door_schedule{"doors":[{"location":"front","type":"exterior","size":"36x80"},{"location":"bedroom","type":"interior","size":"30x80"}]}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
doors = args.get("doors",[])
print(f"DOOR SCHEDULE")
print(f"{'='*60}")
print(f"{'#':<4} {'Location':<15} {'Type':<10} {'Size':<10} {'Hardware'}")
print(f"{'-'*60}")
for i, d in enumerate(doors, 1):
    hw = "deadbolt+handle" if d.get("type")=="exterior" else "passage" if "bed" in d.get("location","") else "privacy" if "bath" in d.get("location","") else "passage"
    print(f"{i:<4} {d.get('location','?'):<15} {d.get('type','?'):<10} {d.get('size','?'):<10} {hw}")
print(f"\nTotal doors: {len(doors)}")
ext = sum(1 for d in doors if d.get("type")=="exterior")
print(f"  Exterior: {ext} | Interior: {len(doors)-ext}")
