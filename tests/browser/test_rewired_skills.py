import json
import os
import subprocess
import sys

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS = os.path.join(FRAMEWORK, "skills", "shared")


def run_skill(name, args, env_extra=None):
    env = dict(os.environ)
    env["SKILL_ARGS"] = json.dumps(args)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, os.path.join(SKILLS, name)],
                          capture_output=True, text=True, env=env, timeout=30)


def test_web_search_searxng_primary(tmp_path):
    # stub searxng via the same http stub pattern as test_skills
    from tests.browser.test_skills import Stub, _start_stub
    import urllib.parse

    class SearxStub(Stub):
        responses = {"/search": {"query": "q", "results": [
            {"title": "A", "url": "https://a.test", "content": "snip"}]}}

        # web_search.py's searxng_search() hits GET /search?q=..&format=json —
        # the shared Stub._reply() matches on self.path verbatim (query
        # string included), so it never matches a bare "/search" key. Match
        # on the path component only, same intent as the shared Stub.
        def do_GET(self):
            self.path = urllib.parse.urlparse(self.path).path
            self._reply()

    import http.server, threading
    srv = http.server.HTTPServer(("127.0.0.1", 0), SearxStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = run_skill("web_search.py", {"query": "q", "output": "json"},
                      {"SEARXNG_URL": f"http://127.0.0.1:{srv.server_port}"})
        out = json.loads(p.stdout)
        assert out["source"] == "searxng"
        assert out["results"][0] == {"title": "A", "url": "https://a.test",
                                     "snippet": "snip"}
    finally:
        srv.shutdown()


def test_web_search_falls_back_to_ddg_source_label():
    # searxng unreachable → source must say duckduckgo (network may or may not
    # work in test env; only assert the source label + valid json shape)
    p = run_skill("web_search.py", {"query": "q", "output": "json"},
                  {"SEARXNG_URL": "http://localhost:9"})
    out = json.loads(p.stdout)
    assert "duckduckgo" in out["source"]


def test_scrape_page_shim_keys():
    from tests.browser.test_skills import Stub
    import http.server, threading

    class ScrapeStub(Stub):
        responses = {"/scrape": {"success": True, "url": "https://s.test/",
                                 "title": "T", "markdown": "body text",
                                 "links": [], "truncated": False}}

    srv = http.server.HTTPServer(("127.0.0.1", 0), ScrapeStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = run_skill("scrape_page.py", {"url": "https://s.test/", "output": "json"},
                      {"PHANTOM_BROWSER_URL": f"http://127.0.0.1:{srv.server_port}"})
        out = json.loads(p.stdout)
        assert out == {"success": True, "url": "https://s.test/", "title": "T",
                       "text": "body text", "chars": len("body text")}
    finally:
        srv.shutdown()


def test_web_fetch_shim_keys():
    from tests.browser.test_skills import Stub
    import http.server, threading

    class ScrapeStub(Stub):
        responses = {"/scrape": {"success": True, "url": "https://s.test/",
                                 "title": "T", "markdown": "body text",
                                 "links": [], "truncated": False}}

    srv = http.server.HTTPServer(("127.0.0.1", 0), ScrapeStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = run_skill("web_fetch.py", {"url": "https://s.test/", "output": "json"},
                      {"PHANTOM_BROWSER_URL": f"http://127.0.0.1:{srv.server_port}"})
        out = json.loads(p.stdout)
        assert out["content"] == "body text" and out["success"] is True
    finally:
        srv.shutdown()
