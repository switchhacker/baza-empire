#!/usr/bin/env python3
"""
Shared Skill: scrape_page
Fetch a URL and extract clean readable text. No external dependencies.
Strips scripts, styles, nav, footer. Returns title + body text.

Usage from agent:
    result = self.skills.run("scrape_page", {
        "url": "https://www.attorneygeneral.gov/protect-yourself/home-improvement/",
        "max_chars": 4000
    })

CLI:
    SKILL_ARGS='{"url":"https://example.com","max_chars":3000}' python scrape_page.py
"""
import os, sys, json, re, html as html_mod
import urllib.request, urllib.error, urllib.parse

args      = json.loads(os.environ.get("SKILL_ARGS", "{}"))
url       = args.get("url", "")
max_chars = int(args.get("max_chars", 4000))
output    = args.get("output", "text")   # "text" or "json"

if not url:
    print(json.dumps({"success": False, "error": "url is required"}))
    sys.exit(1)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def strip_tags(html_str: str) -> str:
    """Remove HTML tags and decode entities."""
    # Remove unwanted blocks
    for tag in ["script", "style", "nav", "footer", "header", "noscript", "iframe", "svg"]:
        html_str = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', ' ', html_str, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r'<[^>]+>', ' ', html_str)
    # Decode entities
    text = html_mod.unescape(text)
    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def extract_title(html_str: str) -> str:
    m = re.search(r'<title[^>]*>(.*?)</title>', html_str, re.IGNORECASE | re.DOTALL)
    if m:
        return html_mod.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
    return ""

def fetch_page(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type and "text" not in content_type:
                return {"success": False, "error": f"Non-HTML content type: {content_type}"}
            raw = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": str(e)}

    title = extract_title(raw)
    text  = strip_tags(raw)
    # Trim to max_chars
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[... truncated at {max_chars} chars]"

    return {
        "success": True,
        "url":     url,
        "title":   title,
        "text":    text,
        "chars":   len(text),
    }


result = fetch_page(url)

if output == "json":
    print(json.dumps(result))
elif result.get("success"):
    print(f"PAGE: {result['title']}")
    print(f"URL:  {result['url']}")
    print(f"CHARS: {result['chars']}")
    print("─" * 60)
    print(result["text"])
else:
    print(f"ERROR: {result.get('error')}", file=sys.stderr)
    sys.exit(1)
