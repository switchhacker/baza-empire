#!/usr/bin/env python3
"""Skill: warranty_letter — Generate warranty letter.
Usage: ##SKILL:warranty_letter{"client":"John Smith","project":"Kitchen Remodel","duration":"1 year"}##"""
import os, json
from datetime import datetime, timedelta
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
dur = args.get("duration","1 year")
years = int(dur.split()[0]) if dur.split()[0].isdigit() else 1
start = datetime.now()
end = start + timedelta(days=365*years)
print(f"WARRANTY CERTIFICATE")
print(f"{'='*50}")
print(f"All Home Building Co LLC")
print(f"Bensalem, PA")
print(f"\nClient: {args.get('client','N/A')}")
print(f"Project: {args.get('project','N/A')}")
print(f"Address: {args.get('address','N/A')}")
print(f"\nWarranty Period: {dur}")
print(f"Start: {start.strftime('%B %d, %Y')}")
print(f"Expires: {end.strftime('%B %d, %Y')}")
print(f"\nThis warranty covers defects in workmanship and materials")
print(f"for the work performed under the above project.")
print(f"\nExclusions: Normal wear, acts of God, misuse, unauthorized modifications.")
print(f"\nTo make a claim: Contact AHBCO at (215) XXX-XXXX")
