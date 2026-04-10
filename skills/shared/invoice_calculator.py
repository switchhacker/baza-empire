#!/usr/bin/env python3
"""Calculate invoice totals from line items, apply tax and markup."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
items = args.get("items", [])  # [{"description": "...", "qty": 1, "unit_price": 100}]
tax_rate = args.get("tax_rate", 0.06)  # PA sales tax 6%
markup = args.get("markup", 0.0)  # e.g. 0.15 for 15%

subtotal = 0
line_details = []
for item in items:
    qty = float(item.get("qty", 1))
    price = float(item.get("unit_price", 0))
    line_total = qty * price
    subtotal += line_total
    line_details.append({
        "description": item.get("description", ""),
        "qty": qty, "unit_price": price, "total": round(line_total, 2)
    })

markup_amount = round(subtotal * markup, 2)
taxable = subtotal + markup_amount
tax_amount = round(taxable * tax_rate, 2)
grand_total = round(taxable + tax_amount, 2)

print(json.dumps({
    "lines": line_details,
    "subtotal": round(subtotal, 2),
    "markup_pct": markup, "markup_amount": markup_amount,
    "tax_rate": tax_rate, "tax_amount": tax_amount,
    "grand_total": grand_total
}))
