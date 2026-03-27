#!/usr/bin/env python3
"""
Shared Skill: news
Fetch top news headlines using RSS feeds. No API key needed.
Focuses on crypto, tech, and general top news.
"""
import os
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
category = args.get("category", "all")  # all, crypto, tech, general

FEEDS = {
    "crypto": [
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ],
    "tech": [
        ("Hacker News", "https://hnrss.org/frontpage?count=5"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ],
    "general": [
        ("Reuters", "https://feeds.reuters.com/reuters/topNews"),
        ("AP News", "https://rsshub.app/apnews/topics/apf-topnews"),
    ],
}

if category == "all":
    selected = [item for feeds in FEEDS.values() for item in feeds]
else:
    selected = FEEDS.get(category, FEEDS["general"])

print("=== News Headlines ===")

total = 0
for source, feed_url in selected:
    try:
        req = urllib.request.Request(
            feed_url,
            headers={"User-Agent": "BazaEmpire/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            content = resp.read()

        root = ET.fromstring(content)

        # Handle both RSS and Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)

        count = 0
        print(f"\n  [{source}]")
        for item in items[:3]:
            title = (
                item.findtext("title") or
                item.findtext("atom:title", namespaces=ns) or
                "No title"
            ).strip()
            if title:
                print(f"  • {title}")
                count += 1
                total += 1
            if count >= 3:
                break

    except Exception as e:
        print(f"  [{source}] Error: {e}")

if total == 0:
    print("  No headlines fetched.")
