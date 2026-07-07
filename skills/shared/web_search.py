#!/usr/bin/env python3
"""
Shared Skill: web_search
Search the web via the self-hosted SearXNG meta-search instance (SEARXNG_URL,
primary, no API key). Falls back to DuckDuckGo HTML scraping if SearXNG is
unreachable or returns nothing.

Usage from agent:
    ##SKILL:web_search{"query": "PA HIC license renewal 2025", "n": 5}##

CLI:
    SKILL_ARGS='{"query":"test","n":3}' python web_search.py
"""
import os, sys, json, urllib.request, urllib.parse, urllib.error, html, re

args   = json.loads(os.environ.get("SKILL_ARGS", "{}"))
query  = args.get("query", "")
n      = int(args.get("n", 5))
output = args.get("output", "text")   # "text" or "json"

if not query:
    print(json.dumps({"success": False, "error": "query is required"}))
    sys.exit(1)

# ── SearXNG (primary) ─────────────────────────────────────────────────────────

def searxng_search(query: str, max_results: int = 5) -> list:
    """Primary: self-hosted SearXNG meta-search (local-first, no API key)."""
    base = os.environ.get("SEARXNG_URL", "http://localhost:8181")
    try:
        req = urllib.request.Request(
            f"{base}/search?q={urllib.parse.quote(query)}&format=json",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return [
            {"title": x.get("title", ""), "url": x.get("url", ""),
             "snippet": (x.get("content") or "")[:300]}
            for x in data.get("results", [])[:max_results]
        ]
    except Exception as e:
        return [{"error": f"searxng: {e}"}]

# ── DuckDuckGo Fallback ────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

def ddg_search(query: str, max_results: int = 5) -> list:
    """DuckDuckGo HTML scraper fallback (no API key required)."""
    encoded = urllib.parse.quote_plus(query)
    url     = f"https://html.duckduckgo.com/html/?q={encoded}&kl=us-en"
    req     = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.URLError as e:
        return [{"error": str(e)}]

    results = []
    blocks  = re.findall(r'<div class="result[^"]*".*?</div>\s*</div>', body, re.DOTALL)
    for block in blocks[:max_results * 2]:
        title_m   = re.search(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
        url_m     = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', block)
        snippet_m = re.search(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not title_m:
            continue
        title   = html.unescape(re.sub(r'<[^>]+>', '', title_m.group(1))).strip()
        url_raw = url_m.group(1) if url_m else ""
        snippet = html.unescape(re.sub(r'<[^>]+>', '', snippet_m.group(1))).strip() if snippet_m else ""
        if url_raw.startswith("//duckduckgo.com/l/?"):
            qs      = urllib.parse.parse_qs(urllib.parse.urlparse("https:" + url_raw).query)
            url_raw = qs.get("uddg", [url_raw])[0]
        if title:
            results.append({"title": title, "url": url_raw, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results

# ── Execute ────────────────────────────────────────────────────────────────────

results = searxng_search(query, n)
source = "searxng"
real = [r for r in results if not (len(r) == 1 and "error" in r)]
if not real:
    results = ddg_search(query, n)
    source = "duckduckgo (searxng down)"
else:
    results = real

# Final scrub of error-only entries
results = [r for r in results if not (len(r) == 1 and "error" in r)]

if output == "json":
    print(json.dumps({"success": True, "query": query, "source": source, "results": results}))
else:
    lines = [f"WEB SEARCH ({source.upper()}): {query}", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', '(no title)')}")
        if r.get("url"):
            lines.append(f"   {r['url']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
        lines.append("")
    print("\n".join(lines))
