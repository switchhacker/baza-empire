"""AHB123 dashboard "Web" tab — status + deploy for the ahb123.com static site.

The site lives at web/ahb123/ (build.py -> dist/, deploy.py -> Cloudflare Pages).
Status shows where the migration stands: nameservers, preview health, and
whether the real apex is still serving Squarespace or already Pages.
"""
import os
import subprocess
import sys

from flask import Blueprint, jsonify

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_DIR = os.path.join(REPO_ROOT, "web", "ahb123")
DIST_DIR = os.path.join(SITE_DIR, "dist")
DOMAIN = "ahb123.com"
PREVIEW_URL = "https://ahb123.pages.dev"
LIVE_URL = "https://ahb123.com"
# Pages:Edit tokens can't enumerate accounts, so wrangler needs this explicitly.
CF_ACCOUNT_ID = "6772f42534eb0597cca169adc4c41e69"

web_bp = Blueprint("ahb_web", __name__)


def dig_ns(domain=DOMAIN, runner=subprocess.run):
    res = runner(["dig", "+short", "NS", domain], capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        return []
    return [line.strip().rstrip(".") for line in res.stdout.splitlines() if line.strip()]


def _default_fetch(url, timeout=10):
    return requests.get(url, timeout=timeout)


def site_status(ns=None, fetch=None):
    """Assemble the cutover-state dict shown in the Web tab."""
    fetch = fetch or _default_fetch
    if ns is None:
        ns = dig_ns()

    preview_ok, apex_source = False, "unknown"
    try:
        r = fetch(PREVIEW_URL + "/", timeout=10)
        preview_ok = r.status_code == 200 and "All Home Building" in r.text
    except Exception:
        pass
    try:
        r = fetch(LIVE_URL + "/", timeout=10)
        if r.status_code in (200, 301, 308):
            server = (r.headers.get("server") or "").lower()
            if "squarespace" in server or "squarespace" in r.text.lower():
                apex_source = "squarespace"
            elif "ahb-nav-title" in r.text:
                apex_source = "pages"
    except Exception:
        pass

    dist_files = 0
    for _root, _dirs, files in os.walk(DIST_DIR):
        dist_files += len(files)

    return {
        "domain": DOMAIN,
        "ns": ns,
        "ns_cloudflare": bool(ns) and all(n.endswith(".ns.cloudflare.com") for n in ns),
        "preview_url": PREVIEW_URL,
        "preview_ok": preview_ok,
        "live_url": LIVE_URL,
        "apex_source": apex_source,
        "dist_files": dist_files,
    }


def deploy_site(runner=subprocess.run):
    """Rebuild dist/ then push to Cloudflare Pages. Returns the deployment URL."""
    py = os.path.join(REPO_ROOT, "venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    env = dict(os.environ, CLOUDFLARE_ACCOUNT_ID=CF_ACCOUNT_ID)

    build = runner([py, os.path.join(SITE_DIR, "build.py")],
                   capture_output=True, text=True, timeout=120, env=env)
    if build.returncode != 0:
        raise RuntimeError(f"build failed: {build.stdout}\n{getattr(build, 'stderr', '')}")

    dep = runner([py, os.path.join(SITE_DIR, "deploy.py")],
                 capture_output=True, text=True, timeout=300, env=env)
    if dep.returncode != 0:
        raise RuntimeError(f"deploy failed: {dep.stdout}\n{getattr(dep, 'stderr', '')}")
    for tok in dep.stdout.split():
        if ".pages.dev" in tok:
            return tok.strip().rstrip("/")
    raise RuntimeError(f"deploy succeeded but no *.pages.dev URL in output:\n{dep.stdout}")


@web_bp.get("/api/ahb/web/status")
def api_status():
    return jsonify(site_status())


@web_bp.post("/api/ahb/web/deploy")
def api_deploy():
    try:
        url = deploy_site()
        return jsonify({"ok": True, "url": url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
