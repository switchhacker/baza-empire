# ahb123.com Static Site Migration (Squarespace → Cloudflare Pages) — Design

**Date:** 2026-07-07
**Status:** Approved (design) — pending spec review, then implementation plan
**Author:** Claude + Serge

## Goal

Replicate the public ahb123.com marketing site from Squarespace as a standalone
static site, host it on **Cloudflare Pages** (free, global CDN, automatic SSL),
cut over the `apex + www` DNS, verify, and then cancel the Squarespace
subscription. The Nova chat widget and lead-capture backend already run on baza
and are **out of scope** — they stay exactly as they are.

This is **Sub-project A**. A follow-on **Sub-project B** (a dashboard
content-editor panel with a Publish button) gets its own spec later; A is
architected so B is a clean add-on, but B is not built here.

## Non-Goals

- No redesign. This is a 1:1 replica of the existing site's content and brand.
  (Serge confirmed the April paste-bundle is still essentially the live site —
  "no / minor" edits made directly in Squarespace since.)
- No change to Nova chat (`nova.ahb123.com`), the `/api/leads` backend, or the
  `ahb_clients` pipeline. Pages embed the existing widget/form unchanged.
- No content-editor UI (that is Sub-project B).
- No move of the domain **registration** or the **nova** subdomain delegation.
  `nova.ahb123.com` stays delegated to deSEC; only apex + www move to Pages.

## Source of Truth

The complete site source already exists at
`dashboard/artifacts/proj-ahb123/sq_bundle/`:

- `pages/*.html` — 6 page **body fragments** (home, services, portfolio, about,
  contact, plan). These are content-only; Squarespace supplied the surrounding
  nav/footer chrome.
- `code-injection/header.html` — site-wide `<head>` additions: `nova-base` meta,
  geo meta, Google-Fonts preconnect, and two Schema.org JSON-LD blocks
  (LocalBusiness/HomeAndConstructionBusiness + FAQPage).
- `code-injection/footer.html` — site-wide end-of-body: Nova widget `<script>`
  and a GA4 tag with a **placeholder** `GA_MEASUREMENT_ID` (never set to a real
  ID).
- `gallery/` + `gallery-iptc/` — the 48 portfolio images (IPTC-tagged variants
  in `gallery-iptc/`).
- `seo/` — `robots.txt`, `sitemap-supplement.xml`, `per-page-meta.md`
  (per-page `<title>` / description / OG blocks).

Verified facts pulled from the bundle that constrain the build:

- **Portfolio** hard-codes 48 image `src`s at the path `/s/<filename>.jpg`
  (Squarespace asset path). The build MUST serve these images at the same `/s/`
  path so the portfolio markup and any external links keep working.
- **Header** references `https://ahb123.com/s/logo.svg` and
  `https://ahb123.com/s/og-homepage.jpg` (Schema logo + OG image). These two
  assets are **not** in the bundle's `gallery/` set and must be located or
  produced during implementation (see Open Items).
- **Plan page** posts a multi-step form via `fetch(NOVA_BASE + '/api/leads')`
  with `source=plan_page`, where `NOVA_BASE` is read from the
  `<meta name="nova-base">` tag (`https://nova.ahb123.com`). The new site is
  same-origin `ahb123.com` → nova cross-origin exactly as today, so the existing
  CORS config on the nova router is unchanged.
- **Clean URLs in use:** internal links point to `/plan`, `/services`,
  `/portfolio` (no `.html`). The build must produce directory-style output so
  those paths resolve.

## Architecture

New site source lives under `dashboard/artifacts/proj-ahb123/site/`, separate
from `sq_bundle/` (which is the input, left untouched):

```
site/
  templates/
    base.html          Full HTML5 shell: <head> (brand CSS, fonts, meta, JSON-LD),
                       <nav>, {{content}}, <footer>, Nova widget <script>, GA4.
  content/             THE EDITABLE LAYER (Sub-project B will edit these):
    home.html          page body fragments, lifted verbatim from sq_bundle/pages/
    services.html
    portfolio.html
    about.html
    contact.html
    plan.html
    meta.json          per-page {title, description, og_image, slug} from per-page-meta.md
  assets/
    css/brand.css      extracted shared brand CSS (Navy #0A2640 / Oak #C4884D system)
    s/                 the 48 portfolio images + logo.svg + og-homepage.jpg
                       (served at /s/ to match hard-coded refs)
    favicon.ico
  seo/
    robots.txt
    sitemap.xml        regenerated for the 6 canonical pages
  build.py             content + template -> dist/ (stdlib only)
  deploy.py            push dist/ to Cloudflare Pages via Direct Upload API
  README.md            build/deploy/rollback runbook
dist/                  build output (gitignored); deployed artifact
```

