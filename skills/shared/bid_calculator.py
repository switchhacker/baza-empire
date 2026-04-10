#!/usr/bin/env python3
"""Calculate bid price from scope (materials + labor + markup)."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
materials = args.get("materials", [])  # [{"item": "Lumber", "cost": 2000}]
labor_hours = float(args.get("labor_hours", 0))
labor_rate = float(args.get("labor_rate", 45))
markup_pct = float(args.get("markup", 0.20))
contingency_pct = float(args.get("contingency", 0.10))

mat_total = sum(float(m.get("cost", 0)) for m in materials)
labor_total = labor_hours * labor_rate
subtotal = mat_total + labor_total
markup = round(subtotal * markup_pct, 2)
contingency = round(subtotal * contingency_pct, 2)
bid_price = round(subtotal + markup + contingency, 2)

print(json.dumps({
    "materials_total": round(mat_total, 2),
    "labor_total": round(labor_total, 2),
    "subtotal": round(subtotal, 2),
    "markup": {"pct": markup_pct, "amount": markup},
    "contingency": {"pct": contingency_pct, "amount": contingency},
    "bid_price": bid_price,
    "per_hour_effective": round(bid_price / labor_hours, 2) if labor_hours > 0 else 0
}))
