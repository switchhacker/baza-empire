#!/usr/bin/env python3
"""List a site's URLs (sitemap-first, link sweep fallback) via the Phantom
Browser service — pick targets before scraping/crawling."""
SKILL_META = {
    "category": "web",
    "summary": "Discover a site's URLs (sitemap or link sweep).",
    "when_to_use": ("Before crawling/scraping a site: get the URL inventory, then "
                    "web_scrape the interesting ones or crawl_site a subset."),
    "args": {"url": "site root or any page (required)", "limit": "default 200"},
}
import json
import os
import sys

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"web_map: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

import httpx

BASE = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")
url = args.get("url", "")
if not url:
    print(json.dumps({"success": False, "error": "url is required"}))
    sys.exit(1)

try:
    r = httpx.post(f"{BASE}/map", json={"url": url, "limit": int(args.get("limit", 200))},
                   timeout=60)
    r.raise_for_status()
    print(json.dumps(r.json()))
except httpx.HTTPError as e:
    print(json.dumps({"success": False, "url": url,
                      "error": f"{type(e).__name__}: {e}",
                      "hint": "is baza-phantom-browser.service running on :8100?"}))
