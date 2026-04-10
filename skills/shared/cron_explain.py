#!/usr/bin/env python3
"""Skill: cron_explain — Explain a crontab expression.
Usage: ##SKILL:cron_explain{"expr":"0 */4 * * *"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
expr = args.get("expr","").strip()
if not expr: print("Error: expr required"); exit(1)
parts = expr.split()
if len(parts) < 5: print("Error: need 5 cron fields"); exit(1)
fields = {"minute":parts[0],"hour":parts[1],"day":parts[2],"month":parts[3],"weekday":parts[4]}
desc = []
if parts[0] == "0" and parts[1] == "*": desc.append("every hour at :00")
elif parts[0] == "0" and "/" in parts[1]: desc.append(f"every {parts[1].split('/')[1]} hours")
elif parts[0] == "*" and parts[1] == "*": desc.append("every minute")
elif "/" in parts[0]: desc.append(f"every {parts[0].split('/')[1]} minutes")
else: desc.append(f"at {parts[1]}:{parts[0].zfill(2)}")
if parts[2] != "*": desc.append(f"on day {parts[2]}")
if parts[3] != "*": desc.append(f"in month {parts[3]}")
if parts[4] != "*":
    days = {0:"Sun",1:"Mon",2:"Tue",3:"Wed",4:"Thu",5:"Fri",6:"Sat"}
    desc.append(f"on {days.get(int(parts[4]),parts[4])}")
print(f"Cron: {expr}")
print(f"Means: {', '.join(desc)}")
print(f"Fields: {json.dumps(fields)}")
