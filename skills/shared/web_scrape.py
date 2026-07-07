#!/usr/bin/env python3
"""Scrape any URL (JS-rendered, headless Chromium) into clean markdown via the
Phantom Browser service. Successor to scrape_page for real pages."""
SKILL_META = {
    "category": "web",
    "summary": "Render a URL in a real browser and return clean markdown + links.",
    "when_to_use": ("To read any web page — including JS-heavy/SPA pages that plain "
                    "HTTP fetch can't render. Returns markdown, title, links."),
    "args": {"url": "page to scrape (required)",
             "max_chars": "markdown cap, default 8000",
             "wait_ms": "extra wait after load for slow JS, default 0",
             "screenshot": "true to also save a PNG"},
}
import json
import os
import sys

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(json.dumps({"success": False, "error": f"invalid SKILL_ARGS JSON: {e}"}))
    sys.exit(1)

import httpx


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)

BASE = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")
url = args.get("url", "")
if not url:
    print(json.dumps({"success": False, "error": "url is required"}))
    sys.exit(1)

try:
    r = httpx.post(f"{BASE}/scrape", json={
        "url": url,
        "max_chars": _int(args.get("max_chars"), 8000),
        "wait_ms": _int(args.get("wait_ms"), 0),
        "screenshot": _bool(args.get("screenshot")),
    }, timeout=90)
    r.raise_for_status()
    print(json.dumps(r.json()))
except httpx.HTTPError as e:
    print(json.dumps({"success": False, "url": url,
                      "error": f"{type(e).__name__}: {e}",
                      "hint": "is baza-phantom-browser.service running on :8100?"}))
