# tests/test_ahb_web_tab.py — AHB123 dashboard "Web" tab (site status + deploy)
import os, sys, types
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
from flask import Flask
import web_site_routes as w


def make_client():
    app = Flask("t")
    app.register_blueprint(w.web_bp)
    return app.test_client()


def test_dig_ns_parses_runner_output():
    def fake_run(argv, **kw):
        assert "NS" in argv
        return types.SimpleNamespace(returncode=0,
            stdout="evelyn.ns.cloudflare.com.\njavon.ns.cloudflare.com.\n")
    ns = w.dig_ns(runner=fake_run)
    assert ns == ["evelyn.ns.cloudflare.com", "javon.ns.cloudflare.com"]


def test_site_status_reports_cutover_state():
    def fake_fetch(url, timeout=10):
        if "pages.dev" in url:
            return types.SimpleNamespace(status_code=200, headers={}, text="<span class='ahb-nav-title'>All Home Building Co</span>")
        return types.SimpleNamespace(status_code=200, headers={"server": "Squarespace"}, text="squarespace site")
    st = w.site_status(ns=["evelyn.ns.cloudflare.com", "javon.ns.cloudflare.com"], fetch=fake_fetch)
    assert st["ns_cloudflare"] is True
    assert st["preview_ok"] is True
    assert st["apex_source"] == "squarespace"
    assert st["dist_files"] > 0            # real dist/ exists in the repo
    assert st["preview_url"] == "https://ahb123.pages.dev"


def test_site_status_detects_pages_apex():
    def fake_fetch(url, timeout=10):
        return types.SimpleNamespace(status_code=200, headers={}, text="x ahb-nav-title x")
    st = w.site_status(ns=[], fetch=fake_fetch)
    assert st["apex_source"] == "pages"
    assert st["ns_cloudflare"] is False


def test_deploy_site_runs_build_then_wrangler():
    calls = []
    def fake_run(argv, **kw):
        calls.append(argv)
        out = "deployed: https://zz99.ahb123.pages.dev\n" if "deploy.py" in " ".join(argv) else "built 59 files"
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
    url = w.deploy_site(runner=fake_run)
    assert url == "https://zz99.ahb123.pages.dev"
    assert "build.py" in " ".join(calls[0])
    assert "deploy.py" in " ".join(calls[1])
    # account id must be exported for wrangler (Pages token can't enumerate accounts)


def test_deploy_site_raises_on_build_failure():
    def fake_run(argv, **kw):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")
    import pytest
    with pytest.raises(RuntimeError):
        w.deploy_site(runner=fake_run)


def test_http_routes_registered():
    c = make_client()
    rules = {r for r in [str(x) for x in c.application.url_map.iter_rules()]}
    assert "/api/ahb/web/status" in rules
    assert "/api/ahb/web/deploy" in rules


def test_ahb123_template_has_web_tab():
    tpl = open(os.path.join(REPO_ROOT, "dashboard", "templates", "ahb123.html")).read()
    assert 'data-tab="web"' in tpl                       # sub-nav button
    assert 'id="tab-web"' in tpl                         # body-level pane
    assert "switchTab('web')" in tpl
    assert "loadWebTab" in tpl                           # loader wired in switchTab
    assert "/api/ahb/web/status" in tpl
