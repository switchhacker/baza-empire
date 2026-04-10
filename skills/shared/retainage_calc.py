#!/usr/bin/env python3
"""Skill: retainage_calc — Calculate retainage held/due.
Usage: ##SKILL:retainage_calc{"contract":100000,"billed":80000,"retainage_pct":10}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
contract = float(args.get("contract",0))
billed = float(args.get("billed",0))
pct = float(args.get("retainage_pct",10))
held = billed * (pct/100)
received = billed - held
remaining = contract - billed
print(f"Retainage Calculator")
print(f"  Contract: ${contract:,.2f}")
print(f"  Billed to date: ${billed:,.2f} ({billed/contract*100:.0f}%)")
print(f"  Retainage rate: {pct:.0f}%")
print(f"  Retainage held: ${held:,.2f}")
print(f"  Cash received: ${received:,.2f}")
print(f"  Remaining to bill: ${remaining:,.2f}")
print(f"  Retainage due at completion: ${held:,.2f}")
