#!/usr/bin/env python3
"""Skill: security_headers — Check HTTP security headers.
Usage: ##SKILL:security_headers{"url":"http://localhost:8888"}##"""
import os, json, urllib.request
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
url = args.get("url","http://localhost:8888")
headers_to_check = ["X-Frame-Options","X-Content-Type-Options","Strict-Transport-Security","Content-Security-Policy","X-XSS-Protection","Referrer-Policy"]
try:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as r:
        print(f"Security Headers: {url}")
        for h in headers_to_check:
            val = r.headers.get(h)
            icon = "🟢" if val else "🔴"
            print(f"  {icon} {h}: {val or 'MISSING'}")
except Exception as e:
    print(f"Error: {e}")
