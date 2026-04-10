#!/usr/bin/env python3
"""Calculate payroll for a period (hours * rate, overtime)."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
employees = args.get("employees", [])
# [{"name": "John", "hours": 45, "rate": 25.00, "overtime_rate": 1.5}]

results = []
total_gross = 0
for emp in employees:
    hours = float(emp.get("hours", 0))
    rate = float(emp.get("rate", 0))
    ot_mult = float(emp.get("overtime_rate", 1.5))
    regular = min(hours, 40)
    overtime = max(0, hours - 40)
    regular_pay = regular * rate
    ot_pay = overtime * rate * ot_mult
    gross = round(regular_pay + ot_pay, 2)
    # Estimate withholdings
    fed_tax = round(gross * 0.12, 2)
    state_tax = round(gross * 0.0307, 2)  # PA flat rate
    fica = round(gross * 0.0765, 2)
    net = round(gross - fed_tax - state_tax - fica, 2)
    total_gross += gross
    results.append({
        "name": emp.get("name", ""), "regular_hours": regular,
        "overtime_hours": overtime, "gross": gross,
        "fed_tax": fed_tax, "state_tax": state_tax, "fica": fica, "net": net
    })

print(json.dumps({"employees": results, "total_gross": round(total_gross, 2),
                   "employee_count": len(results)}))
