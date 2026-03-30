#!/usr/bin/env python3
"""
Shared Skill: web_fetch
Fetch a full web page and return its clean text content using Ollama's Web Fetch API.
Requires OLLAMA_API_KEY.

Usage from agent:
    ##SKILL:web_fetch{"url": "https://www.phila.gov/permits/"}##

CLI:
    OLLAMA_API_KEY=<key> SKILL_ARGS='{"url":"https://ollama.com","max_chars":4000}' python web_fetch.py
"""
import os, sys, json

args      = json.loads(os.environ.get("SKILL_ARGS", "{}"))
url       = args.get("url", "")
max_chars = int(args.get("max_chars", 8000))
output    = args.get("output", "text")   # "text" or "json"

if not url:
    print(json.dumps({"success": False, "error": "url is required"}))
    sys.exit(1)

api_key = os.environ.get("OLLAMA_API_KEY", "")
if not api_key:
    print(json.dumps({"success": False, "error": "OLLAMA_API_KEY not set — web_fetch requires Ollama Pro"}))
    sys.exit(1)

try:
    import ollama
    response = ollama.web_fetch(url)

    title   = response.title   if hasattr(response, "title")   else response.get("title", "")
    content = response.content if hasattr(response, "content") else response.get("content", "")
    links   = response.links   if hasattr(response, "links")   else response.get("links", [])

    # Truncate content to max_chars
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n... [truncated at {max_chars} chars]"

    if output == "json":
        print(json.dumps({
            "success": True,
            "url":     url,
            "title":   title,
            "content": content,
            "links":   links[:20],   # cap links list
        }))
    else:
        lines = [
            f"PAGE: {title}",
            f"URL: {url}",
            "━━━━━━━━━━━━━━━━",
            content,
        ]
        print("\n".join(lines))

except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
    sys.exit(1)
