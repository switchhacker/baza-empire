#!/usr/bin/env python3
"""Skill: progress_photo_log — Log photos with timestamps + notes.
Usage: ##SKILL:progress_photo_log{"project":"Kitchen","phase":"demo","notes":"cabinets removed","photos":["img1.jpg","img2.jpg"]}##"""
import os, json
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
print(f"PROGRESS PHOTO LOG")
print(f"{'='*50}")
print(f"Project: {args.get('project','N/A')}")
print(f"Phase: {args.get('phase','N/A')}")
print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"\nNotes: {args.get('notes','No notes')}")
photos = args.get("photos",[])
print(f"\nPhotos ({len(photos)}):")
for p in photos:
    print(f"  📷 {p}")
print(f"\nLogged by: {args.get('logged_by','Field crew')}")
