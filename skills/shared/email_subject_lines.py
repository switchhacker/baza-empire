#!/usr/bin/env python3
"""Skill: email_subject_lines — Generate 5 email subject line options.
Usage: ##SKILL:email_subject_lines{"topic":"kitchen remodel estimate","type":"follow_up"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
topic = args.get("topic","your project")
etype = args.get("type","general")
templates = {
    "follow_up": [f"Following up: {topic}", f"Quick question about {topic}", f"Still interested in {topic}?", f"Your {topic} — next steps", f"Checking in: {topic}"],
    "proposal": [f"Your {topic} proposal is ready", f"Proposal: {topic}", f"Ready to get started on {topic}?", f"Your personalized {topic} plan", f"Let's build your dream {topic}"],
    "general": [f"About {topic}", f"Update on {topic}", f"Important: {topic}", f"Re: {topic}", f"Quick note about {topic}"],
}
lines = templates.get(etype, templates["general"])
print(f"Subject Lines ({etype}):")
for i, line in enumerate(lines, 1):
    print(f"  {i}. {line}")
