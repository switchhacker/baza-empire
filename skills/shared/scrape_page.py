#!/usr/bin/env python3
"""Fetch a page as text. Now backed by the Phantom Browser service (:8100,
real Chromium render); falls back to plain urllib if the service is down.
Kept for prompt-compat — new work should call web_scrape."""
SKILL_META = {
    "category": "web",
    "summary": "Fetch a URL's text content (browser-rendered; urllib fallback).",
    "when_to_use": "Legacy alias — prefer web_scrape for markdown + links.",
    "args": {"url": "required", "max_chars": "default 4000",
             "output": "text|json"},
}
import json
import os
import re
import sys
import urllib.request

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"scrape_page: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

url = args.get("url", "")
max_chars = int(args.get("max_chars", 4000))
output = args.get("output", "text")

if not url:
    print(json.dumps({"success": False, "error": "url is required"}))
    sys.exit(1)


def via_phantom_browser() -> dict | None:
    try:
        import httpx
        r = httpx.post(
            f"{os.environ.get('PHANTOM_BROWSER_URL', 'http://localhost:8100')}/scrape",
            json={"url": url, "max_chars": max_chars}, timeout=90)
        r.raise_for_status()
        d = r.json()
        if not d.get("success"):
            return None
        text = d.get("markdown", "")
        return {"success": True, "url": url, "title": d.get("title", ""),
                "text": text, "chars": len(text)}
    except Exception:
        return None


def via_urllib() -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
        text = re.sub(r"(?is)<(script|style|noscript|nav|footer|header)[^>]*>.*?</\1>",
                      " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()[:max_chars]
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        return {"success": True, "url": url, "title": title,
                "text": text, "chars": len(text)}
    except Exception as e:
        return {"success": False, "url": url, "error": f"{type(e).__name__}: {e}"}


result = via_phantom_browser() or via_urllib()

if output == "json":
    print(json.dumps(result))
elif result.get("success"):
    print(f"PAGE: {result['title']}\nURL: {result['url']}\nCHARS: {result['chars']}\n"
          + "-" * 40 + f"\n{result['text']}")
else:
    print(f"ERROR: {result.get('error')}", file=sys.stderr)
    sys.exit(1)
