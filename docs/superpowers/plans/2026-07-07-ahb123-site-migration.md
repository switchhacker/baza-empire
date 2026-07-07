# ahb123.com Static Site Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replicate the ahb123.com marketing site as a standalone static site built by a stdlib Python build script and deployed to Cloudflare Pages, ready to replace Squarespace.

**Architecture:** A tracked source tree under `web/ahb123/` holds page-body content, a single `base.html` template carrying all chrome (nav, footer, `<head>`, brand CSS, Schema.org JSON-LD, Nova widget), a shared `brand.css`, and the image assets. `build.py` (stdlib string templating) renders each page into a clean-URL `dist/` tree; `deploy.py` pushes `dist/` to Cloudflare Pages. Nova chat + lead/review backends are unchanged and referenced cross-origin at `nova.ahb123.com`.

**Tech Stack:** Python 3 standard library (no Jinja/framework), `PIL`/Pillow (already in venv, for one OG image), `pytest` (via `venv/bin/pytest`), Cloudflare Pages + `wrangler` CLI (deploy only).

## Global Constraints

- **Local-first:** `build.py` uses ONLY the Python standard library for templating (no Jinja2, no external template engine). `deploy.py` is the one exception — deploying to Cloudflare Pages is intrinsically a cloud/host action; it shells out to `wrangler` (documented prerequisite). This is a deliberate deviation from the spec's "stdlib-preferred deploy," made because pure-stdlib Cloudflare Direct-Upload (the JWT asset-hash dance) is error-prone; flagged for reviewer.
- **Canonical page sources (exact):** `home.html`, `services.html`, `portfolio.html`, `plan.html` come from `dashboard/artifacts/proj-ahb123/sq_bundle/pages/`. `about.html` and `contact.html` come from `dashboard/artifacts/proj-ahb123/sq_bundle/v2/pages/` (v2 fixed legal errors). Site-wide `<head>` block comes from `dashboard/artifacts/proj-ahb123/sq_bundle/v2/code-injection/header.html` (NOT the v1 header).
- **Legal/contact copy (verbatim, from v2):** email `serge@ahb123.com` (NEVER `info@ahb123.com`); address `Bensalem, PA 19020`; license `PA HIC# PA175897`; phone `(800) 484-6404`; founded `2013`. The string `info@ahb123.com` must never appear in any output.
- **Footer text (verbatim, author into `base.html`):**
  `All Home Building Co LLC · Bensalem, PA 19020`
  `(800) 484-6404 · serge@ahb123.com`
  `PA HIC# PA175897 · Licensed · Insured · Bonded`
- **Clean URLs:** `home` → `dist/index.html`; every other page `X` → `dist/X/index.html` so `/services`, `/portfolio`, `/plan`, `/about`, `/contact` resolve without `.html`.
- **Image path:** portfolio images and logo are served at `/s/<file>` (matches the 48 hard-coded `src="/s/…jpg"` refs). Logo is `/s/logo.png` (PNG, not the JSON-LD's old `logo.svg`).
- **No analytics placeholder:** the string `GA_MEASUREMENT_ID` must never appear in any `dist/` output. GA is omitted entirely unless a real Measurement ID is supplied (deferred to Sub-project B).
- **Tokens:** any Cloudflare API token is stored at file mode `0600` and never logged or printed.
- **Backends unchanged:** do NOT modify `tools/nova_router.py`, the Caddyfile, `ahb_clients`, or the reviews API. Pages only reference `https://nova.ahb123.com`.
- **Tests** live under `tests/` and run with `venv/bin/pytest`. The site source is not importable as a package; tests load modules by path via `tests/ahb123_util.py` (Task 2).
- **Safety:** nothing is deployed to the real `ahb123.com` domain until the preview URL is verified; Squarespace stays paid until after a post-cutover grace period. The apex/www cutover additionally depends on the separate Cloudflare nameserver migration reaching "zone Active."

---

### Task 1: Site source scaffold + content & asset layer

Assemble the editable inputs (page bodies from the correct v1/v2 sources, brand CSS, images, per-page metadata) into a tracked `web/ahb123/` tree, guarded by a completeness + legal-correctness test.

**Files:**
- Create: `web/ahb123/content/{home,services,portfolio,about,contact,plan}.html`
- Create: `web/ahb123/content/meta.json`
- Create: `web/ahb123/assets/css/brand.css`
- Create: `web/ahb123/assets/s/` (48 portfolio JPGs + `logo.png`)
- Create: `web/ahb123/.gitignore` (ignore `../dist/`)
- Test: `tests/test_ahb123_content_layer.py`

**Interfaces:**
- Produces: the `web/ahb123/content/`, `web/ahb123/assets/css/brand.css`, and `web/ahb123/assets/s/` directories consumed by `build.py` (Tasks 2–3). `meta.json` schema: a JSON object mapping slug → `{"title": str, "description": str, "og_image": str}` for slugs `home, services, portfolio, about, contact, plan`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ahb123_content_layer.py
import json, os, re
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "web", "ahb123")
SLUGS = ["home", "services", "portfolio", "about", "contact", "plan"]

