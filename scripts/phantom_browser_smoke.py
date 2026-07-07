#!/usr/bin/env python3
"""Live smoke test for Phantom Browser. Hits the real service + real web.
Run: venv/bin/python scripts/phantom_browser_smoke.py"""
import json
import sys
import time

import httpx

BASE = "http://localhost:8100"
FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        FAILS.append(name)


def main():
    # /extract may retry a local LLM twice (~180s each), so give the client a
    # budget above the extractor's worst case; one slow/failing check must not
    # abort the rest of the run.
    c = httpx.Client(timeout=400)

    r = c.get(f"{BASE}/health").json()
    check("health", r.get("ok") is True)

    r = c.post(f"{BASE}/scrape", json={"url": "https://example.com"}).json()
    # trafilatura dedups an <h1> matching <title>, so the title text lives in
    # the title field; assert the body rendered to non-empty markdown too.
    check("scrape",
          r.get("success") and "Example Domain" in r.get("title", "")
          and len(r.get("markdown", "")) > 20,
          f"title={r.get('title')!r} md_len={len(r.get('markdown', ''))}")

    r = c.post(f"{BASE}/search", json={"query": "home builder pennsylvania", "n": 3}).json()
    check("search", r.get("success") and len(r.get("results", [])) > 0,
          f"{len(r.get('results', []))} results")

    r = c.post(f"{BASE}/map", json={"url": "https://www.iana.org", "limit": 20}).json()
    check("map", r.get("success") and r.get("count", 0) > 0,
          f"{r.get('count')} urls via {r.get('source')}")

    r = c.post(f"{BASE}/crawl", json={"url": "https://example.com", "max_pages": 2}).json()
    jid = r.get("job_id")
    check("crawl start", bool(jid))
    status = None
    for _ in range(30):
        time.sleep(2)
        j = c.get(f"{BASE}/crawl/{jid}").json()
        status = j["job"]["status"]
        if status in ("done", "error"):
            break
    check("crawl finish", status == "done",
          f"status={status}, pages={len(j.get('pages', []))}")

    r = c.post(f"{BASE}/extract", json={
        "url": "https://example.com",
        "schema": {"type": "object", "required": ["heading"],
                   "properties": {"heading": {"type": "string"}}},
        "prompt": "Extract the page's main heading text.",
    }).json()
    check("extract", r.get("success") and "example" in
          json.dumps(r.get("data", {})).lower(), f"data={r.get('data')}")

    sid = c.post(f"{BASE}/session", json={}).json().get("session_id")
    check("session create", bool(sid))
    c.post(f"{BASE}/session/{sid}/goto", json={"url": "https://example.com"})
    read = c.post(f"{BASE}/session/{sid}/read", json={}).json()
    check("session read", read.get("success") and len(read.get("elements", [])) > 0,
          f"{len(read.get('elements', []))} elements")
    link = next((e for e in read.get("elements", []) if e["tag"] == "a"), None)
    if link:
        c.post(f"{BASE}/session/{sid}/click", json={"index": link["idx"]})
        read2 = c.post(f"{BASE}/session/{sid}/read", json={}).json()
        check("session click nav", read2.get("url") != "https://example.com/",
              f"now at {read2.get('url')}")
    c.delete(f"{BASE}/session/{sid}")

    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