### Content / template separation (enables Sub-project B)

- Page **bodies** live in `content/*.html` — a single editable region per page.
- All **chrome** (nav, footer, `<head>`, brand CSS, JSON-LD, widget/GA scripts)
  lives once in `templates/base.html`.
- `build.py` renders each page = `base.html` with `{{content}}` = the page body
  and `{{title}}`/`{{description}}`/`{{og_*}}` = the page's `meta.json` entry.
- This is the seam Sub-project B plugs into: an editor that writes `content/*.html`
  + `meta.json` and triggers `build.py` + `deploy.py`. Not built here, but the
  boundary is fixed now so B needs no rework of A.

### build.py (stdlib only — local-first rule)

- Pure Python standard library (string templating; no Jinja/framework), run on
  the box's `python3`.
- Reads `templates/base.html`, `content/*.html`, `content/meta.json`.
- **Clean-URL output mapping:**
  - `home` → `dist/index.html`
  - every other page `X` → `dist/X/index.html` (so `/services`, `/portfolio`,
    `/plan`, `/about`, `/contact` resolve without `.html`)
- Copies `assets/s/*` → `dist/s/*` (preserves the `/s/` image path), `assets/css`
  → `dist/assets/css`, `favicon.ico`, and `seo/{robots.txt,sitemap.xml}` → `dist/`.
- Emits a `dist/_manifest.txt` (relative path + sha256 per file) used by tests
  and by the deploy step.
- Idempotent: a clean rebuild from unchanged inputs produces byte-identical
  output (stable ordering, no timestamps embedded).

### deploy.py (Cloudflare Pages Direct Upload)

- Uses the **Cloudflare Pages Direct Upload API** to push `dist/` to a Pages
  project named `ahb123` (created once, manually or via API).
- Token: a **scoped** Cloudflare API token (Pages:Edit on the account) stored the
  same 0600 way as the existing deSEC/Cloudflare tokens
  (`dashboard/network.db` `provider_tokens`, or a sibling 0600 env file —
  implementation picks one and documents it). Never logged, never printed.
- stdlib `urllib` for the API calls (local-first; no `wrangler`/npm dependency).
  If Direct Upload's multi-step hashing proves impractical in pure stdlib, the
  documented fallback is `npx wrangler pages deploy dist/` — but the default
  target is stdlib.
- Prints the resulting `*.pages.dev` deployment URL.

### templates/base.html assembly

`<head>` includes, in order:
1. `<meta charset>`, viewport, `{{title}}`, `{{description}}`.
2. Per-page OG/Twitter tags from `meta.json` (`{{og_title}}`, `{{og_image}}`, …).
3. The static block from `sq_bundle/code-injection/header.html`: `nova-base`
   meta, geo meta, fonts preconnect, LocalBusiness + FAQPage JSON-LD (verbatim).
4. `<link rel="stylesheet" href="/assets/css/brand.css">` — the shared brand CSS
   extracted from the header/pages (the pages currently carry heavy inline
   styles; shared rules move to `brand.css`, page-specific inline styles stay in
   the body fragments unchanged to guarantee visual parity).
5. Google Fonts stylesheet (Montserrat + body font, matching current design).

End of `<body>`:
1. `<nav>` — a hand-authored nav matching the Squarespace nav (logo → `/`, links
   to Services/Portfolio/About/Contact, CTA → `/plan`). Squarespace generated
   this; it is not in the bundle, so it is authored from the visible current site
   during implementation.
2. `<footer>` — brand footer (contact info, service areas, copyright).
3. Nova widget `<script src="https://nova.ahb123.com/widget.js?v=1" defer>`.
4. GA4 block — **only if** Serge supplies a real Measurement ID; otherwise the
   GA block is omitted entirely (no placeholder `GA_MEASUREMENT_ID` shipped to
   production). See Open Items.

## Cutover Sequence (nothing goes dark)

1. **Build + preview.** Run `build.py` → `deploy.py`. Site goes live at
   `ahb123.pages.dev` (public but unlinked; real, testable).
