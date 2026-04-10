#!/usr/bin/env python3
"""Skill: satisfaction_survey — Generate survey questions.
Usage: ##SKILL:satisfaction_survey{"project":"Kitchen Remodel","client":"John"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
print(f"CUSTOMER SATISFACTION SURVEY")
print(f"Project: {args.get('project','Your Project')}")
print(f"Client: {args.get('client','Valued Client')}")
print(f"\nPlease rate 1-5 (1=Poor, 5=Excellent):")
qs = [
    "Overall quality of workmanship",
    "Communication throughout the project",
    "Timeliness / meeting deadlines",
    "Cleanliness of job site",
    "Value for money",
    "Professionalism of crew",
    "Problem resolution / responsiveness",
    "Would you recommend us to others?",
]
for i, q in enumerate(qs, 1):
    print(f"  {i}. {q}  [ 1  2  3  4  5 ]")
print(f"\nComments: ________________________________")
print(f"\nMay we use your feedback as a testimonial? [ Yes / No ]")
