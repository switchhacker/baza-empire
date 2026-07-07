"""Phantom Browser — FastAPI service on :8100. Firecrawl-style verbs
(scrape/search/map/crawl/extract) + interactive sessions for baza agents.

Run: venv/bin/uvicorn server:app --host 0.0.0.0 --port 8100
(WorkingDirectory=browser/, PYTHONPATH=framework root — see systemd unit.)
"""
import asyncio
import html as _html
import logging
import os
import re as _re

from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

try:  # package import (tests) or flat import (uvicorn server:app from browser/)
    from browser import db
    from browser import crawler
    from browser.engine import Engine, UA
    from browser.page_to_md import page_to_md
except ImportError:  # pragma: no cover
    import db
    import crawler
    from engine import Engine, UA
    from page_to_md import page_to_md

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("phantom_browser")

engine = Engine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    await engine.start()
    import json as _json
    for jid in db.requeue_running():
        job = db.get_job(jid)
        log.info("requeueing crawl job %s after restart", jid)
        _launch_crawl(jid, _json.loads(job["params"]))
    yield
    await engine.stop()


app = FastAPI(title="Phantom Browser", version="1.0.0", lifespan=lifespan)


class ScrapeReq(BaseModel):
    url: str
    max_chars: int = 8000
    wait_ms: int = 0
    screenshot: bool = False
    no_cache: bool = False


@app.get("/health")
async def health():
    return {"ok": True, "service": "phantom-browser"}


async def do_scrape(url: str, max_chars: int = 8000, wait_ms: int = 0,
                    screenshot: bool = False, no_cache: bool = False) -> dict:
    cacheable = not screenshot and wait_ms == 0 and max_chars == 8000
    if cacheable and not no_cache:
        hit = db.cache_get(url)
        if hit:
            return {**hit, "cached": True}
    r = await engine.render(url, wait_ms=wait_ms, screenshot=screenshot)
    md = page_to_md(r["html"], r["final_url"], max_chars=max_chars)
    out = {
        "success": True, "url": url, "final_url": r["final_url"],
        "status": r["status"], "title": md["title"],
        "description": md["description"], "markdown": md["markdown"],
        "links": md["links"], "truncated": md["truncated"],
        "screenshot_path": r["screenshot_path"], "cached": False,
    }
    if cacheable:
        db.cache_put(url, out)
    return out


@app.post("/scrape")
async def scrape(req: ScrapeReq):
    try:
        return await do_scrape(req.url, req.max_chars, req.wait_ms,
                               req.screenshot, req.no_cache)
    except Exception as e:
        return {"success": False, "url": req.url, "error": f"{type(e).__name__}: {e}"}


class SearchReq(BaseModel):
    query: str
    n: int = 5
    fetch_content: bool = False
    max_chars: int = 3000


class MapReq(BaseModel):
    url: str
    limit: int = 200


class CrawlReq(BaseModel):
    url: str
    max_pages: int = 50
    max_depth: int = 3
    max_chars: int = 3000
    include_paths: list[str] | None = None
    exclude_paths: list[str] | None = None
    same_domain: bool = True
    ignore_robots: bool = False


def _launch_crawl(job_id: int, params: dict) -> None:
    async def scrape_fn(url, max_chars=3000, **kw):
        return await do_scrape(url, max_chars=max_chars)
    asyncio.create_task(crawler.run_crawl(job_id, scrape_fn, params))


@app.post("/crawl")
async def crawl_start(req: CrawlReq):
    params = req.model_dump()
    job_id = db.create_job(req.url, params)
    _launch_crawl(job_id, params)
    return {"success": True, "job_id": job_id}


@app.get("/crawl/{job_id}")
async def crawl_status(job_id: int, include_content: bool = True):
    job = db.get_job(job_id)
    if not job:
        return {"success": False, "error": f"no such job {job_id}"}
    pages = db.job_pages(job_id)
    if not include_content:
        pages = [{k: v for k, v in p.items() if k != "markdown"} for p in pages]
    return {"success": True, "job": job, "pages": pages}


@app.post("/search")
async def search(req: SearchReq):
    searx = os.environ.get("SEARXNG_URL", "http://localhost:8181")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{searx}/search",
                params={"q": req.query, "format": "json"},
                headers={"User-Agent": UA},
            )
            resp.raise_for_status()
            data = resp.json()
        results = [
            {"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": (r.get("content") or "")[:300]}
            for r in data.get("results", [])[: req.n]
        ]
        if req.fetch_content:
            pages = await asyncio.gather(
                *(do_scrape(r["url"], max_chars=req.max_chars) for r in results),
                return_exceptions=True,
            )
            for r, p in zip(results, pages):
                if isinstance(p, dict) and p.get("success"):
                    r["content"] = p["markdown"]
                else:
                    r["content"] = f"(fetch failed: {p})"
        return {"success": True, "query": req.query, "source": "searxng", "results": results}
    except Exception as e:
        return {"success": False, "query": req.query, "error": f"{type(e).__name__}: {e}"}


def _clean_loc(loc: str) -> str:
    """Clean XML entity-escaped and CDATA-wrapped URL values."""
    loc = loc.strip()
    if loc.startswith("<![CDATA[") and loc.endswith("]]>"):
        loc = loc[9:-3].strip()
    return _html.unescape(loc)


@app.post("/map")
async def map_url(req: MapReq):
    try:
        parsed = urlparse(req.url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        urls: list[str] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for sm in (urljoin(origin, "/sitemap.xml"), urljoin(origin, "/sitemap_index.xml")):
                if urls:
                    break
                try:
                    resp = await client.get(sm, headers={"User-Agent": UA})
                except httpx.HTTPError:
                    continue
                if resp.status_code != 200 or "<loc>" not in resp.text:
                    continue
                locs = [_clean_loc(l) for l in _re.findall(r"<loc>\s*(.*?)\s*</loc>", resp.text)]
                # one level of nested sitemap expansion
                for loc in locs:
                    if len(urls) >= req.limit:
                        break
                    if loc.endswith(".xml"):
                        try:
                            sub = await client.get(loc, headers={"User-Agent": UA})
                            for l2 in _re.findall(r"<loc>\s*(.*?)\s*</loc>", sub.text):
                                l2 = _clean_loc(l2)
                                if not l2.endswith(".xml") and l2 not in seen:
                                    seen.add(l2)
                                    urls.append(l2)
                                if len(urls) >= req.limit:
                                    break
                        except httpx.HTTPError:
                            continue
                    elif loc not in seen:
                        seen.add(loc)
                        urls.append(loc)
        source = "sitemap"
        if not urls:
            source = "links"
            page = await do_scrape(req.url, max_chars=1000)
            urls = [u for u in page.get("links", [])
                    if urlparse(u).netloc == parsed.netloc][: req.limit]
        urls = urls[: req.limit]
        return {"success": True, "url": req.url, "count": len(urls),
                "urls": urls, "source": source}
    except Exception as e:
        return {"success": False, "url": req.url, "error": f"{type(e).__name__}: {e}"}
