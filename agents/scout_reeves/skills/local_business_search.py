#!/usr/bin/env python3
"""
Scout Reeves — local_business_search skill
Find local businesses near a zip code: name, phone, address, hours.

Strategy:
  1. Search DuckDuckGo for "{query} near {zip}" to get business website URLs
  2. Fetch each business page and extract JSON-LD schema.org data
  3. Fall back to meta tags / body text phone extraction
  4. Run a second targeted DDG search for missing details

Args (SKILL_ARGS env var, JSON):
    query   : str  — e.g. "auto glass replacement"
    zip     : str  — zip code (default: "19020")
    radius  : int  — miles (default: 10, display only)
    n       : int  — results to return (default: 5, max 8)

CLI test:
    SKILL_ARGS='{"query":"auto glass replacement","zip":"19020"}' python local_business_search.py
"""
import os, sys, json, re, html as _html, time
import urllib.request, urllib.parse, urllib.error

args     = json.loads(os.environ.get("SKILL_ARGS", "{}"))
query    = args.get("query", "").strip()
zip_code = str(args.get("zip", "19020")).strip()
radius   = int(args.get("radius", 10))
n        = min(int(args.get("n", 5)), 8)

if not query:
    print(json.dumps({"success": False, "error": "query is required"}))
    sys.exit(1)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(url: str, timeout: int = 8) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(200_000)   # cap at 200KB
            return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _strip(s: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


# ── Step 1: DDG search → extract real URLs ────────────────────────────────────

def ddg_search_urls(query: str, zip_code: str, max_urls: int = 12) -> list:
    """Return list of (title, url, snippet) from DuckDuckGo."""
    q = f"{query} near {zip_code}"
    encoded = urllib.parse.quote_plus(q)
    body = _get(f"https://html.duckduckgo.com/html/?q={encoded}&kl=us-en")
    if not body:
        return []

    results = []
    # Match result blocks
    blocks = re.findall(
        r'<div class="result(?:\s[^"]*)?">.*?(?=<div class="result|</div>\s*</body>)',
        body, re.DOTALL
    )

    for block in blocks:
        # Title
        title_m = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
        title   = _strip(title_m.group(1)) if title_m else ""
        if not title:
            continue

        # Real URL: decode from DDG redirect ?uddg= param
        url = ""
        uddg_m = re.search(r'uddg=([^&"]+)', block)
        if uddg_m:
            url = urllib.parse.unquote(uddg_m.group(1))
        # Fallback: href from result__a
        if not url:
            href_m = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', block)
            if href_m:
                url = href_m.group(1)
        # Skip ad/tracking URLs
        if not url or "bing.com/aclick" in url or "duckduckgo.com/y.js" in url:
            continue

        # Snippet
        snip_m = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
        snippet = _strip(snip_m.group(1)) if snip_m else ""

        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_urls:
            break

    return results


# ── Step 2: Extract business info from a page ─────────────────────────────────

def _extract_phone(text: str) -> str:
    """Find first US phone number in plain text."""
    m = re.search(
        r'(\(?\b\d{3}\)?[\s\.\-]\d{3}[\s\.\-]\d{4}\b)',
        text
    )
    return m.group(1).strip() if m else ""


def _extract_from_jsonld(body: str) -> dict:
    """Pull LocalBusiness fields from JSON-LD schema blocks."""
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        body, re.DOTALL
    )
    for raw in blocks:
        try:
            data = json.loads(raw.strip())
        except Exception:
            continue
        # Handle array or single object; walk into @graph if present
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("@graph", [data])

        for item in items:
            t = item.get("@type", "")
            if not isinstance(t, str):
                t = " ".join(t) if isinstance(t, list) else ""
            if not any(x in t for x in ["LocalBusiness", "AutoRepair", "Store", "Service",
                                          "AutomotiveBusiness", "Organization"]):
                continue
            name    = item.get("name", "")
            if not name:
                continue
            addr    = item.get("address", {})
            if isinstance(addr, str):
                address = addr
            else:
                address = ", ".join(filter(None, [
                    addr.get("streetAddress", ""),
                    addr.get("addressLocality", ""),
                    addr.get("addressRegion", ""),
                    addr.get("postalCode", ""),
                ]))
            phone   = item.get("telephone", "") or item.get("phone", "")
            rating  = str(item.get("aggregateRating", {}).get("ratingValue", "")) if isinstance(item.get("aggregateRating"), dict) else ""

            # Hours: openingHoursSpecification or openingHours
            hours_raw = item.get("openingHours", item.get("openingHoursSpecification", ""))
            if isinstance(hours_raw, list):
                hours = "; ".join(str(h) for h in hours_raw[:3])
            elif isinstance(hours_raw, dict):
                days  = hours_raw.get("dayOfWeek", [])
                opens = hours_raw.get("opens", "")
                closes= hours_raw.get("closes", "")
                hours = f"{', '.join(days)} {opens}–{closes}".strip() if days else ""
            else:
                hours = str(hours_raw)[:120]

            return {
                "name":    name,
                "phone":   phone,
                "address": address,
                "hours":   hours or "Call for hours",
                "rating":  rating,
            }
    return {}


