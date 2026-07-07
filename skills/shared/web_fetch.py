#!/usr/bin/env python3
"""Fetch full page content. Now backed by the Phantom Browser service (:8100,
real Chromium render); falls back to plain urllib. Kept because
core/base_agent.py exposes self.web_fetch() and prompts reference it."""
SKILL_META = {
    "category": "web",
    "summary": "Fetch a URL's full content (browser-rendered; urllib fallback).",
    "when_to_use": "Legacy alias — prefer web_scrape for markdown + links.",
    "args": {"url": "required", "max_chars": "default 8000",
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
    print(f"web_fetch: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

url = args.get("url", "")
max_chars = int(args.get("max_chars", 8000))
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
        content = d.get("markdown", "")
        return {"success": True, "url": url, "title": d.get("title", ""),
                "content": content, "chars": len(content),
                "links": d.get("links", [])}
    except Exception:
        return None


def via_urllib() -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
        content = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
        content = re.sub(r"(?s)<[^>]+>", " ", content)
        content = re.sub(r"\s+", " ", content).strip()[:max_chars]
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        return {"success": True, "url": url, "title": title,
                "content": content, "chars": len(content), "links": []}
    except Exception as e:
        return {"success": False, "url": url, "error": f"{type(e).__name__}: {e}"}


result = via_phantom_browser() or via_urllib()

if output == "json":
    print(json.dumps(result))
elif result.get("success"):
    print(f"PAGE: {result['title']}\nURL: {result['url']}\nCHARS: {result['chars']}\n"
          + "-" * 40 + f"\n{result['content']}")
else:
    print(f"ERROR: {result.get('error')}", file=sys.stderr)
    sys.exit(1)
