#!/usr/bin/env python3
"""Extract structured JSON from web pages via the Phantom Browser service —
scrape → LOCAL Ollama model → JSON validated against your schema."""
SKILL_META = {
    "category": "web",
    "summary": "Scrape page(s) and extract JSON matching a schema (local LLM).",
    "when_to_use": ("When you need specific fields off a page — prices, specs, "
                    "contact info, listings — as clean JSON instead of prose."),
    "args": {"url": "single page", "urls": "list of pages (max 5)",
             "content": "raw text instead of a url",
             "schema": "JSON schema of the wanted object (required)",
             "prompt": "optional extra extraction instructions"},
}
import json
import os
import sys

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"web_extract: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

import httpx

BASE = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")
if not args.get("schema"):
    print(json.dumps({"success": False, "error": "schema is required"}))
    sys.exit(1)

try:
    payload = {k: args[k] for k in ("url", "urls", "content", "schema", "prompt", "model")
               if k in args}
    r = httpx.post(f"{BASE}/extract", json=payload, timeout=240)
    r.raise_for_status()
    print(json.dumps(r.json()))
except httpx.HTTPError as e:
    print(json.dumps({"success": False, "error": f"{type(e).__name__}: {e}",
                      "hint": "is baza-phantom-browser.service running on :8100?"}))
