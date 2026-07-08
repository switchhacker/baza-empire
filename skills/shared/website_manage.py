#!/usr/bin/env python3
"""Manage the AHBCO websites: ahb123.com (public site, Cloudflare Pages) and
baza.ahb123.com (Baza dashboard behind Cloudflare Access).

Source of truth for ahb123.com is web/ahb123/ in this framework:
content/<slug>.html + content/meta.json -> build.py -> dist/ -> deploy.py
(wrangler -> Cloudflare Pages). This skill lets agents inspect status, read
and edit page content, rebuild, and deploy — with privileged actions gated
on args.approved (Serge must approve edits/deploys; silence is NOT consent).
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime

SKILL_META = {
    "category": "web",
    "summary": ("Manage the AHB123 public website (ahb123.com on Cloudflare Pages) and check the "
                "Baza dashboard site (baza.ahb123.com): cutover status, list/read/edit page content, "
                "rebuild the static site, and deploy to Cloudflare Pages."),
    "when_to_use": ("when asked to check, change, fix, update, build, or deploy the ahb123.com website "
                    "(pages: home, services, portfolio, about, contact, plan), update site copy/SEO, "
                    "or check whether ahb123.com / baza.ahb123.com are live and healthy"),
    "args": {
        "action": "status | pages | read_page | edit_page | build | deploy (required)",
        "slug": "page slug for read_page/edit_page: home|services|portfolio|about|contact|plan",
        "html": "edit_page: full replacement body HTML for the page (mutually exclusive with find/replace)",
        "find": "edit_page: exact substring to find in the page HTML",
        "replace": "edit_page: replacement text for `find`",
        "title": "edit_page: optional new <title> for the page (meta.json)",
        "description": "edit_page: optional new meta description (meta.json)",
        "approved": "true required for edit_page and deploy (Serge must approve; ask first)",
    },
}

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FRAMEWORK_DIR)
SITE_DIR = os.path.join(FRAMEWORK_DIR, "web", "ahb123")
CONTENT_DIR = os.path.join(SITE_DIR, "content")
META_PATH = os.path.join(CONTENT_DIR, "meta.json")
BACKUP_DIR = os.path.join(CONTENT_DIR, ".backups")
DASHBOARD_URL = os.environ.get("BAZA_DASHBOARD_URL", "http://localhost:8888")
BAZA_SITE_URL = "https://baza.ahb123.com"
SLUGS = ("home", "services", "portfolio", "about", "contact", "plan")

try:
    from core import task_events as _te  # type: ignore
except Exception:
    _te = None


def _emit(kind, data):
    if _te is None:
        return None
    try:
        return _te.emit(kind, data, agent_id=os.environ.get("AGENT_ID"))
    except Exception:
        return None


def _fail(msg, **extra):
    print(json.dumps({"error": msg, **extra}))
    sys.exit(1)


def _http(method, url, body=None, timeout=30):
    req = urllib.request.Request(url, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)


def action_status():
    out = {"site": "ahb123.com"}
    try:
        _st, body, _h = _http("GET", DASHBOARD_URL + "/api/ahb/web/status", timeout=45)
        out["ahb123"] = json.loads(body)
    except Exception as e:
        out["ahb123"] = {"error": f"dashboard status unavailable: {e}"}
    # baza.ahb123.com: anonymous request must be intercepted by Cloudflare Access
    baza = {"url": BAZA_SITE_URL}
    try:
        req = urllib.request.Request(BAZA_SITE_URL + "/", method="GET")

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            r = opener.open(req, timeout=15)
            baza["status_code"] = r.status
            baza["access_protected"] = False  # served without auth — should not happen
        except urllib.error.HTTPError as he:
            baza["status_code"] = he.code
            loc = he.headers.get("Location", "")
            # protected = redirected to the Access login, or challenged/denied (401/403)
            baza["access_protected"] = (
                (he.code in (301, 302, 303, 307, 308) and "cloudflareaccess.com" in loc)
                or he.code in (401, 403)
            )
    except Exception as e:
        baza["error"] = str(e)
    # local dashboard behind the tunnel
    try:
        st, _b, _h = _http("GET", DASHBOARD_URL + "/", timeout=10)
        baza["local_dashboard_up"] = st == 200
    except Exception:
        baza["local_dashboard_up"] = False
    out["baza_dashboard"] = baza
    return out


def _load_meta():
    with open(META_PATH, encoding="utf-8") as f:
        return json.load(f)


def action_pages():
    meta = _load_meta()
    pages = []
    for slug in SLUGS:
        path = os.path.join(CONTENT_DIR, slug + ".html")
        st = os.stat(path) if os.path.exists(path) else None
        pages.append({
            "slug": slug,
            "path": path,
            "bytes": st.st_size if st else None,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds") if st else None,
            "title": meta.get(slug, {}).get("title"),
            "description": meta.get(slug, {}).get("description"),
        })
    return {"pages": pages, "note": "edit_page changes content only; run deploy (approved) to publish"}


def _slug(args):
    slug = (args.get("slug") or "").strip()
    if slug not in SLUGS:
        _fail(f"slug must be one of {list(SLUGS)}")
    return slug


def action_read_page(args):
    slug = _slug(args)
    path = os.path.join(CONTENT_DIR, slug + ".html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    return {"slug": slug, "path": path, "meta": _load_meta().get(slug, {}), "html": html}


def action_edit_page(args):
    slug = _slug(args)
    path = os.path.join(CONTENT_DIR, slug + ".html")
    with open(path, encoding="utf-8") as f:
        current = f.read()

    new_html = None
    if args.get("html") is not None:
        new_html = str(args["html"])
    elif args.get("find") is not None:
        find, replace = str(args["find"]), str(args.get("replace") or "")
        n = current.count(find)
        if n == 0:
            _fail("find string not present in page", slug=slug)
        if n > 1:
            _fail(f"find string matches {n} times — make it unique", slug=slug)
        new_html = current.replace(find, replace)

    meta_updates = {k: str(args[k]) for k in ("title", "description") if args.get(k)}
    if new_html is None and not meta_updates:
        _fail("nothing to change: pass html, find/replace, title, or description")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    changed = []
    if new_html is not None and new_html != current:
        shutil.copy2(path, os.path.join(BACKUP_DIR, f"{slug}-{stamp}.html"))
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_html)
        changed.append(f"content/{slug}.html")
    if meta_updates:
        meta = _load_meta()
        shutil.copy2(META_PATH, os.path.join(BACKUP_DIR, f"meta-{stamp}.json"))
        meta.setdefault(slug, {}).update(meta_updates)
        with open(META_PATH, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
            f.write("\n")
        changed.append("content/meta.json")

    return {"slug": slug, "changed": changed, "backup_stamp": stamp,
            "note": "live site NOT updated yet — run action=build to preview, then action=deploy with approved=true"}


def action_build():
    py = os.path.join(FRAMEWORK_DIR, "venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    r = subprocess.run([py, os.path.join(SITE_DIR, "build.py")],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        _fail("build failed", stdout=r.stdout[-2000:], stderr=r.stderr[-2000:])
    dist_files = sum(len(fs) for _r, _d, fs in os.walk(os.path.join(SITE_DIR, "dist")))
    return {"built": True, "dist_files": dist_files, "stdout": r.stdout[-1000:]}


def action_deploy():
    st, body, _h = _http("POST", DASHBOARD_URL + "/api/ahb/web/deploy", body={}, timeout=420)
    try:
        return json.loads(body)
    except Exception:
        return {"status_code": st, "body": body[-2000:]}


def main():
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    action = (args.get("action") or "").strip()
    privileged = action in ("edit_page", "deploy")
    if privileged and not bool(args.get("approved")):
        _fail(f"action '{action}' changes the live website and needs Serge's approval",
              hint="ask Serge first, then rerun with approved=true; silence is not consent")

    _emit("tool_call", {"tool": "website_manage." + action,
                        "args": {k: v for k, v in args.items() if k not in ("html", "approved")}})
    try:
        if action == "status":
            result = action_status()
        elif action == "pages":
            result = action_pages()
        elif action == "read_page":
            result = action_read_page(args)
        elif action == "edit_page":
            result = action_edit_page(args)
        elif action == "build":
            result = action_build()
        elif action == "deploy":
            result = action_deploy()
        else:
            _fail("action must be one of: status, pages, read_page, edit_page, build, deploy")
    except urllib.error.URLError as e:
        _fail(f"dashboard unreachable at {DASHBOARD_URL}: {e}")
    _emit("tool_result", {"tool": "website_manage." + action, "ok": "error" not in result})
    print(json.dumps(result))


if __name__ == "__main__":
    main()
