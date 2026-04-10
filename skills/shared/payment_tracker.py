#!/usr/bin/env python3
"""Check payment status for an invoice, calculate overdue interest."""
import os, json
from datetime import datetime, timedelta

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
invoice_amount = float(args.get("amount", 0))
due_date = args.get("due_date", "")  # YYYY-MM-DD
paid_amount = float(args.get("paid", 0))
annual_rate = float(args.get("interest_rate", 0.015))  # 1.5% monthly default

balance = invoice_amount - paid_amount
status = "paid" if balance <= 0 else "partial" if paid_amount > 0 else "unpaid"
days_overdue = 0
interest = 0.0

if due_date and balance > 0:
    try:
        due = datetime.strptime(due_date, "%Y-%m-%d")
        days_overdue = max(0, (datetime.now() - due).days)
        if days_overdue > 0:
            monthly_rate = annual_rate
            months_overdue = days_overdue / 30.0
            interest = round(balance * monthly_rate * months_overdue, 2)
            status = "overdue"
    except ValueError:
        pass

print(json.dumps({
    "invoice_amount": invoice_amount, "paid": paid_amount,
    "balance": round(balance, 2), "status": status,
    "days_overdue": days_overdue, "interest_accrued": interest,
    "total_owed": round(balance + interest, 2)
}))
