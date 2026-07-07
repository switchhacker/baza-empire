#!/usr/bin/env python3
"""Crawl a whole site/section into markdown pages via the Phantom Browser
service. Starts an async BFS job and polls it (up to ~75s inside the skill;
longer crawls: re-call with job_id to keep polling)."""
SKILL_META = {
    "category": "web",
    "summary": "BFS-crawl a site (or path subset) into markdown pages.",
    "when_to_use": ("To gather MANY pages from one site — docs sections, competitor "
                    "sites, catalogs. For one page use web_scrape. Re-call with "
                    "job_id if status is still 'running'."),
    "args": {"url": "start URL (required unless job_id)",
             "job_id": "poll an existing crawl instead of starting a new one",
             "max_pages": "default 50", "max_depth": "default 3",
             "include_paths": "list of regexes paths must match",
             "exclude_paths": "list of regexes to skip",
             "max_chars": "markdown cap per page, default 3000"},
}
import json
import os
import sys
import time

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"crawl_site: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

import httpx

BASE = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")

try:
    job_id = args.get("job_id")
    if not job_id:
        if not args.get("url"):
            print(json.dumps({"success": False, "error": "url or job_id required"}))
            sys.exit(1)
        payload = {k: args[k] for k in ("url", "max_pages", "max_depth",
                                        "include_paths", "exclude_paths",
                                        "max_chars", "same_domain") if k in args}
        r = httpx.post(f"{BASE}/crawl", json=payload, timeout=30)
        r.raise_for_status()
        job_id = r.json()["job_id"]

    deadline = time.time() + 75
    body = None
    while time.time() < deadline:
        r = httpx.get(f"{BASE}/crawl/{job_id}", timeout=30)
        r.raise_for_status()
        body = r.json()
        if not body.get("success") or body["job"]["status"] in ("done", "error"):
            break
        time.sleep(3)
    body = body or {"success": False, "error": "no response"}
    body["job_id"] = job_id
    if body.get("job", {}).get("status") == "running":
        body["hint"] = f"crawl still running — call crawl_site again with job_id {job_id}"
    print(json.dumps(body))
except httpx.HTTPError as e:
    print(json.dumps({"success": False, "error": f"{type(e).__name__}: {e}",
                      "hint": "is baza-phantom-browser.service running on :8100?"}))
