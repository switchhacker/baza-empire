#!/usr/bin/env python3
"""Skill: portfolio_page — Generate project portfolio entry.
Usage: ##SKILL:portfolio_page{"project":"Kitchen Remodel","client":"Smith Family","value":45000,"duration":"6 weeks"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
print(f"PROJECT PORTFOLIO ENTRY")
print(f"{'='*50}")
print(f"Project: {args.get('project','N/A')}")
print(f"Client: {args.get('client','N/A')}")
print(f"Location: {args.get('location','Bensalem, PA')}")
print(f"Value: ${float(args.get('value',0)):,.0f}")
print(f"Duration: {args.get('duration','N/A')}")
print(f"\nDescription:")
print(f"  {args.get('description','Complete renovation including design, demolition, and finish work.')}")
print(f"\nHighlights:")
for h in args.get("highlights",["Quality craftsmanship","On time and on budget","Client satisfaction"]):
    print(f"  ✓ {h}")
print(f"\nTestimonial: \"{args.get('testimonial','Great work, highly recommend!')}\"")
print(f"  — {args.get('client','Happy Client')}")
