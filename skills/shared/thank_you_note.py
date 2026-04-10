#!/usr/bin/env python3
"""Skill: thank_you_note — Generate thank you note.
Usage: ##SKILL:thank_you_note{"client":"John Smith","project":"kitchen remodel","reason":"choosing us"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
print(f"Dear {args.get('client','valued client')},")
print(f"\nThank you for {args.get('reason','trusting us with your project')}.")
if args.get("project"):
    print(f"Working on your {args['project']} has been a pleasure.")
print(f"\nWe take pride in every project and hope you're enjoying the results.")
print(f"If you ever need anything in the future, don't hesitate to reach out.")
print(f"\nWarm regards,")
print(f"Serge Tkach")
print(f"All Home Building Co LLC")
print(f"Bensalem, PA")
