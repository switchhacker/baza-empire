# Mobile PWA = the full desktop Baza UI — Design

**Date:** 2026-06-16
**Author:** Claude (with Serge)
**Status:** Approved design — pending implementation plan

## Problem

The installable mobile PWA at `/mobile` renders `templates/mobile.html` — a separate,
hand-built native-app-style shell (4 bottom tabs: Home / Work / Chat / More) that exposes
only a curated subset of the dashboard and hard-disables zoom
(`viewport ... maximum-scale=1, user-scalable=no`).

Serge wants the mobile PWA to be the **entire desktop Baza dashboard** — "nothing missing,
everything the same as desktop, one webpage like the one on my desktop" — and to be able to
**pinch-zoom in and out**.

## Key facts discovered

- The desktop UI is a **multi-page Flask app**: `index.html` (Agents home) + `ahb123.html`
  (business app) + `datahub`, `chains`, `projects`, `cloud`, `edge`, `infra`, `comms`,
  `vision`, `settings`, etc. There is **no base template** (`{% extends %}` is unused);
  every page has its own standalone `<head>`.
- **All 22 nav-bearing desktop templates include one shared partial: `_nav.html`**
  (rendered in `<body>`, with a `<script>` block at the bottom). This is the single
  injection point that reaches the whole dashboard.
- **Desktop pages already permit pinch-zoom** — they use
  `viewport content="width=device-width, initial-scale=1.0"` (no `user-scalable=no`).
  Only `mobile.html` (and `review_public.html`, `portal.html`) disable zoom.
- The PWA plumbing (`<link rel="manifest">`, service-worker registration) currently lives
  **only** in `mobile.html`. Desktop pages have none, so on their own they are not
  installable/standalone.
- Routes: `/mobile` (app.py:5417), `/mobile/manifest.json` (app.py:5425),
  `/mobile/sw.js` + `/sw.js` (app.py:5444). The SW caches an app shell, network-first for
  HTML/APIs.

## Decisions (from brainstorming)

1. **Scope:** `/mobile` shows the *entire* desktop dashboard, identical to desktop.
2. **Zoom:** native pinch-to-zoom (no custom +/- buttons).
3. **Layout on phone:** *true desktop, scaled down* — a fixed ~1280px-wide viewport on
   mobile only, so the page renders exactly like desktop and is shrunk to fit, then
   pinch-zoomed. Desktop-browser viewing must stay unchanged.

## Design

### 1. Repoint the PWA at the real dashboard
- Change `mobile_page()` (app.py:5417) so **`/mobile` 302-redirects to `/`** (desktop home,
  full `_nav.html`). All desktop routes are within the PWA `/` scope, so navigation stays
  inside the installed app.
- Update `/mobile/manifest.json` (app.py:5425): `start_url` → `/`; keep
  `display: standalone`, name, icons, `theme_color`, `background_color`.
- **Preserve `mobile.html`**: keep the file on disk and keep it reachable at a new
  `/mobile-classic` route. Nothing is deleted — trivial rollback.

### 2. Make every desktop page installable, standalone, and desktop-scaled — one file
Add a single `<script>` at the bottom of **`_nav.html`** (already included by all 22 pages).
On load it:
- Injects into `document.head` (idempotent — guard against double-insert):
  `<link rel="manifest" href="/mobile/manifest.json">`,
  `apple-mobile-web-app-capable=yes`,
  `apple-mobile-web-app-status-bar-style=black-translucent`,
  `apple-mobile-web-app-title=Baza`,
  `theme-color=#07070f`,
  `<link rel="apple-touch-icon" href="/static/img/ahb_logo.jpeg">`.
- Registers the existing service worker: `navigator.serviceWorker.register('/sw.js', {scope:'/'})`.
- **Mobile-only viewport rewrite:** if the screen is small (e.g.
  `matchMedia('(max-width: 1024px)')` or coarse pointer), rewrite the page's
  `<meta name=viewport>` to `width=1280, initial-scale=<innerWidth/1280>, user-scalable=yes,
  viewport-fit=cover`. On desktop, leave the existing `width=device-width` meta untouched.
  This is what makes the phone show a true-desktop layout scaled to fit, still pinch-zoomable.

One edit propagates installability + standalone + desktop-scaling to the whole dashboard.

### 3. Stragglers (optional, follow-up)
`cloud.html`, `shell.html`, `agent.html` do **not** include `_nav.html`. They still work as
normal pages; if Serge wants them standalone/desktop-scaled too, add the same snippet (or a
shared `_pwa.html` partial) to their heads. Out of scope for the first pass unless requested.

## What we are NOT doing (YAGNI)
- No responsive redesign of desktop pages.
- No custom zoom buttons.
- No deletion of `mobile.html` or its routes.
- No editing of all 28 viewport metas (the `_nav.html` JS handles viewport at runtime).

## Testing / verification
- Restart `baza-dashboard.service` (debug=False → Jinja template cache; restart required).
- `curl -sI /mobile` → `302` to `/`.
- `curl -s /` (and `/ahb123`, `/datahub`, `/edge`) → page source shows injected manifest link
  + SW registration after JS, or confirm via a headless browser that `document.head` has the
  manifest link and `navigator.serviceWorker` is registered.
- `curl -s /mobile/manifest.json` → `start_url` is `/`.
- `/mobile-classic` → still renders the old `mobile.html`.
- Manual device check: install to a phone home screen → opens standalone into the full
  dashboard, renders desktop layout scaled to fit, pinch-zoom in/out works, nav routes load.
- Desktop browser unchanged: viewport stays `width=device-width`; layout identical to before.

## Risks
- iOS standalone PWAs require the apple-mobile-web-app metas on each navigated page to stay
  out of Safari — covered because `_nav.html` injects them everywhere.
- Runtime viewport rewrite must run before first paint feels jarring; acceptable (brief
  reflow) and only on mobile. If objectionable, move the viewport rewrite to an inline head
  snippet later.
- `theme`/data-theme cookie on desktop pages is unaffected.
