import http.server
import json
import os
import subprocess
import sys
import threading

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS = os.path.join(FRAMEWORK, "skills", "shared")


def run_skill(name, args, base_url):
    env = dict(os.environ)
    env["SKILL_ARGS"] = json.dumps(args)
    env["PHANTOM_BROWSER_URL"] = base_url
    return subprocess.run([sys.executable, os.path.join(SKILLS, name)],
                          capture_output=True, text=True, env=env, timeout=30)


class Stub(http.server.BaseHTTPRequestHandler):
    """Fake :8100 — answers every POST with a canned JSON body per path."""
    responses = {
        "/session": {"success": True, "session_id": "abc123", "profile": None},
        "/session/abc123/goto": {"success": True, "url": "https://s.test/"},
        "/session/abc123/read": {"success": True, "url": "https://s.test/",
                                 "title": "S", "markdown": "# S",
                                 "elements": [{"idx": 0, "tag": "a", "type": "",
                                               "text": "Next", "in_form": False,
                                               "form_method": ""}]},
        "/scrape": {"success": True, "url": "u", "title": "T", "markdown": "# T",
                    "links": [], "truncated": False},
        "/map": {"success": True, "count": 1, "urls": ["https://s.test/a"],
                 "source": "sitemap"},
        "/crawl": {"success": True, "job_id": 7},
        "/crawl/7": {"success": True, "job": {"status": "done"},
                     "pages": [{"url": "https://s.test/", "title": "S",
                                "markdown": "# S", "status": "ok"}]},
        "/extract": {"success": True, "data": {"x": 1}, "sources": ["u"]},
    }

    def _reply(self):
        body = self.responses.get(self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._reply()

    def do_GET(self):
        self._reply()

    def log_message(self, *a):
        pass


def _start_stub():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def test_browse_goto_auto_session():
    srv, base = _start_stub()
    try:
        p = run_skill("browse.py", {"action": "goto", "url": "https://s.test/"}, base)
        out = json.loads(p.stdout)
        assert out["success"] is True and out["session_id"] == "abc123"
    finally:
        srv.shutdown()


def test_browse_read_lists_elements():
    srv, base = _start_stub()
    try:
        p = run_skill("browse.py", {"action": "read", "session_id": "abc123"}, base)
        out = json.loads(p.stdout)
        assert out["elements"][0]["text"] == "Next"
    finally:
        srv.shutdown()


def test_web_scrape_happy():
    srv, base = _start_stub()
    try:
        p = run_skill("web_scrape.py", {"url": "https://s.test/"}, base)
        out = json.loads(p.stdout)
        assert out["success"] is True and out["markdown"] == "# T"
    finally:
        srv.shutdown()


def test_crawl_site_polls_to_done():
    srv, base = _start_stub()
    try:
        p = run_skill("crawl_site.py", {"url": "https://s.test/"}, base)
        out = json.loads(p.stdout)
        assert out["job"]["status"] == "done" and len(out["pages"]) == 1
    finally:
        srv.shutdown()


def test_web_map_and_extract():
    srv, base = _start_stub()
    try:
        m = json.loads(run_skill("web_map.py", {"url": "https://s.test/"}, base).stdout)
        assert m["urls"] == ["https://s.test/a"]
        e = json.loads(run_skill("web_extract.py",
                                 {"url": "u", "schema": {"type": "object"}}, base).stdout)
        assert e["data"] == {"x": 1}
    finally:
        srv.shutdown()


def test_service_down_is_graceful():
    p = run_skill("web_scrape.py", {"url": "https://x.com"}, "http://localhost:9")
    out = json.loads(p.stdout)
    assert out["success"] is False and "phantom-browser" in out["hint"]
