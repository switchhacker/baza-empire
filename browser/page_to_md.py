"""Rendered HTML → clean markdown + metadata. trafilatura extracts the main
content; markdownify is the fallback for pages trafilatura can't parse."""
import re
from urllib.parse import urljoin, urldefrag

import trafilatura
from markdownify import markdownify


def page_to_md(html: str, url: str, max_chars: int = 8000) -> dict:
    md = None
    try:
        md = trafilatura.extract(
            html, url=url, output_format="markdown",
            include_links=True, include_tables=True,
        )
    except Exception:
        md = None
    if not md:
        body = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
        try:
            md = markdownify(body, strip=["img"]) or ""
        except Exception:
            md = re.sub(r"(?s)<[^>]+>", " ", body)
        md = re.sub(r"\n{3,}", "\n\n", md)
        md = re.sub(r"[ \t]{2,}", " ", md).strip()

    title, description = "", ""
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
        if meta:
            title = (meta.title or "").strip()
            description = (meta.description or "").strip()
    except Exception:
        pass
    if not title:
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()

    links: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"""(?is)<a[^>]+href=["']([^"']+)["']""", html):
        href = m.group(1).strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
            continue
        absu, _ = urldefrag(urljoin(url, href))
        if absu.startswith(("http://", "https://")) and absu not in seen:
            seen.add(absu)
            links.append(absu)
        if len(links) >= 100:
            break

    truncated = len(md) > max_chars
    return {
        "markdown": md[:max_chars],
        "title": title,
        "description": description,
        "links": links,
        "truncated": truncated,
    }