def scrape_business_page(url: str, title_hint: str = "") -> dict:
    """Fetch a page and extract business contact info."""
    body = _get(url, timeout=7)
    if not body:
        return {}

    info = _extract_from_jsonld(body)

    # If JSON-LD didn't give us enough, supplement from page text
    plain = re.sub(r"<[^>]+>", " ", body)
    plain = _html.unescape(re.sub(r"\s+", " ", plain))

    if not info.get("name"):
        # Use title tag as name
        title_m = re.search(r"<title[^>]*>(.*?)</title>", body, re.DOTALL | re.IGNORECASE)
        name = _strip(title_m.group(1)).split("|")[0].split("–")[0].strip() if title_m else title_hint
        info["name"] = name[:80]

    if not info.get("phone"):
        info["phone"] = _extract_phone(plain) or "N/A"

    if not info.get("address"):
        # Try meta description
        meta_m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content="([^"]+)"', body, re.IGNORECASE)
        if meta_m:
            desc = meta_m.group(1)
            addr_m = re.search(r'\d+\s+\w+[^,]+,\s*\w[\w\s]+,\s*[A-Z]{2}\s+\d{5}', desc)
            info["address"] = addr_m.group(0)[:100] if addr_m else f"{zip_code} area"
        else:
            info["address"] = f"{zip_code} area"

    if not info.get("hours"):
        # Look for hours pattern in body text
        h_m = re.search(
            r'(?:hours?|open)[:\s]+([^\n<]{15,80})',
            plain, re.IGNORECASE
        )
        info["hours"] = h_m.group(1).strip()[:80] if h_m else "Call for hours"

    return info


# ── Step 3: Targeted phone search for missing entries ─────────────────────────

def ddg_phone_search(business_name: str, zip_code: str) -> str:
    """Search DDG specifically to find a phone number for a business."""
    q = f'"{business_name}" {zip_code} phone number'
    encoded = urllib.parse.quote_plus(q)
    body = _get(f"https://html.duckduckgo.com/html/?q={encoded}&kl=us-en")
    if not body:
        return ""
    plain = _html.unescape(re.sub(r"<[^>]+>", " ", body))
    return _extract_phone(plain)


# ── Main ──────────────────────────────────────────────────────────────────────

ddg_results = ddg_search_urls(query, zip_code, max_urls=min(n * 3, 15))

businesses = []
seen_names = set()

for r in ddg_results:
    if len(businesses) >= n:
        break

    title   = r["title"]
    url     = r["url"]
    snippet = r["snippet"]

    # Skip pure directory listing pages (we want actual business sites)
    # but DO process them since they often have the info in structured form
    info = scrape_business_page(url, title_hint=title)

    name = info.get("name", title)
    # Deduplicate
    key = re.sub(r'\W+', '', name.lower())[:30]
    if key in seen_names:
        continue
    seen_names.add(key)

    phone = info.get("phone", "")
    if not phone or phone == "N/A":
        # Try snippet
        phone = _extract_phone(snippet)
    if not phone or phone == "N/A":
        # Last resort: targeted DDG phone search
        phone = ddg_phone_search(name.split("-")[0].strip(), zip_code) or "N/A"

    businesses.append({
        "name":     name,
        "phone":    phone,
        "address":  info.get("address", f"{zip_code} area"),
        "hours":    info.get("hours", snippet[:80] if snippet else "Call for hours"),
        "rating":   info.get("rating", ""),
        "url":      url,
    })
    time.sleep(0.3)   # be polite between fetches

# ── Output ────────────────────────────────────────────────────────────────────

lines = [
    f"📍 LOCAL BUSINESS SEARCH: {query}",
    f"Area: {zip_code} | Within {radius} miles | {len(businesses)} results",
    "─" * 50,
]

for i, b in enumerate(businesses, 1):
    rating_str = f" ★{b['rating']}" if b.get("rating") else ""
    lines.append(f"\n{i}. {b['name']}{rating_str}")
    lines.append(f"   📞 {b['phone']}")
    lines.append(f"   📍 {b['address']}")
    lines.append(f"   🕐 {b['hours']}")
    if b.get("url"):
        lines.append(f"   🌐 {b['url']}")

if not businesses:
    lines.append(f"\n⚠️ No results found for '{query}' near {zip_code}.")
    lines.append("Try a broader search term or check the zip code.")

print("\n".join(lines))
