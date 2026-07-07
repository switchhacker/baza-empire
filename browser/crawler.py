"""BFS crawl: pure frontier logic + the async job runner. robots.txt is
honored for bulk crawls only (spec: single-page scrape/sessions are 'browsing
like Serge would' and skip it)."""
import logging
import re
import urllib.robotparser
from urllib.parse import urldefrag, urlparse

import httpx

try:
    from browser import db
except ImportError:  # pragma: no cover
    import db

log = logging.getLogger("phantom_browser.crawler")

BINARY_RX = re.compile(
    r"\.(png|jpe?g|gif|webp|svg|ico|css|js|mjs|pdf|zip|gz|tar|mp4|mp3|wav|woff2?|ttf|eot)($|\?)",
    re.I,
)


def normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    if urlparse(url).path == "":
        url += "/"
    return url


def should_visit(url, root_url, visited, include_paths=None, exclude_paths=None,
                 same_domain=True) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    if url in visited:
        return False
    p, r = urlparse(url), urlparse(root_url)
    if same_domain and p.netloc != r.netloc:
        return False
    path = p.path or "/"
    if BINARY_RX.search(path):
        return False
    if include_paths and not any(re.search(pat, path) for pat in include_paths):
        return False
    if exclude_paths and any(re.search(pat, path) for pat in exclude_paths):
        return False
    return True


_robots: dict[str, object] = {}


async def robots_allows(url: str, ua: str = "PhantomBrowser") -> bool:
    """Async, time-bounded robots.txt check. Fail-open: any fetch failure or
    non-2xx status is treated as ALLOWED — this preserves the previous
    sync-urllib implementation's failure policy (a read() exception cached
    "unreachable" → always allow). Cache is per-origin (parsed RobotFileParser
    or the "unreachable" sentinel), not per-URL."""
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    rp = _robots.get(origin)
    if rp is None:
        rp = "unreachable"  # default until a 2xx robots.txt is parsed
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{origin}/robots.txt",
                                        headers={"User-Agent": ua})
            if 200 <= resp.status_code < 300:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(resp.text.splitlines())
                rp = parser
        except Exception as e:
            log.warning("robots.txt fetch failed for %s: %s — failing open", origin, e)
        _robots[origin] = rp
    if rp == "unreachable":
        return True
    return rp.can_fetch(ua, url)


async def run_crawl(job_id: int, scrape_fn, params: dict) -> None:
    """scrape_fn: async (url, max_chars=...) -> scrape dict (do_scrape)."""
    try:
        root = normalize_url(params["url"])
        max_pages = int(params.get("max_pages", 50))
        max_depth = int(params.get("max_depth", 3))
        max_chars = int(params.get("max_chars", 3000))
        include_paths = params.get("include_paths")
        exclude_paths = params.get("exclude_paths")
        same_domain = bool(params.get("same_domain", True))
        ignore_robots = bool(params.get("ignore_robots", False))

        db.set_job_status(job_id, "running")

        # Resume support: seed visited from prior progress so a requeued job
        # (e.g. after a service restart) doesn't re-scrape pages it already
        # recorded, and max_pages bounds the job's LIFETIME total rather than
        # resetting to 0 on every relaunch.
        visited: set[str] = set()
        existing = db.job_pages(job_id)
        for row in existing:
            visited.add(row["url"])

        queue: list[tuple[str, int]] = [(root, 0)]
        while queue and len(visited) < max_pages:
            url, depth = queue.pop(0)
            url = normalize_url(url)
            if url in visited:
                continue
            visited.add(url)
            if not ignore_robots and not await robots_allows(url):
                db.add_page(job_id, url, None, None, status="error",
                            error="robots.txt disallow")
                continue
            try:
                page = await scrape_fn(url, max_chars=max_chars)
            except Exception as e:
                db.add_page(job_id, url, None, None, status="error",
                            error=f"{type(e).__name__}: {e}")
                continue
            if not page.get("success"):
                db.add_page(job_id, url, None, None, status="error",
                            error=page.get("error", "scrape failed"))
                continue
            db.add_page(job_id, url, page.get("title"), page.get("markdown"))
            if depth < max_depth:
                queued = {q for q, _ in queue}
                for link in page.get("links", []):
                    ln = normalize_url(link)
                    if ln not in queued and should_visit(
                        ln, root, visited, include_paths, exclude_paths, same_domain
                    ):
                        queue.append((ln, depth + 1))
                        queued.add(ln)
        db.set_job_status(job_id, "done")
    except Exception as e:
        log.exception("crawl job %s failed", job_id)
        db.set_job_status(job_id, "error", error=f"{type(e).__name__}: {e}")
