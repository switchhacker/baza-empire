#!/usr/bin/env python3
"""Static-site builder for ahb123.com. Standard library only."""
import os, json, shutil, hashlib, argparse

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
