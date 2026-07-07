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
import time

from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

try:  # package import (tests) or flat import (uvicorn server:app from browser/)
    from browser import db
    from browser import crawler
    from browser import extractor
    from browser.engine import Engine, UA
    from browser.page_to_md import page_to_md
except ImportError:  # pragma: no cover
    import db
    import crawler
    import extractor
    from engine import Engine, UA
    from page_to_md import page_to_md

try:
    from browser.sessions import SessionManager
except ImportError:  # pragma: no cover
    from sessions import SessionManager

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("phantom_browser")

engine = Engine()
sessions = SessionManager(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    await engine.start()
    import json as _json
    for jid in db.requeue_running():
        job = db.get_job(jid)
        log.info("requeueing crawl job %s after restart", jid)
        _launch_crawl(jid, _json.loads(job["params"]))

    async def _reaper():
        while True:
            await asyncio.sleep(60)
            try:
                n = await sessions.reap_once()
                if n:
                    log.info("reaped %d idle sessions", n)
                db.expire_stale(300)
            except Exception:
                log.exception("reaper iteration failed")

    reaper_task = asyncio.create_task(_reaper())
    yield
    reaper_task.cancel()
    await sessions.close_all()
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


class ExtractReq(BaseModel):
    json_schema: dict = Field(alias="schema")
    url: str | None = None
    urls: list[str] | None = None
    content: str | None = None
    prompt: str | None = None
    model: str | None = None

    model_config = {"populate_by_name": True}


# Background crawl tasks must be held onto — asyncio only keeps a weak
# reference to a task, so a fire-and-forget create_task() can be garbage
# collected mid-run. Standard pattern: track in a module-level set, release
# via a done-callback.
_crawl_tasks: set = set()


def _launch_crawl(job_id: int, params: dict) -> None:
    async def scrape_fn(url, max_chars=3000, **kw):
        return await do_scrape(url, max_chars=max_chars)
    t = asyncio.create_task(crawler.run_crawl(job_id, scrape_fn, params))
    _crawl_tasks.add(t)
    t.add_done_callback(_crawl_tasks.discard)


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


EXTRACT_CONTENT_BUDGET = 24000  # must match extractor.extract's content[:24000] cap


@app.post("/extract")
async def extract_route(req: ExtractReq):
    try:
        sources: list[str] = []
        content = req.content or ""
        urls = req.urls or ([req.url] if req.url else [])
        for u in urls[:5]:
            # A source is only honest if its content actually made it into
            # the window extractor.extract() will see. Once the budget is
            # exhausted, stop scraping/appending entirely rather than
            # listing URLs whose text got truncated away.
            if len(content) >= EXTRACT_CONTENT_BUDGET:
                break
            page = await do_scrape(u, max_chars=8000)
            if page.get("success"):
                chunk = f"\n\n=== {u} ===\n{page['markdown']}"
                remaining = EXTRACT_CONTENT_BUDGET - len(content)
                content += chunk[:remaining]
                sources.append(u)
        if not content.strip():
            return {"success": False, "error": "no content: pass url, urls or content"}
        out = await extractor.extract(content, req.json_schema, req.prompt, req.model)
        out["sources"] = sources
        return out
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


class SessionCreateReq(BaseModel):
    profile: str | None = None


class ActReq(BaseModel):
    url: str | None = None
    index: int | None = None
    text: str | None = None
    key: str | None = None
    dy: int | None = None
    max_chars: int = 6000
    approval_id: int | None = None


def _no_session(sid):
    return {"success": False, "error": f"unknown or expired session '{sid}'",
            "hint": "create a new session"}


@app.post("/session")
async def session_create(req: SessionCreateReq):
    try:
        sid = await sessions.create(profile=req.profile)
        return {"success": True, "session_id": sid, "profile": req.profile}
    except (ValueError, RuntimeError) as e:
        return {"success": False, "error": str(e)}


@app.delete("/session/{sid}")
async def session_close(sid: str):
    await sessions.close(sid)
    return {"success": True}


@app.post("/session/{sid}/goto")
async def session_goto(sid: str, req: ActReq):
    try:
        s = sessions.get(sid)
        blocked = sessions.pending_block(sid)
        if blocked:
            return blocked
        if s.profile and gate.is_gated_goto(req.url):
            desc = (f"Agent wants to GO TO {req.url!r} "
                    f"in logged-in profile '{s.profile}' (session {sid}).")
            action = {"op": "goto", "url": req.url}
            result = await gate.request_approval(sid, action, desc)
            sessions.mark_pending_approval(sid, result["approval_id"])
            return result
        return await sessions.act(sid, "goto", url=req.url)
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/read")
async def session_read(sid: str, req: ActReq):
    try:
        return await sessions.read(sid, max_chars=req.max_chars)
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/click")
async def session_click(sid: str, req: ActReq):
    if req.index is None:
        return {"success": False, "error": "missing required field: index",
                "hint": "call read to get element indexes"}
    try:
        s = sessions.get(sid)
        blocked = sessions.pending_block(sid)
        if blocked:
            return blocked
        if s.profile:
            el = await sessions.element_info(sid, req.index)
            href = (el or {}).get("href") or None
            # Finding 3 (round 2 review): a click that navigates via an <a>
            # to a mutation URL (e.g. <a href="/unsubscribe?token=…">Manage
            # preferences</a>) can carry neutral link text that the
            # text/attr heuristic in is_gated_click misses entirely — and
            # Playwright's click-navigation never routes through
            # session_goto/is_gated_goto on its own. So the href has to be
            # folded into the click-gating decision itself.
            if gate.is_gated_click(el) or (href and gate.is_gated_goto(href)):
                desc = (f"Agent wants to CLICK [{req.index}] "
                        f"{(el or {}).get('tag', '?')} “{(el or {}).get('text', '?')}”"
                        + (f" → {href}" if href else "") +
                        f" in logged-in profile '{s.profile}' (session {sid}).")
                # Bind the approval to the element's identity (finding 2b),
                # not just its index — data-pb-idx is reassigned 0..149 on
                # every read, so index N alone can silently point at a
                # different element by the time Serge approves. href is
                # part of that identity now too (finding 3).
                descriptor = {"tag": (el or {}).get("tag"), "text": (el or {}).get("text"),
                              "href": href}
                action = {"op": "click", "index": req.index, "descriptor": descriptor}
                result = await gate.request_approval(sid, action, desc)
                sessions.mark_pending_approval(sid, result["approval_id"])
                return result
        return await sessions.act(sid, "click", index=req.index)
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/type")
async def session_type(sid: str, req: ActReq):
    try:
        return await sessions.act(sid, "type", index=req.index, text=req.text or "")
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/press")
async def session_press(sid: str, req: ActReq):
    key = req.key or "Enter"
    try:
        s = sessions.get(sid)
        blocked = sessions.pending_block(sid)
        if blocked:
            return blocked
        if s.profile:
            active = await sessions.active_element(sid)
            if gate.is_gated_press(key, active):
                desc = (f"Agent wants to PRESS {key} on "
                        f"“{(active or {}).get('text', '?')}” (POST form) "
                        f"in logged-in profile '{s.profile}' (session {sid}).")
                # Finding 3 (round 2 review): press gets the same
                # element-identity drift protection click already has
                # (finding 2b) — capture {tag,text} of the active element
                # at gate time and re-verify it at decide time before
                # replaying the keypress.
                descriptor = {"tag": (active or {}).get("tag"),
                              "text": (active or {}).get("text")}
                action = {"op": "press", "key": key, "descriptor": descriptor}
                result = await gate.request_approval(sid, action, desc)
                sessions.mark_pending_approval(sid, result["approval_id"])
                return result
        return await sessions.act(sid, "press", key=key)
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/scroll")
async def session_scroll(sid: str, req: ActReq):
    try:
        return await sessions.act(sid, "scroll", dy=req.dy or 800)
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/back")
async def session_back(sid: str, req: ActReq):
    try:
        return await sessions.act(sid, "back")
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/screenshot")
async def session_screenshot(sid: str, req: ActReq):
    try:
        return await sessions.act(sid, "screenshot")
    except KeyError:
        return _no_session(sid)


import json as _json2
from fastapi.responses import HTMLResponse


@app.get("/approvals/{aid}")
async def approval_status(aid: int):
    a = db.get_approval(aid)
    if not a:
        return {"success": False, "error": f"no approval {aid}"}
    return {"success": True, "status": a["status"]}


@app.get("/approvals/{aid}/decide")
async def approval_decide(aid: int, tok: str, d: str):
    a = db.get_approval(aid)
    if not a:
        return HTMLResponse("<h2>Unknown approval.</h2>", status_code=404)
    if tok != a["token"]:
        return HTMLResponse("<h2>Bad token.</h2>", status_code=403)
    if a["status"] != "pending":
        # Belt-and-suspenders (finding 1, round 2 review): this row can
        # reach a terminal status without ever hitting the expiry/deny
        # checks below in THIS request — e.g. the reaper's
        # db.expire_stale(300) sweep flips it to 'expired' behind the
        # scenes, with no browser hit on this route at all. Whatever
        # decided it, the session's freeze marker must not outlive the
        # approval it was guarding — sessions.pending_block() already
        # self-heals this lazily too, but clearing it here means the very
        # next act on the session doesn't even need to make that DB round
        # trip to find out it's free again.
        sessions.clear_pending_approval(a["session_id"], aid)
        return HTMLResponse(f"<h2>Already {a['status']}.</h2>")
    # Deadline enforced here too, not just by the 60s reaper sweep (db.expire_stale):
    # a decision landing between the 300s mark and the next sweep tick must not
    # execute. Status string matches expire_stale's so a stale row always reads
    # the same regardless of which path caught it.
    if time.time() - a["created_at"] > 300:
        db.decide_approval(aid, "expired")
        sessions.clear_pending_approval(a["session_id"], aid)
        return HTMLResponse("<h2>⏰ Expired — 5 min silence window passed.</h2>")
    if d != "approve":
        db.decide_approval(aid, "denied")
        sessions.clear_pending_approval(a["session_id"], aid)
        return HTMLResponse("<h2>❌ Denied.</h2>")

    action = _json2.loads(a["action"])
    op = action.pop("op")
    descriptor = action.pop("descriptor", None)

    # Finding 2b: the approval was described to Serge against a specific
    # element (text + tag [+ href, finding 3]) at a specific data-pb-idx. If
    # the agent re-read the page (or navigated) while the approval sat
    # pending, that index may now belong to a completely different element
    # — re-verify before replaying a click by index; refuse rather than act
    # on a guess.
    if op == "click" and descriptor is not None:
        session_gone = False
        try:
            current = await sessions.element_info(a["session_id"], action.get("index"))
        except KeyError:
            session_gone = True
            current = None
        if not session_gone:
            matches = bool(current) and \
                (current.get("tag") or "") == (descriptor.get("tag") or "") and \
                (current.get("text") or "") == (descriptor.get("text") or "") and \
                (current.get("href") or "") == (descriptor.get("href") or "")
            if not matches:
                db.decide_approval(aid, "expired")
                sessions.clear_pending_approval(a["session_id"], aid)
                return HTMLResponse(
                    "<h2>⚠️ Element changed since approval; re-request.</h2>",
                    status_code=409,
                )

    # Finding 3 (round 2 review): press gets the same drift check click has
    # — the active element's {tag,text} is re-verified before replaying the
    # keypress, since a changed focus/active element means the approval no
    # longer describes what's actually about to happen.
    if op == "press" and descriptor is not None:
        session_gone = False
        try:
            current = await sessions.active_element(a["session_id"])
        except KeyError:
            session_gone = True
            current = None
        if not session_gone:
            matches = bool(current) and \
                (current.get("tag") or "") == (descriptor.get("tag") or "") and \
                (current.get("text") or "") == (descriptor.get("text") or "")
            if not matches:
                db.decide_approval(aid, "expired")
                sessions.clear_pending_approval(a["session_id"], aid)
                return HTMLResponse(
                    "<h2>⚠️ Element changed since approval; re-request.</h2>",
                    status_code=409,
                )

    db.decide_approval(aid, "approved")
    # Unfreeze before replaying — act()'s own pending-approval guard would
    # otherwise refuse this exact, already-approved action.
    sessions.clear_pending_approval(a["session_id"], aid)
    try:
        result = await sessions.act(a["session_id"], op, **action)
        db.decide_approval(aid, "executed" if result.get("success") else "error")
        return HTMLResponse(f"<h2>✅ Approved — action executed.</h2>"
                            f"<p>Now at: {result.get('url', '?')}</p>")
    except KeyError:
        db.decide_approval(aid, "error")
        return HTMLResponse("<h2>⚠️ Approved, but the session already expired.</h2>")
