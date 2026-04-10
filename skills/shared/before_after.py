#!/usr/bin/env python3
"""Skill: before_after — Generate before/after comparison metadata.
Usage: ##SKILL:before_after{"project":"Kitchen Remodel","before_photos":["kitchen_old.jpg"],"after_photos":["kitchen_new.jpg"]}##"""
import os, json
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
print(f"BEFORE & AFTER COMPARISON")
print(f"{'='*50}")
print(f"Project: {args.get('project','N/A')}")
print(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
befores = args.get("before_photos",[])
afters = args.get("after_photos",[])
if befores:
    print(f"\nBefore ({len(befores)} photos):")
    for p in befores: print(f"  📷 {p}")
if afters:
    print(f"\nAfter ({len(afters)} photos):")
    for p in afters: print(f"  📷 {p}")
print(f"\nCaption: {args.get('caption','Another beautiful transformation by All Home Building Co LLC')}")
print(f"Tags: #renovation #homeimprovement #beforeandafter #ahbco #bensalem")
