#!/usr/bin/env python3
"""Calculate debt payoff schedule (snowball/avalanche)."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
debts = args.get("debts", [])  # [{"name": "CC1", "balance": 5000, "rate": 0.18, "min_payment": 100}]
extra = float(args.get("extra_payment", 0))
method = args.get("method", "avalanche")  # avalanche (high rate first) or snowball (low balance first)

if method == "avalanche":
    debts.sort(key=lambda d: -float(d.get("rate", 0)))
else:
    debts.sort(key=lambda d: float(d.get("balance", 0)))

schedule = []
month = 0
active = [{"name": d["name"], "balance": float(d["balance"]), "rate": float(d.get("rate", 0)),
           "min": float(d.get("min_payment", 50))} for d in debts]

while any(d["balance"] > 0 for d in active) and month < 360:
    month += 1
    remaining_extra = extra
    for d in active:
        if d["balance"] <= 0:
            continue
        interest = d["balance"] * d["rate"] / 12
        payment = min(d["min"] + remaining_extra, d["balance"] + interest)
        remaining_extra = max(0, remaining_extra - (payment - d["min"]))
        d["balance"] = round(d["balance"] + interest - payment, 2)
        if d["balance"] <= 0:
            remaining_extra += d["min"]
            d["balance"] = 0

total_months = month
for d in active:
    schedule.append({"name": d["name"], "final_balance": d["balance"]})

print(json.dumps({"method": method, "months_to_payoff": total_months,
                   "years": round(total_months / 12, 1), "debts": schedule}))
