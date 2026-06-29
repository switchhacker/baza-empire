# Profile Link Directory (Track C) Design

**Date:** 2026-06-29
**Status:** Approved in brainstorming, pending spec review
**Scope:** An editable directory of AHB123's public profile URLs (LinkedIn, YouTube, Instagram, Facebook, TikTok, Thumbtack, HomeAdvisor/Angi, Google Business, website, …). Edited in the dashboard (Social tab), rendered clickably in the dashboard and on the public `/review` page.

This is **Track C** of a 3-track effort (A = LinkedIn publishing — shipped; B = Thumbtack/Angi lead+review intake — shipped). The small capstone.

---

## 1. What this is (and is not)

A public-facing "Find us on" directory: one editable list of `{platform, label, url}` that customers click to reach (and review) AHB123 across platforms, and that Serge uses as quick-launch links to manage those profiles.

It is **not** the same as Track A's `social_connections` (OAuth *publishing* credentials for YouTube/Meta/TikTok/LinkedIn). HomeAdvisor/Thumbtack/Google-Business have no OAuth connection at all — only a public profile URL. So this is a separate, simpler dataset. Pure CRUD: no LLM, no cloud, no network. Local-first by construction.

## 2. Grounding facts (verified 2026-06-29)

- `ahb_business_profile` is tax/legal only (no URL fields) — not extended here.
- The dashboard already serves a public, no-auth, customer-facing page: `/review` → `templates/review_public.html` (`app.py:14350`), reached via QR. This is the public surface for the links block.
- Existing blueprints are registered in `dashboard/app.py` near `app.register_blueprint(_social_bp)` (~line 15908-15915). `lead_bp` (Track B) was added there too.
- The Social tab + its connect module live in `templates/ahb123.html` (`SocialStudio.modules.connect`); icons for platforms already exist there.

## 3. Architecture

### 3.1 Data — new table `ahb_profile_links`
```
ahb_profile_links(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,        -- machine key: 'linkedin','thumbtack','homeadvisor','google',...
  label TEXT,                    -- display name, e.g. 'HomeAdvisor'
  url TEXT NOT NULL,
  icon TEXT,                     -- emoji/icon string for display
  display_order INTEGER DEFAULT 100,
  visible INTEGER DEFAULT 1,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
)
```
Created idempotently in `_ensure_tables`. Integer PK (this is a brand-new table we own; no UUID-PK convention to match here).

### 3.2 Backend — new module `dashboard/profile_links.py` (`profile_bp`)
Mirrors the `_db()` / `_ensure_tables()` pattern of the sibling modules.
- `GET /api/ahb/profile-links` — admin list, all rows ordered by `display_order, id`.
- `POST /api/ahb/profile-links` — create a link. Body `{platform, label, url, icon?, display_order?, visible?}`. `platform` + `url` required; `url` lightly validated (must start with `http://` or `https://`; if missing scheme, prepend `https://`). Returns the created row.
- `PUT /api/ahb/profile-links/<int:id>` — update whitelisted fields (`platform,label,url,icon,display_order,visible`); same URL normalization. 404 if missing.
- `DELETE /api/ahb/profile-links/<int:id>` — remove. 404 if missing.
- `GET /api/ahb/profile-links/public` — **visible=1 only**, ordered; returns `{items:[{platform,label,url,icon}]}` (no internal fields). Consumed by the public `/review` page, the dashboard display, and any external embed.
- Registered in `app.py` next to `_social_bp`/`lead_bp`, calling `_ensure_tables()` at import.

### 3.3 Frontend
- **Dashboard editor — "Profiles & Links" section in the Social tab** (`templates/ahb123.html`). A new `SocialStudio.modules.links` module (mirroring `modules.connect`): renders the current links as rows with a **launch** button (opens the URL in a new tab), an edit (label/url/visible/order) control, and a delete; plus an "Add link" form with a **platform preset picker** (LinkedIn 💼, YouTube ▶️, Instagram 📸, Facebook 👥, TikTok 🎵, Thumbtack 🛠️, HomeAdvisor/Angi 🏠, Google Business ⭐, Website 🌐) that prefills `platform`/`label`/`icon`. All add/edit modals are body-level (dashboard modal rule). After save, restart of `baza-dashboard` is required for template changes (build-time), but data edits are live via the API.
- **Public block — `templates/review_public.html`**: a "Find us on" row that fetches `/api/ahb/profile-links/public` on load and renders icon+label links (`target="_blank" rel="noopener"`). The whole block is hidden when the list is empty. Escapes all values.

## 4. Error handling

- POST/PUT with missing `platform`/`url` → 400 with a clear message. Bad/relative URL → normalized to `https://…` (never stored as a javascript: or other scheme — reject non-http(s) schemes with 400 to avoid an XSS link target).
- Public endpoint and public-page fetch are best-effort: a failure leaves the `/review` page's existing review form unaffected (the block simply doesn't render).
- DELETE/PUT on a missing id → 404.

## 5. Testing — `tests/test_profile_links.py`

Isolated tmp DB + Flask app with `profile_bp` registered (mirror `tests/test_social_connect.py` fixture style). No network.
- create → returns row with normalized url (scheme prepended when omitted); reject non-http(s) scheme with 400; reject missing url/platform with 400.
- list returns all ordered by `display_order,id`.
- update changes whitelisted fields; PUT unknown id → 404.
- delete removes row; DELETE unknown id → 404.
- `public` returns only `visible=1` rows, ordered, exposing only `{platform,label,url,icon}` (no `id`/`updated_at`/`visible`).

## 6. Decisions (override on spec review)

- Editor lives as a **section in the Social tab** (not its own tab). Confirmed.
- Public surface is the **`/review` page** for now; the `/public` JSON endpoint makes an ahb123.com/Squarespace embed trivial later (out of scope now). Confirmed.
- New `ahb_profile_links` table (not reusing `social_connections` or `ahb_business_profile`). Confirmed.

## 7. Constraints honored

- Local-first: pure local CRUD, no LLM/cloud/network.
- Dashboard modal rule: editor modals are body-level.
- Template cache: restart `baza-dashboard` after editing `ahb123.html` / `review_public.html`.
- Auto-git: spec committed by the hourly `claw-auto-git` timer, not manually.
- XSS: public endpoint stores only http(s) URLs (non-http(s) schemes rejected); the public page and dashboard escape all rendered values.