2. **Verify on preview** (see Testing). Squarespace still serves the real
   domain — zero public impact.
3. **Custom-domain cutover.** In Cloudflare Pages, add custom domains
   `ahb123.com` and `www.ahb123.com`. Cloudflare replaces the apex A records /
   `www` CNAME (currently Squarespace) with Pages targets automatically.
   - **Prerequisite:** the nameservers must already be on Cloudflare — i.e. the
     in-flight tunnel migration's Phase 3–4 complete and the zone "Active."
     Steps 1–2 do **not** need this and can proceed immediately in parallel.
4. **Verify on the real domain** `https://ahb123.com` (repeat Testing checklist).
5. **Grace period.** Leave Squarespace **paid and untouched** for a few days as
   an instant fallback.
6. **Cancel Squarespace** once Serge is satisfied. After cancel, the
   Squarespace-specific `_domainconnect` CNAME can be removed from DNS.

### Rollback

Before step 6, rollback = repoint apex + www back to Squarespace's IPs from the
zone export:
```
ahb123.com  A  198.49.23.144
ahb123.com  A  198.185.159.144
ahb123.com  A  198.185.159.145
ahb123.com  A  198.49.23.145
www         CNAME  ext-sq.squarespace.com
```
Squarespace content is untouched during the grace period, so reverting DNS fully
restores the old site within DNS-propagation time.

## Testing

`build.py` and its helpers are unit-tested; the site is verified end-to-end on
the preview URL before any DNS change.

**Unit (pytest, `tests/` in the framework):**
- `build.py` produces the correct clean-URL file tree (`index.html` +
  `X/index.html` per page) from a fixture content set.
- Each output page contains: the page's `meta.json` title + description in
  `<head>`, the LocalBusiness JSON-LD block, the `nova-base` meta, the Nova
  widget `<script>`, and the page body fragment.
- All 48 portfolio `/s/*.jpg` references have a corresponding file copied into
  `dist/s/`. (Fails if any referenced image is missing.)
- No `GA_MEASUREMENT_ID` placeholder appears in any `dist/` file.
- Rebuild idempotency: two clean builds yield identical `_manifest.txt`.
- `sitemap.xml` lists exactly the 6 canonical URLs, absolute `https://ahb123.com`.

**Manual, on `ahb123.pages.dev` preview (checklist in README):**
- All 6 pages render with correct brand styling on desktop + mobile.
- All 48 portfolio images load.
- Nova chat widget opens and returns a reply.
- `/plan` multi-step form submits → writes an `ahb_clients` row
  (`source=plan_page`) → Rex Telegram alert fires.
- Internal links (`/services`, `/portfolio`, `/plan`, `/about`, `/contact`) all
  resolve.
- View-source: JSON-LD present and valid (Google Rich Results test).

## Open Items (resolve during implementation, before cutover)

1. **`logo.svg` + `og-homepage.jpg`** — referenced by the header at `/s/` but not
   in the bundle. Locate from Squarespace assets / brand kit, or regenerate
   (Sam), and place in `assets/s/`. Blocks JSON-LD logo + social share preview.
2. **Nav + footer markup** — Squarespace-generated, not in the bundle. Author to
   match the current live site during implementation (single source in
   `base.html`).
3. **GA4 Measurement ID** — placeholder today. Decision: ship with a real ID if
   Serge provides one, else omit GA entirely (revisit in Sub-project B). No
   placeholder ships.
4. **Cloudflare Pages token scope** — create a token scoped to `Pages:Edit` on
   the account (not the broad zone token). Store 0600.

## Dependencies / Ordering

- Steps 1–2 (build + preview) — **no dependency**, can start now.
- Step 3 (custom-domain cutover) — depends on the Cloudflare **nameserver
  migration** (separate in-flight project) reaching "zone Active."
- Sub-project B (editor) — depends on this spec's `content/` + `build.py` +
  `deploy.py` seam. Separate spec.

## Related

- `project_ahb123_squarespace` (memory) — the original paste-bundle + Nova split.
- `project_nova_caddy_dynamic_ip` (memory) — nova self-host, deSEC delegation,
  DDNS.
- `project_cloudflare_tunnel_domain` (memory) + `~/Desktop/ahb123-cloudflare-tunnel-plan.md`
  — the nameserver migration this cutover depends on.
