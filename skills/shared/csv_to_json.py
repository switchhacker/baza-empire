#!/usr/bin/env python3
"""Skill: csv_to_json — Convert CSV to JSON.
Usage: ##SKILL:csv_to_json{"csv":"name,age\nJohn,30\nJane,25"}##"""
import os, json, csv, io
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
data = args.get("csv","")
if not data: print("Error: csv data required"); exit(1)
reader = csv.DictReader(io.StringIO(data))
rows = list(reader)
print(json.dumps(rows, indent=2))
