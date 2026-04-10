#!/usr/bin/env python3
"""Skill: referral_tracker — Log and report referral sources.
Usage: ##SKILL:referral_tracker{"source":"John Smith","client":"Jane Doe","project_value":25000}##"""
import os, json
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
print(f"Referral Logged")
print(f"  Date: {datetime.now().strftime('%Y-%m-%d')}")
print(f"  Source: {args.get('source','N/A')}")
print(f"  New client: {args.get('client','N/A')}")
print(f"  Project value: ${float(args.get('project_value',0)):,.2f}")
print(f"  Status: {args.get('status','new lead')}")
print(f"\nReferral reward: {'Send thank you + $100 gift card' if float(args.get('project_value',0)) > 10000 else 'Send thank you note'}")
