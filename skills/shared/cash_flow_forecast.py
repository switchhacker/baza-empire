#!/usr/bin/env python3
"""Skill: cash_flow_forecast — 30/60/90 day projection.
Usage: ##SKILL:cash_flow_forecast{"current_balance":50000,"monthly_revenue":30000,"monthly_expenses":25000}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
balance = float(args.get("current_balance",0))
revenue = float(args.get("monthly_revenue",0))
expenses = float(args.get("monthly_expenses",0))
ar = float(args.get("accounts_receivable",0))
print(f"Cash Flow Forecast")
print(f"  Current balance: ${balance:,.2f}")
print(f"  Monthly revenue: ${revenue:,.2f}")
print(f"  Monthly expenses: ${expenses:,.2f}")
net = revenue - expenses
print(f"  Net monthly: ${net:,.2f}")
if ar: print(f"  Accounts receivable: ${ar:,.2f}")
print(f"\n  30-day: ${balance + net:,.2f}")
print(f"  60-day: ${balance + net*2:,.2f}")
print(f"  90-day: ${balance + net*3:,.2f}")
health = "Healthy" if balance + net*3 > 0 else "Warning" if balance + net > 0 else "Critical"
print(f"  Status: {health}")
