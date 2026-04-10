#!/usr/bin/env python3
"""Skill: json_to_csv — Convert JSON array to CSV.
Usage: ##SKILL:json_to_csv{"data":[{"name":"John","age":30}]}##"""
import os, json, csv, io
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
data = args.get("data",[])
if not data: print("Error: data array required"); exit(1)
out = io.StringIO()
writer = csv.DictWriter(out, fieldnames=data[0].keys())
writer.writeheader()
writer.writerows(data)
print(out.getvalue())
