#!/usr/bin/env python3
"""Estimate quarterly tax based on revenue/expenses."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
revenue = float(args.get("revenue", 0))
expenses = float(args.get("expenses", 0))
quarter = args.get("quarter", "Q1")
filing_status = args.get("filing_status", "single")

taxable = revenue - expenses
se_tax = round(taxable * 0.9235 * 0.153, 2)  # Self-employment tax
se_deduction = round(se_tax / 2, 2)
adjusted = taxable - se_deduction

# Simple federal brackets (2026 est.)
if adjusted <= 11600:
    fed = adjusted * 0.10
elif adjusted <= 47150:
    fed = 1160 + (adjusted - 11600) * 0.12
elif adjusted <= 100525:
    fed = 5426 + (adjusted - 47150) * 0.22
else:
    fed = 17168.50 + (adjusted - 100525) * 0.24

fed = round(fed, 2)
state = round(taxable * 0.0307, 2)  # PA flat
quarterly_fed = round(fed / 4, 2)
quarterly_state = round(state / 4, 2)
quarterly_se = round(se_tax / 4, 2)

print(json.dumps({
    "quarter": quarter, "revenue": revenue, "expenses": expenses,
    "taxable_income": round(taxable, 2),
    "estimated_federal": fed, "estimated_state": state, "self_employment_tax": se_tax,
    "quarterly_payment": round(quarterly_fed + quarterly_state + quarterly_se, 2),
    "quarterly_breakdown": {"federal": quarterly_fed, "state": quarterly_state, "se": quarterly_se}
}))
