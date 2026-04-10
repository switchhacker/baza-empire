#!/usr/bin/env python3
"""Skill: api_health_check — Check multiple API endpoints.
Usage: ##SKILL:api_health_check{"urls":["http://localhost:8888","http://localhost:11434"]}##"""
import os, json, urllib.request, time
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
urls = args.get("urls",[])
if not urls: urls = ["http://localhost:8888","http://localhost:11434","http://localhost:11435","http://localhost:11436","http://localhost:4000","http://localhost:8000"]
print("API Health Check")
for url in urls:
    try:
        start = time.time()
        req = urllib.request.Request(url); req.method = "GET"
        with urllib.request.urlopen(req, timeout=5) as r:
            ms = int((time.time()-start)*1000)
            print(f"  🟢 {url} → {r.status} ({ms}ms)")
    except Exception as e:
        print(f"  🔴 {url} → {str(e)[:60]}")
