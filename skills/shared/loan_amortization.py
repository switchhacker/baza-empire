#!/usr/bin/env python3
"""Skill: loan_amortization — Monthly payment and schedule.
Usage: ##SKILL:loan_amortization{"principal":50000,"rate":6.5,"years":5}##"""
import os, json, math
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
P = float(args.get("principal",0))
r = float(args.get("rate",0)) / 100 / 12
n = int(args.get("years",0)) * 12
if P<=0 or r<=0 or n<=0: print("Error: principal, rate, years required"); exit()
M = P * (r*(1+r)**n) / ((1+r)**n - 1)
total = M * n
interest = total - P
print(f"Loan Amortization")
print(f"  Principal: ${P:,.2f}")
print(f"  Rate: {args.get('rate')}% APR")
print(f"  Term: {args.get('years')} years ({n} payments)")
print(f"  Monthly payment: ${M:,.2f}")
print(f"  Total paid: ${total:,.2f}")
print(f"  Total interest: ${interest:,.2f}")
