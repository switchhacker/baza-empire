#!/usr/bin/env python3
"""Skill: meeting_agenda — Generate meeting agenda.
Usage: ##SKILL:meeting_agenda{"title":"Weekly Sync","topics":["project updates","budget review","next steps"],"duration":30}##"""
import os, json
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
topics = args.get("topics",[])
dur = int(args.get("duration",30))
per_topic = dur // max(len(topics),1)
print(f"MEETING AGENDA")
print(f"{'='*40}")
print(f"Title: {args.get('title','Meeting')}")
print(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
print(f"Duration: {dur} minutes")
print(f"{'='*40}")
t = 0
for i, topic in enumerate(topics, 1):
    print(f"  {i}. {topic} ({per_topic} min)")
    t += per_topic
print(f"  {len(topics)+1}. Open discussion ({dur-t} min)")
print(f"\nAttendees: ____________")
print(f"Notes: ____________")
