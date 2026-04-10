#!/usr/bin/env python3
"""Skill: follow_up_email — Generate follow-up email.
Usage: ##SKILL:follow_up_email{"client":"John","topic":"kitchen estimate","days_since":3}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
client = args.get("client","there")
topic = args.get("topic","our recent conversation")
days = int(args.get("days_since",3))
print(f"Subject: Following up on {topic}")
print(f"\nHi {client},")
print(f"\nI wanted to follow up on {topic} from {days} days ago.")
print(f"Do you have any questions or would you like to schedule a time")
print(f"to discuss next steps?")
print(f"\nWe're here to help whenever you're ready.")
print(f"\nBest regards,")
print(f"All Home Building Co LLC")
print(f"Bensalem, PA")
