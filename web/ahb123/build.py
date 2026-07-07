#!/usr/bin/env python3
"""Static-site builder for ahb123.com. Standard library only."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL_BASE = "https://ahb123.com"
SLUG_PATH = {  # slug -> canonical URL path
    "home": "/", "services": "/services", "portfolio": "/portfolio",
    "about": "/about", "contact": "/contact", "plan": "/plan",
}

def _template():
    with open(os.path.join(HERE, "templates", "base.html"), encoding="utf-8") as f:
        return f.read()

def render_page(slug, meta, body_html):
    """Fill base.html for one page. meta = {title, description, og_image}."""
    canonical = CANONICAL_BASE + SLUG_PATH[slug]
    html = _template()
    for key, val in {
        "{{title}}": meta["title"],
        "{{description}}": meta["description"],
        "{{og_image}}": meta["og_image"],
        "{{canonical}}": canonical,
        "{{content}}": body_html,
    }.items():
        html = html.replace(key, val)
    return html
