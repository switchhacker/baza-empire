#!/usr/bin/env python3
"""Calculate subcontractor payments from hours/rates."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
subs = args.get("subcontractors", [])
# [{"name": "Mike Electric", "hours": 20, "rate": 65, "materials": 500}]

results = []
total = 0
for sub in subs:
    hours = float(sub.get("hours", 0))
    rate = float(sub.get("rate", 0))
    materials = float(sub.get("materials", 0))
    labor = round(hours * rate, 2)
    payment = round(labor + materials, 2)
    total += payment
    results.append({
        "name": sub.get("name", ""),
        "hours": hours, "rate": rate,
        "labor": labor, "materials": materials,
        "total_payment": payment
    })

print(json.dumps({"subcontractors": results, "total_payments": round(total, 2)}))
