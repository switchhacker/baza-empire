#!/usr/bin/env python3
"""Skill: progress_billing — Calculate progress billing amounts.
Usage: ##SKILL:progress_billing{"contract":100000,"pct_complete":60,"prev_billed":40000,"retainage_pct":10}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
contract = float(args.get("contract",0))
pct = float(args.get("pct_complete",0))
prev = float(args.get("prev_billed",0))
ret_pct = float(args.get("retainage_pct",10))
earned = contract * (pct/100)
current_billing = earned - prev
retainage = current_billing * (ret_pct/100)
net_due = current_billing - retainage
print(f"Progress Billing")
print(f"  Contract: ${contract:,.2f}")
print(f"  % Complete: {pct:.0f}%")
print(f"  Total earned: ${earned:,.2f}")
print(f"  Previously billed: ${prev:,.2f}")
print(f"  Current billing: ${current_billing:,.2f}")
print(f"  Retainage ({ret_pct:.0f}%): -${retainage:,.2f}")
print(f"  Net due this period: ${net_due:,.2f}")
