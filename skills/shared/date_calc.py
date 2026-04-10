#!/usr/bin/env python3
"""Calculate days between dates, add/subtract days."""
import os, json
from datetime import datetime, timedelta
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
if args.get("from") and args.get("to"):
    d1 = datetime.strptime(args["from"], "%Y-%m-%d"); d2 = datetime.strptime(args["to"], "%Y-%m-%d")
    print(json.dumps({"days": (d2-d1).days, "weeks": (d2-d1).days//7, "from": args["from"], "to": args["to"]}))
elif args.get("date") and args.get("add_days"):
    d = datetime.strptime(args["date"], "%Y-%m-%d") + timedelta(days=int(args["add_days"]))
    print(json.dumps({"result": d.strftime("%Y-%m-%d"), "day": d.strftime("%A")}))
else: print(json.dumps({"error": "Provide from+to or date+add_days"}))