def test_all_content_bodies_present_and_nonempty():
    for slug in SLUGS:
        p = os.path.join(SRC, "content", f"{slug}.html")
        assert os.path.isfile(p), f"missing {p}"
        assert os.path.getsize(p) > 100, f"too small {p}"

def test_meta_json_has_all_slugs_with_required_keys():
    meta = json.load(open(os.path.join(SRC, "content", "meta.json")))
    assert set(meta) == set(SLUGS)
    for slug, m in meta.items():
        assert m["title"] and m["description"] and m["og_image"]

def test_v2_legal_facts_present_and_no_stale_email():
    about = open(os.path.join(SRC, "content", "about.html")).read()
    contact = open(os.path.join(SRC, "content", "contact.html")).read()
    assert "PA175897" in about
    assert "serge@ahb123.com" in contact
    for slug in SLUGS:
        body = open(os.path.join(SRC, "content", f"{slug}.html")).read()
        assert "info@ahb123.com" not in body, f"stale email in {slug}"

def test_all_portfolio_images_exist_in_assets():
    portfolio = open(os.path.join(SRC, "content", "portfolio.html")).read()
    refs = set(re.findall(r'/s/([0-9][^"\']+\.jpg)', portfolio))
    assert len(refs) == 48, f"expected 48 image refs, got {len(refs)}"
    for fn in refs:
        assert os.path.isfile(os.path.join(SRC, "assets", "s", fn)), f"missing image {fn}"

def test_logo_and_brand_css_present():
    assert os.path.isfile(os.path.join(SRC, "assets", "s", "logo.png"))
    assert os.path.getsize(os.path.join(SRC, "assets", "css", "brand.css")) > 500
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_ahb123_content_layer.py -v`
Expected: FAIL (files do not exist yet).

- [ ] **Step 3: Create the directory tree and copy the canonical inputs**

Run exactly (from repo root `/home/switchhacker/baza-empire/agent-framework-v3`):
```bash
B=dashboard/artifacts/proj-ahb123/sq_bundle
mkdir -p web/ahb123/content web/ahb123/assets/css web/ahb123/assets/s

# page bodies — v1 for these four:
cp $B/pages/home.html      web/ahb123/content/home.html
cp $B/pages/services.html  web/ahb123/content/services.html
cp $B/pages/portfolio.html web/ahb123/content/portfolio.html
cp $B/pages/plan.html      web/ahb123/content/plan.html
# v2 (fixed legal info) for these two:
cp $B/v2/pages/about.html   web/ahb123/content/about.html
cp $B/v2/pages/contact.html web/ahb123/content/contact.html

# brand CSS:
cp $B/design/custom-css.css web/ahb123/assets/css/brand.css

# 48 portfolio images (IPTC-tagged variants) + logo:
cp $B/gallery-iptc/[0-9]*.jpg web/ahb123/assets/s/
cp $B/v2/assets/logo.png web/ahb123/assets/s/logo.png

