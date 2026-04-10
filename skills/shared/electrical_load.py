#!/usr/bin/env python3
"""Skill: electrical_load — Circuit load/amp calculator.
Usage: ##SKILL:electrical_load{"circuits":[{"name":"kitchen","watts":2400},{"name":"bathroom","watts":1800}]}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
circuits = args.get("circuits",[])
voltage = int(args.get("voltage",120))
print(f"Electrical Load Calculator ({voltage}V)")
total_watts = 0
for c in circuits:
    w = float(c.get("watts",0))
    amps = w / voltage
    breaker = 15 if amps <= 12 else 20 if amps <= 16 else 30 if amps <= 24 else 50
    total_watts += w
    print(f"  {c.get('name','?')}: {w:.0f}W = {amps:.1f}A → {breaker}A breaker")
total_amps = total_watts / voltage
print(f"\nTotal: {total_watts:,.0f}W = {total_amps:.1f}A")
print(f"Service needed: {'100A' if total_amps < 80 else '200A' if total_amps < 160 else '400A'}")
