#!/usr/bin/env python3
"""List, add, or remove cron jobs via the dashboard API."""
import os, json, urllib.request

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
action = args.get("action", "list")
base = args.get("base_url", "http://localhost:8888")

try:
    if action == "list":
        r = urllib.request.urlopen(f"{base}/api/crons", timeout=5)
        print(r.read().decode())
    elif action == "add":
        data = json.dumps({"name": args.get("name",""), "schedule": args.get("schedule",""),
                           "command": args.get("command",""), "agent_id": args.get("agent_id","")}).encode()
        req = urllib.request.Request(f"{base}/api/crons", data=data, headers={"Content-Type":"application/json"}, method="POST")
        r = urllib.request.urlopen(req, timeout=10)
        print(r.read().decode())
    elif action == "delete":
        cid = args.get("id","")
        req = urllib.request.Request(f"{base}/api/crons/{cid}", method="DELETE")
        r = urllib.request.urlopen(req, timeout=5)
        print(r.read().decode())
    else:
        print(json.dumps({"error": f"Unknown action: {action}. Use list/add/delete"}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