printf 'dist/\n' > web/ahb123/.gitignore
ls web/ahb123/assets/s/*.jpg | wc -l   # expect 48
```

- [ ] **Step 4: Author `web/ahb123/content/meta.json`**

Titles/descriptions are transcribed from `dashboard/artifacts/proj-ahb123/sq_bundle/seo/per-page-meta.md`. Read that file for the exact per-page strings and write them in. Use `/s/og-homepage.jpg` as the default `og_image` for all pages (that image is generated in Task 5).

```json
{
  "home":      {"title": "All Home Building Co LLC | Philadelphia Home Builder & Renovation Contractor", "description": "Philadelphia's trusted home builder. Kitchen remodels, bathroom renovations, additions, full rehabs, and new construction. Licensed, insured, locally owned. Free estimates.", "og_image": "/s/og-homepage.jpg"},
  "services":  {"title": "Home Renovation Services | All Home Building Co LLC, Philadelphia", "description": "Full-service home renovation in Philadelphia. Kitchen remodels, bathroom renovations, home additions, full rehabs, new construction, and project management.", "og_image": "/s/og-homepage.jpg"},
  "portfolio": {"title": "Portfolio | All Home Building Co LLC, Philadelphia", "description": "Recent kitchen, bathroom, addition, and whole-home renovation projects across Philadelphia and the surrounding counties.", "og_image": "/s/og-homepage.jpg"},
  "about":     {"title": "About | All Home Building Co LLC, Philadelphia Home Builder", "description": "Founded 2013. Pennsylvania HIC# PA175897, licensed, insured, and bonded. Honest, locally-owned residential construction in Greater Philadelphia.", "og_image": "/s/og-homepage.jpg"},
  "contact":   {"title": "Contact | All Home Building Co LLC, Philadelphia", "description": "Get a free, no-obligation estimate. Call (800) 484-6404 or send your project details. Bensalem, PA. Licensed & insured.", "og_image": "/s/og-homepage.jpg"},
  "plan":      {"title": "Plan Your Project | Free Estimate | All Home Building Co LLC", "description": "Tell us about your renovation or new build and get a free line-item estimate within 24 hours. Kitchen, bath, additions, rehabs, new construction.", "og_image": "/s/og-homepage.jpg"}
}
```

> Verify against `seo/per-page-meta.md`: if that file's title/description for any page differs from the above, the file wins — copy its exact text.

- [ ] **Step 5: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_ahb123_content_layer.py -v`
Expected: PASS (5 tests). If `test_all_portfolio_images_exist_in_assets` fails, check that `gallery-iptc/` supplied all 48 (some numbers may be in `gallery/` instead — copy any missing number from `$B/gallery/`).

- [ ] **Step 6: Commit**

```bash
git add web/ahb123/content web/ahb123/assets tests/test_ahb123_content_layer.py web/ahb123/.gitignore
git commit -m "feat(ahb123): content + asset layer for static site migration"
```

---

### Task 2: base.html template + single-page render in build.py

Build the one HTML shell (nav, footer, `<head>`, JSON-LD, Nova widget) and the `render_page` function that fills it. This is the correctness core.

**Files:**
- Create: `web/ahb123/templates/base.html`
- Create: `web/ahb123/build.py`
- Create: `tests/ahb123_util.py` (path-loader for the site modules)
- Test: `tests/test_ahb123_render.py`

**Interfaces:**
- Produces: `build.render_page(slug: str, meta: dict, body_html: str) -> str`. `meta` is one entry from `meta.json` (keys `title`, `description`, `og_image`). Reads `templates/base.html` relative to `build.py`'s own directory. Returns the full HTML document string.
- Consumes: `web/ahb123/content/*.html`, `content/meta.json`, `assets/css/brand.css` (from Task 1).

- [ ] **Step 1: Write the module loader helper**

```python
# tests/ahb123_util.py
import importlib.util, os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "web", "ahb123")

def load(name):
    """Load web/ahb123/<name>.py as a module by path."""
    path = os.path.join(SRC, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_ahb123_render.py
import json, os
from ahb123_util import load, SRC

def _render_home():
    build = load("build")
    meta = json.load(open(os.path.join(SRC, "content", "meta.json")))["home"]
    body = open(os.path.join(SRC, "content", "home.html")).read()
    return build.render_page("home", meta, body)

def test_render_includes_title_and_description():
    html = _render_home()
    assert "<title>All Home Building Co LLC | Philadelphia Home Builder" in html
    assert 'name="description"' in html and "trusted home builder" in html

def test_render_includes_jsonld_and_legal_facts():
    html = _render_home()
    assert "PA175897" in html            # from v2 header JSON-LD
    assert "serge@ahb123.com" in html
    assert "info@ahb123.com" not in html

def test_render_includes_nova_and_footer():
    html = _render_home()
    assert 'name="nova-base" content="https://nova.ahb123.com"' in html
    assert "nova.ahb123.com/widget.js" in html
    assert "Bensalem, PA 19020" in html
    assert "(800) 484-6404" in html

def test_render_has_no_ga_placeholder():
    assert "GA_MEASUREMENT_ID" not in _render_home()

def test_render_embeds_body_and_brand_css():
    html = _render_home()
    assert "Philadelphia's Trusted Home Builder" in html   # hero from home.html body
    assert '/assets/css/brand.css' in html
```

- [ ] **Step 3: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_ahb123_render.py -v`
Expected: FAIL (`build` module / `base.html` do not exist).

- [ ] **Step 4: Author `web/ahb123/templates/base.html`**

Paste the site-wide `<head>` block verbatim from `dashboard/artifacts/proj-ahb123/sq_bundle/v2/code-injection/header.html` into the marked region (it carries the `nova-base` meta, geo meta, fonts preconnect, and the corrected LocalBusiness + FAQ JSON-LD). If that block's JSON-LD `logo` value is `.../s/logo.svg`, change it to `https://ahb123.com/s/logo.png`.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{title}}</title>
  <meta name="description" content="{{description}}">
  <link rel="canonical" href="{{canonical}}">
  <meta property="og:title" content="{{title}}">
  <meta property="og:description" content="{{description}}">
  <meta property="og:image" content="https://ahb123.com{{og_image}}">
  <meta property="og:type" content="website">
  <meta name="twitter:card" content="summary_large_image">
  <link rel="icon" href="/s/logo.png">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700&family=Source+Sans+3:wght@400;600&display=swap">
  <link rel="stylesheet" href="/assets/css/brand.css">
  <!-- BEGIN v2 header code-injection (nova-base meta, geo meta, LocalBusiness+FAQ JSON-LD) -->
  <!-- PASTE the body of v2/code-injection/header.html here, with logo.svg -> logo.png -->
  <!-- END v2 header code-injection -->
</head>
<body>
  <header class="ahb-nav">
    <a href="/" class="ahb-nav-logo"><img src="/s/logo.png" alt="All Home Building Co LLC" height="44"></a>
    <nav class="ahb-nav-links">
      <a href="/services">Services</a>
      <a href="/portfolio">Portfolio</a>
      <a href="/about">About</a>
      <a href="/contact">Contact</a>
      <a href="/plan" class="ahb-nav-cta">Free Estimate</a>
    </nav>
  </header>

  <main>{{content}}</main>

  <footer class="ahb-footer">
    <p><strong>All Home Building Co LLC</strong> · Bensalem, PA 19020</p>
    <p>(800) 484-6404 · <a href="mailto:serge@ahb123.com">serge@ahb123.com</a></p>
    <p>PA HIC# PA175897 · Licensed · Insured · Bonded</p>
    <p class="ahb-footer-copy">© 2026 All Home Building Co LLC. All rights reserved.</p>
  </footer>

  <script src="https://nova.ahb123.com/widget.js?v=1" defer></script>
</body>
</html>
```

- [ ] **Step 5: Append nav/footer styles to `brand.css`**

The nav/footer classes above are new (Squarespace rendered its own chrome). Append minimal brand-consistent rules to `web/ahb123/assets/css/brand.css`:

```css
/* --- migrated-site nav + footer (Navy #0A2640 / Oak #C4884D) --- */
.ahb-nav{display:flex;align-items:center;justify-content:space-between;padding:14px 32px;background:#fff;border-bottom:1px solid #E5E7EB;position:sticky;top:0;z-index:100;}
.ahb-nav-logo img{display:block;}
.ahb-nav-links{display:flex;gap:28px;align-items:center;font-family:'Montserrat',sans-serif;font-size:14px;text-transform:uppercase;letter-spacing:.05em;}
.ahb-nav-links a{color:#0A2640;text-decoration:none;font-weight:600;}
.ahb-nav-links a:hover{color:#C4884D;}
.ahb-nav-cta{background:#C4884D;color:#fff!important;padding:10px 18px;border-radius:8px;}
.ahb-footer{background:#0A2640;color:rgba(255,255,255,.9);text-align:center;padding:40px 24px;font-family:'Source Sans 3',sans-serif;line-height:1.7;}
.ahb-footer a{color:#C4884D;text-decoration:none;}
.ahb-footer-copy{margin-top:12px;font-size:13px;color:rgba(255,255,255,.55);}
@media(max-width:640px){.ahb-nav{flex-direction:column;gap:12px;}.ahb-nav-links{gap:16px;flex-wrap:wrap;justify-content:center;}}
```

- [ ] **Step 6: Write `web/ahb123/build.py` with `render_page`**

```python
#!/usr/bin/env python3
"""Static-site builder for ahb123.com. Standard library only."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL_BASE = "https://ahb123.com"
SLUG_PATH = {  # slug -> canonical URL path
    "home": "/", "services": "/services", "portfolio": "/portfolio",
    "about": "/about", "contact": "/contact", "plan": "/plan",
}

def _template():
    with open(os.path.join(HERE, "templates", "base.html"), encoding="utf-8") as f:
        return f.read()

def render_page(slug, meta, body_html):
    """Fill base.html for one page. meta = {title, description, og_image}."""
    canonical = CANONICAL_BASE + SLUG_PATH[slug]
    html = _template()
    for key, val in {
        "{{title}}": meta["title"],
        "{{description}}": meta["description"],
        "{{og_image}}": meta["og_image"],
        "{{canonical}}": canonical,
        "{{content}}": body_html,
    }.items():
        html = html.replace(key, val)
    return html
```

- [ ] **Step 7: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_ahb123_render.py -v`
Expected: PASS (5 tests). If `test_render_includes_jsonld_and_legal_facts` fails, the v2 header block was not pasted into `base.html` (Step 4).

- [ ] **Step 8: Commit**

```bash
git add web/ahb123/templates/base.html web/ahb123/build.py web/ahb123/assets/css/brand.css tests/ahb123_util.py tests/test_ahb123_render.py
git commit -m "feat(ahb123): base template + render_page core"
```

---

### Task 3: Full site build — clean URLs, asset copy, sitemap, manifest

Turn `render_page` into a complete `build_site()` that writes the whole `dist/` tree, copies assets, and emits `sitemap.xml` + a deterministic manifest.

**Files:**
- Modify: `web/ahb123/build.py` (add `build_site` + CLI)
- Create: `web/ahb123/seo/robots.txt`
- Test: `tests/test_ahb123_build_site.py`

**Interfaces:**
- Consumes: `render_page` (Task 2), `content/`, `assets/` (Task 1).
- Produces: `build.build_site(dist_dir: str) -> list[str]` returns the sorted list of relative paths written. CLI: `python web/ahb123/build.py [--dist DIR]` (default `web/ahb123/dist`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ahb123_build_site.py
import hashlib, os, tempfile
from ahb123_util import load

def _build():
    build = load("build")
    d = tempfile.mkdtemp(prefix="ahb123dist_")
    build.build_site(d)
    return d

def test_clean_url_tree():
    d = _build()
    assert os.path.isfile(os.path.join(d, "index.html"))              # home
    for slug in ["services", "portfolio", "about", "contact", "plan"]:
        assert os.path.isfile(os.path.join(d, slug, "index.html")), slug

def test_images_and_css_copied():
    d = _build()
    # count only the numbered portfolio images, so this stays correct after
    # Task 5 adds the (unnumbered) og-homepage.jpg to assets/s.
    portfolio_jpgs = [f for f in os.listdir(os.path.join(d, "s"))
                      if f.endswith(".jpg") and f[0].isdigit()]
    assert len(portfolio_jpgs) == 48
    assert os.path.isfile(os.path.join(d, "s", "logo.png"))
    assert os.path.isfile(os.path.join(d, "assets", "css", "brand.css"))

def test_sitemap_lists_six_canonical_urls():
    d = _build()
    sm = open(os.path.join(d, "sitemap.xml")).read()
    for url in ["https://ahb123.com/", "https://ahb123.com/services",
                "https://ahb123.com/portfolio", "https://ahb123.com/about",
                "https://ahb123.com/contact", "https://ahb123.com/plan"]:
        assert f"<loc>{url}</loc>" in sm
    assert sm.count("<loc>") == 6

def test_no_ga_placeholder_anywhere():
    d = _build()
    for root, _, files in os.walk(d):
        for fn in files:
            if fn.endswith((".html", ".xml", ".txt")):
                assert "GA_MEASUREMENT_ID" not in open(os.path.join(root, fn)).read()

def test_build_is_idempotent():
    d1, d2 = _build(), _build()
    m1 = open(os.path.join(d1, "_manifest.txt")).read()
    m2 = open(os.path.join(d2, "_manifest.txt")).read()
    assert m1 == m2 and "_manifest.txt" not in m1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_ahb123_build_site.py -v`
Expected: FAIL (`build_site` not defined).

- [ ] **Step 3: Create `web/ahb123/seo/robots.txt`**

```
User-agent: *
Allow: /
Sitemap: https://ahb123.com/sitemap.xml
```

- [ ] **Step 4: Add `build_site` + CLI to `web/ahb123/build.py`**

Append to `build.py`:

```python
import json, shutil, hashlib, argparse

SLUGS = ["home", "services", "portfolio", "about", "contact", "plan"]

def _dist_relpath(slug):
    return "index.html" if slug == "home" else os.path.join(slug, "index.html")

def _sitemap():
    urls = "".join(
        f"  <url><loc>{CANONICAL_BASE}{SLUG_PATH[s]}</loc></url>\n" for s in SLUGS
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{urls}</urlset>\n")

def build_site(dist_dir):
    """Render all pages + copy assets into dist_dir. Returns sorted rel paths."""
    if os.path.isdir(dist_dir):
        shutil.rmtree(dist_dir)
    os.makedirs(dist_dir)
    meta = json.load(open(os.path.join(HERE, "content", "meta.json"), encoding="utf-8"))
    written = []
    for slug in SLUGS:
        body = open(os.path.join(HERE, "content", f"{slug}.html"), encoding="utf-8").read()
        html = render_page(slug, meta[slug], body)
        rel = _dist_relpath(slug)
        dest = os.path.join(dist_dir, rel)
        os.makedirs(os.path.dirname(dest) or dist_dir, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(html)
        written.append(rel)
    # assets
    shutil.copytree(os.path.join(HERE, "assets", "s"), os.path.join(dist_dir, "s"))
    shutil.copytree(os.path.join(HERE, "assets", "css"),
                    os.path.join(dist_dir, "assets", "css"))
    # seo
    with open(os.path.join(dist_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(_sitemap())
    shutil.copy(os.path.join(HERE, "seo", "robots.txt"),
                os.path.join(dist_dir, "robots.txt"))
    # deterministic manifest (excludes itself)
    rels = []
    for root, _, files in os.walk(dist_dir):
        for fn in files:
            full = os.path.join(root, fn)
            rels.append(os.path.relpath(full, dist_dir))
    rels.sort()
    lines = []
    for rel in rels:
        h = hashlib.sha256(open(os.path.join(dist_dir, rel), "rb").read()).hexdigest()
        lines.append(f"{h}  {rel}")
    with open(os.path.join(dist_dir, "_manifest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return sorted(rels)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=os.path.join(HERE, "dist"))
    args = ap.parse_args()
    paths = build_site(args.dist)
    print(f"built {len(paths)} files -> {args.dist}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_ahb123_build_site.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Do a real build and eyeball it**

Run: `venv/bin/python web/ahb123/build.py`
Expected: `built 58 files -> .../web/ahb123/dist` (6 html + 48 jpg + logo.png + brand.css + sitemap.xml + robots.txt; the manifest is written after the count so it is not included in the printed number). After Task 5 adds og-homepage.jpg this becomes 59. Open `web/ahb123/dist/index.html` in a browser to confirm layout.

- [ ] **Step 7: Commit**

```bash
git add web/ahb123/build.py web/ahb123/seo/robots.txt tests/test_ahb123_build_site.py
git commit -m "feat(ahb123): full site build with clean URLs, sitemap, manifest"
```

---

### Task 4: deploy.py — push dist/ to Cloudflare Pages

Deploy `dist/` to a Cloudflare Pages project via `wrangler`, reading a 0600-stored token. Upload mechanics are wrangler's; `deploy.py` handles token loading, invocation, and URL parsing — all testable with an injected runner.

**Files:**
- Create: `web/ahb123/deploy.py`
- Test: `tests/test_ahb123_deploy.py`

**Interfaces:**
- Produces: `deploy.load_token(path: str) -> str` (raises `FileNotFoundError` if absent; strips whitespace); `deploy.deploy(dist_dir, project, token, runner=subprocess.run) -> str` returns the `*.pages.dev` URL parsed from wrangler stdout. `runner` is injectable for tests; it is called with the wrangler argv and must return an object with `.returncode` and `.stdout`.
- Token file default path: `web/ahb123/.cf_pages_token` (mode 0600, gitignored).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ahb123_deploy.py
import os, stat, tempfile, types, pytest
from ahb123_util import load

def test_load_token_strips_and_reads():
    deploy = load("deploy")
    fd, p = tempfile.mkstemp(); os.write(fd, b"  tok123\n  "); os.close(fd)
    assert deploy.load_token(p) == "tok123"

def test_load_token_missing_raises():
    deploy = load("deploy")
    with pytest.raises(FileNotFoundError):
        deploy.load_token("/no/such/token")

def test_deploy_invokes_wrangler_and_parses_url():
    deploy = load("deploy")
    calls = {}
    def fake_runner(argv, **kw):
        calls["argv"] = argv; calls["env"] = kw.get("env", {})
        return types.SimpleNamespace(
            returncode=0,
            stdout="Uploading... done.\nDeployment complete! https://abcd1234.ahb123.pages.dev\n")
    url = deploy.deploy("/tmp/dist", "ahb123", "TOK", runner=fake_runner)
    assert url == "https://abcd1234.ahb123.pages.dev"
    assert "pages" in calls["argv"] and "deploy" in calls["argv"]
    assert "/tmp/dist" in calls["argv"]
    assert calls["env"].get("CLOUDFLARE_API_TOKEN") == "TOK"

def test_deploy_raises_on_nonzero():
    deploy = load("deploy")
    def fake_runner(argv, **kw):
        return types.SimpleNamespace(returncode=1, stdout="auth error")
    with pytest.raises(RuntimeError):
        deploy.deploy("/tmp/dist", "ahb123", "TOK", runner=fake_runner)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_ahb123_deploy.py -v`
Expected: FAIL (`deploy` module missing).

- [ ] **Step 3: Write `web/ahb123/deploy.py`**

```python
#!/usr/bin/env python3
"""Deploy web/ahb123/dist to Cloudflare Pages via wrangler.

Prerequisite: node + `npx wrangler` available. Token is a Cloudflare API token
scoped to Pages:Edit, stored at web/ahb123/.cf_pages_token (mode 0600).
"""
import os, re, subprocess, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TOKEN = os.path.join(HERE, ".cf_pages_token")
_URL_RE = re.compile(r"https://[a-z0-9-]+\.[a-z0-9-]*\.?pages\.dev")

def load_token(path=DEFAULT_TOKEN):
    with open(path) as f:
        return f.read().strip()

def deploy(dist_dir, project, token, runner=subprocess.run):
    env = dict(os.environ)
    env["CLOUDFLARE_API_TOKEN"] = token
    argv = ["npx", "--yes", "wrangler", "pages", "deploy", dist_dir,
            f"--project-name={project}"]
    res = runner(argv, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"wrangler deploy failed: {res.stdout}")
    m = _URL_RE.search(res.stdout or "")
    return m.group(0) if m else ""

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=os.path.join(HERE, "dist"))
    ap.add_argument("--project", default="ahb123")
    ap.add_argument("--token-file", default=DEFAULT_TOKEN)
    args = ap.parse_args()
    url = deploy(args.dist, args.project, load_token(args.token_file))
    print(f"deployed: {url}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_ahb123_deploy.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Add token file to gitignore and commit**

```bash
printf '.cf_pages_token\n' >> web/ahb123/.gitignore
git add web/ahb123/deploy.py web/ahb123/.gitignore tests/test_ahb123_deploy.py
git commit -m "feat(ahb123): Cloudflare Pages deploy script"
```

> Live deploy is an operator step (Task 5 runbook): create the `ahb123` Pages project + `Pages:Edit` token, write it to `web/ahb123/.cf_pages_token` (`chmod 600`), then `venv/bin/python web/ahb123/deploy.py`.

---

### Task 5: OG image, finalize assets, README runbook (build → preview → cutover → cancel)

Generate the missing social-share image, guard against the two remaining asset gaps, and write the operator runbook covering the safe cutover and rollback.

**Files:**
- Create: `web/ahb123/make_og_image.py`
- Create: `web/ahb123/assets/s/og-homepage.jpg`
- Create: `web/ahb123/README.md`
- Test: `tests/test_ahb123_finalize.py`

**Interfaces:**
- Consumes: `assets/s/*.jpg` (Task 1), `base.html` (Task 2).
- Produces: `assets/s/og-homepage.jpg` (1200×630 JPEG) referenced by every page's `og_image`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ahb123_finalize.py
import os
from PIL import Image
from ahb123_util import SRC

def test_og_image_exists_and_correct_size():
    p = os.path.join(SRC, "assets", "s", "og-homepage.jpg")
    assert os.path.isfile(p)
    assert Image.open(p).size == (1200, 630)

def test_base_template_uses_png_logo_not_svg():
    base = open(os.path.join(SRC, "templates", "base.html")).read()
    assert "logo.svg" not in base
    assert "GA_MEASUREMENT_ID" not in base

def test_readme_has_rollback_ips_and_cancel_step():
    readme = open(os.path.join(SRC, "README.md")).read()
    assert "198.49.23.144" in readme          # Squarespace rollback A record
    assert "ext-sq.squarespace.com" in readme
    assert "pages.dev" in readme
    assert "Cancel" in readme or "cancel" in readme
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_ahb123_finalize.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `web/ahb123/make_og_image.py` and run it**

Crops a hero portfolio image to a 1200×630 social card.

```python
#!/usr/bin/env python3
"""Generate assets/s/og-homepage.jpg (1200x630) from a hero portfolio image."""
import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_IMG = os.path.join(HERE, "assets", "s", "01-modern-kitchen-fishtown.jpg")
OUT = os.path.join(HERE, "assets", "s", "og-homepage.jpg")
TARGET = (1200, 630)

def make():
    im = Image.open(SRC_IMG).convert("RGB")
    tw, th = TARGET
    scale = max(tw / im.width, th / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)))
    left = (im.width - tw) // 2
    top = (im.height - th) // 2
    im.crop((left, top, left + tw, top + th)).save(OUT, "JPEG", quality=85)
    print("wrote", OUT)

if __name__ == "__main__":
    make()
```

Run: `venv/bin/python web/ahb123/make_og_image.py`
Expected: `wrote .../assets/s/og-homepage.jpg`

- [ ] **Step 4: Fix the logo reference in base.html if needed**

If `test_base_template_uses_png_logo_not_svg` still fails, edit `web/ahb123/templates/base.html` and replace any remaining `logo.svg` (in the pasted v2 JSON-LD `logo` field) with `logo.png`.

- [ ] **Step 5: Write `web/ahb123/README.md`**

Include, in full: the build command, deploy command, the preview verification checklist, and the cutover + rollback runbook. Must contain these exact operational facts (the test checks for them):

```markdown
# ahb123.com static site (Cloudflare Pages)

## Build
    venv/bin/python web/ahb123/make_og_image.py   # once, regenerates og image
    venv/bin/python web/ahb123/build.py           # -> web/ahb123/dist/

## Deploy (preview)
One-time: create Cloudflare Pages project `ahb123`; create an API token scoped
to **Pages:Edit**; save it: `echo TOKEN > web/ahb123/.cf_pages_token && chmod 600 web/ahb123/.cf_pages_token`.
    venv/bin/python web/ahb123/deploy.py          # prints the *.pages.dev URL

## Preview verification checklist (on the *.pages.dev URL — Squarespace still live)
- [ ] All 6 pages render with Navy/Oak branding, desktop + mobile
- [ ] All 48 portfolio images load
- [ ] Nova chat widget opens and replies
- [ ] /plan multi-step form submits -> new ahb_clients row (source=plan_page) -> Rex Telegram alert
- [ ] /contact reviews grid + QR load (served from nova.ahb123.com)
- [ ] Footer shows: All Home Building Co LLC · Bensalem, PA 19020 · (800) 484-6404 · serge@ahb123.com · PA HIC# PA175897
- [ ] View-source JSON-LD passes Google Rich Results test

## Cutover (ONLY after nameserver migration shows zone "Active" in Cloudflare)
1. Cloudflare Pages -> project ahb123 -> Custom domains -> add `ahb123.com` and `www.ahb123.com`.
   Cloudflare auto-replaces the apex A records / www CNAME.
2. Re-run the verification checklist on the real https://ahb123.com.
3. Leave Squarespace PAID and untouched for a few days (fallback).

## Rollback (any time before cancelling Squarespace)
Repoint DNS back to Squarespace:
    ahb123.com  A      198.49.23.144
    ahb123.com  A      198.185.159.144
    ahb123.com  A      198.185.159.145
    ahb123.com  A      198.49.23.145
    www         CNAME  ext-sq.squarespace.com

## Cancel Squarespace
Only after several days of the real domain serving from Pages with no issues,
Cancel the Squarespace subscription. Then remove the Squarespace-only
`_domainconnect` CNAME from DNS.
```

- [ ] **Step 6: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_ahb123_finalize.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Rebuild so dist picks up the OG image, then commit**

```bash
venv/bin/python web/ahb123/build.py
git add web/ahb123/make_og_image.py web/ahb123/assets/s/og-homepage.jpg web/ahb123/README.md tests/test_ahb123_finalize.py
git commit -m "feat(ahb123): OG image, logo fix, deploy/cutover runbook"
```

- [ ] **Step 8: Full suite sanity check**

Run: `venv/bin/pytest tests/test_ahb123_content_layer.py tests/test_ahb123_render.py tests/test_ahb123_build_site.py tests/test_ahb123_deploy.py tests/test_ahb123_finalize.py -v`
Expected: all green (22 tests).

---

## Post-plan operator actions (not code — done with Serge)

1. Create the Cloudflare Pages `ahb123` project + `Pages:Edit` token; first live preview deploy.
2. Walk the preview verification checklist.
3. After the nameserver migration reaches "Active," do the custom-domain cutover.
4. Grace period, then cancel Squarespace.

These depend on Serge's Cloudflare account and the in-flight nameserver migration; they are documented in `web/ahb123/README.md`.
