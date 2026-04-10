#!/usr/bin/env python3
"""Skill: complaint_response — Generate professional complaint response.
Usage: ##SKILL:complaint_response{"client":"John","issue":"paint peeling","severity":"medium"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
client = args.get("client","valued client")
issue = args.get("issue","the issue")
severity = args.get("severity","medium")
print(f"Dear {client},")
print(f"\nThank you for bringing {issue} to our attention.")
print(f"We take all feedback seriously and want to make this right.")
if severity == "high":
    print(f"\nI'll personally oversee the resolution. We can schedule a site")
    print(f"visit within the next 24-48 hours to assess and address the issue.")
else:
    print(f"\nWe'd like to schedule a time to come take a look and resolve")
    print(f"this for you at your earliest convenience.")
print(f"\nYour satisfaction is our top priority, and this is covered under")
print(f"our workmanship warranty.")
print(f"\nPlease call us at your convenience to schedule.")
print(f"\nSincerely,")
print(f"Serge Tkach, Owner")
print(f"All Home Building Co LLC")
