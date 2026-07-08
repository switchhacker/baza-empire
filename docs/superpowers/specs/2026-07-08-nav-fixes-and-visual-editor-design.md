# Nav Fixes + Baza Visual Page Editor — Design

**Date:** 2026-07-08
**Status:** Approved by Serge (chat), pending spec review
**Scope:** Two deliverables. Part A: dashboard nav/subtab fixes (ships first). Part B: a visual click-to-edit page editor covering all Baza dashboard pages and the ahb123.com public site.

---

## Part A — Nav & subtab fixes

### A1. AHB123 sub-tab reorder + QuickRF promotion

New top-row order in `dashboard/templates/ahb123.html` (`.sub-nav`):

> **Email, Projects, QuickRF**, Dashboard, Clients, Treasury, Heavy Equipment, Calendar, Sticky Pad, Voice, Chat Dept, Media, Social, Reviews, Leads, Web

- QuickRF (`data-tab="receipts"`) is **promoted to a top-level sub-tab** while remaining a child of the Treasury group (`TAB_GROUPS.treasury.children` unchanged).
- `switchTab()` highlight logic must be adjusted: today `_parentGroupOf('receipts')` → `treasury` and the Treasury super-tab gets highlighted. New rule: **if a top-level `.sub-tab[data-tab=<leaf>]` exists, highlight it directly and hide the leaf-nav row**; otherwise fall back to the parent-group behavior. This keeps Treasury→QuickRF working *and* makes the promoted tab feel first-class.
- Default landing tab when opening `/ahb123` stays **Dashboard** (Email first in the row is a priority-order statement, not a landing change).

### A2. Single source of truth for AHB123 subtabs (fixes the drift)

The bug reported ("some subtabs don't show up under ahb123 like email") is drift between two hand-maintained lists:

- `_nav.html:52-71` AHB123 dropdown — stale: missing Email, Web, Social, Leads, Heavy Equipment, Treasury, Sticky Pad; still lists merged-away InvoiceIT/Billing.
- `ahb123.html:882-896` sub-tab bar — current.

**Fix:** new include `dashboard/templates/_ahb_tabs.html` defining one Jinja list:

```jinja
{% set AHB_TABS = [
  ('email',     '📧', 'Email'),
  ('projects',  '🏗️', 'Projects'),
  ('receipts',  '🧾', 'QuickRF'),
  ('dashboard', '📊', 'Dashboard'),
  ...
] %}
```

- `_nav.html` imports it and renders the dropdown as `/ahb123?tab=<key>` links.
- `ahb123.html` imports it and renders the `.sub-tab` row from the same list.
- Leaf tabs inside groups (Treasury/Heavy Eq/Projects children) stay defined in `TAB_GROUPS` in `ahb123.html`; the dropdown shows top-level tabs only (dropdown height is already at its limit).
- Route note: `/ahb123/<tab>` deep links already exist (`app.py:5876`) and `switchTab` handles `?tab=`; dropdown links use `/ahb123?tab=<key>` which the existing deep-link init consumes.

### A3. Main-nav additions: Email + Web

In `_nav.html` main row:

- **📧 Email** → `/email` (full Email Studio page), `nav_active` key `email` (already reserved in the key list).
- **🌐 Web** → `/web` (new page, Part B home), new key `web`.
- Placement: Email after AHB123, Web after Email. Both plain links (no dropdown).

### A4. Banner shrink

`.nav-brand` currently costs ~180px. Changes (host pages define `.nav-brand`; add canonical override in `_nav.html`'s style block so it applies everywhere):

- `h1` font-size down (~18px → 13px), tighter letter-spacing, reduced gap/padding.
- Below `1400px` viewport: collapse to just the ⚡ glyph (title attribute keeps the name discoverable).
- Net effect: room for the two new main tabs without wrapping.

### A5. Verification (Part A)

- pytest: template renders include Email/Web links; `_ahb_tabs.html` list drives both surfaces (parse rendered HTML for the dropdown and sub-tab bar and assert same key set — a regression test that kills the drift class permanently).
- Manual: `sudo systemctl restart baza-dashboard` (required — Jinja template cache), click through Email/Projects/QuickRF/Treasury→QuickRF, phone-width check (nav-submenu fixed-position mobile mode).

---

## Part B — Visual Page Editor ("Web")

### B0. Concept

One editor, two backends:

| Target | What an edit is | Persistence | Publish step |
|---|---|---|---|
| Baza dashboard pages (any tab at :8888 / baza.ahb123.com) | **Override** applied over live DOM | `ui_overrides.db` (SQLite) | none — instant |
| ahb123.com (static site) | **Source edit** to `web/ahb123/content/*.html` / assets | git working tree (auto-git hourly) | Draft rebuild → **Publish** → CF Pages deploy |

baza.ahb123.com *is* the dashboard (CF tunnel), so it's covered by the overrides path automatically.

### B1. Edit Mode on every dashboard page

- `static/edit.js` + `static/edit.css`, loaded `defer` from `_nav.html` (already included by every page — single injection point). Cache-busted with `?v=` (service worker `/sw.js` caches statics).
- ✏️ toggle button in `.nav-right`. Off by default; state in `sessionStorage`.
- **Edit mode on:**
  - Hover: outline highlight + tag/label tooltip on the hovered element.
  - Click: select element → inspector panel (right-side dock, draggable) opens.
  - Click-through suppression: in edit mode, clicks are captured before page handlers (capture-phase listener + `preventDefault`) so selecting a button doesn't fire it. Esc or toggle exits.
- **Inspector capabilities ("a lot of options"):**
  - **Text**: inline `contenteditable` editing of the selected element's text.
  - **Image**: swap `src` — upload (→ `dashboard/static/uploads/`, served path stored) or pick from Data Hub (reuse existing `_bin_picker.html` / media-picker pattern).
  - **Style**: color, background, font family/size/weight, padding, margin, border, radius, shadow, width/height, opacity, alignment — grouped controls writing a per-element style override.
  - **Visibility**: hide/show (hidden elements listed in panel so they can be found again).
  - **Rename**: shorthand for text edit on tabs/labels/buttons.
  - **Link**: edit `href` on anchors.
  - **Reorder**: drag selected element among its siblings (same parent only); stored as an `order` override on the parent (ordered list of child keys). Covers reordering tabs, cards, nav links.
  - **Per-change undo**, **page history list**, **Reset element**, **Reset page**.
- **Selector strategy** (robustness over cleverness): prefer `#id`, then `[data-tab=…]`, then shortest unique CSS path with `nth-of-type`. Each override also stores a fingerprint `{tag, trimmed-text-prefix, class-list}`; on apply, if the selector matches nothing (template changed underneath), the override is marked **stale** in the history UI instead of silently vanishing or mis-applying.
- **Dynamic content**: overrides re-applied via a `MutationObserver` (debounced) so JS-rendered tabs/cards (e.g. leaf-nav, kanban) get their overrides after render. Style overrides applied as an injected `<style>` sheet (survives re-render without observer work); text/order/image applied on match.
- **FOUC**: accepted — overrides fetch + apply on `DOMContentLoaded`; typical delta is small.

### B2. Overrides storage + API

New `dashboard/ui_overrides.db` (separate file; keeps `baza_projects.db` schema clean), WAL mode:

```sql
CREATE TABLE overrides (
  id INTEGER PRIMARY KEY,
  page TEXT NOT NULL,            -- path key, e.g. '/ahb123' (query stripped)
  selector TEXT NOT NULL,
  kind TEXT NOT NULL,            -- text|image|style|hide|link|order|attr
  value TEXT NOT NULL,           -- JSON payload per kind
  fingerprint TEXT,              -- JSON {tag,text,classes}
  active INTEGER DEFAULT 1,      -- 0 = reverted (kept for history)
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_overrides_page ON overrides(page, active);
```

Flask API (new blueprint `dashboard/ui_editor.py`, registered in `app.py`):

- `GET  /api/ui/overrides?page=` → active overrides for page
- `POST /api/ui/overrides` → upsert (same page+selector+kind updates in place)
- `POST /api/ui/overrides/<id>/revert` → set inactive
- `POST /api/ui/overrides/reset?page=` → deactivate all for page
- `GET  /api/ui/overrides/history?page=` → all incl. inactive/stale
- `POST /api/ui/upload` → image upload (extension allow-list, size cap, random name under `static/uploads/`)

Auth posture: same as the rest of the dashboard (LAN/Tailscale/CF Access, single user). No separate auth layer.

### B3. `/web` — the editor home (new main tab)

New route `/web` → `templates/web.html` (`nav_active='web'`):

- **Site cards**: ahb123.com (deploy state, last publish, draft-dirty flag), baza.ahb123.com + dashboard (tunnel/service status — reuse `/api/ahb/web/status` bits), nova.ahb123.com (status only, not editable in v1).
- **Baza Dash section**: page list (from a curated route list) → "Open in Edit Mode" (navigates with `?edit=1`, which auto-enables the toggle); per-page override counts + history/revert UI.
- **ahb123.com section**: page picker (home/services/portfolio/about/contact/plan) → embedded **preview iframe** with the editor injected (B4); Draft banner + **Publish** button + build/deploy log tail; edit history (git log of `web/ahb123/`).
- The existing AHB123→Web subtab keeps its status cards and links out to `/web` for editing ("Open editor" button); no duplicate editor embedded there.

### B4. ahb123.com click-to-edit pipeline

- **Preview build**: `build.py` gains a `--preview` mode → builds to `web/ahb123/.preview/` and **stamps `data-edit-id`** on editable nodes. Stamping happens by parsing each `content/<page>.html` fragment (stdlib `html.parser`) and assigning stable ids `<page>:<node-path>` to text-bearing elements and `<img>`s. Published builds (`dist/`) are never stamped.
- **Serving**: `GET /web/preview/ahb123/<path>` serves `.preview/` files (path-traversal guarded, subtree-locked).
- **Editing**: the `/web` iframe loads the preview; `edit.js` runs in a mode where the save target is source, not overrides:
  - Text edit → `POST /api/web/ahb123/edit {edit_id, html}` → server locates the node in `content/<page>.html` via the same parser/path → writes the fragment → re-runs preview build → iframe refreshes.
  - Image swap → upload lands in `web/ahb123/assets/s/` (slugged name) → `src` rewritten in the fragment.
  - `meta.json` (title/description) editable via a small form in the page picker (not click-on-page).
  - Style edits on the public site v1: **per-element inline style only** (written into the fragment). Site-wide brand.css editing is out of scope v1.
  - Reorder: sibling reorder within a fragment, same drag UX, server reorders nodes in the fragment.
- **Draft/Publish**:
  - Every source edit = draft (working tree). Preview always reflects draft. Auto-git commits hourly (existing `claw-auto-git`) — that's the history/rollback story, surfaced in the `/web` history panel (`git log --oneline -- web/ahb123`).
  - **Publish** = `build.py` (real build → `dist/`) + `deploy.py` (wrangler → CF Pages), run as a background job with status polling (`/api/web/ahb123/publish`, `GET .../publish/status`). Errors surfaced verbatim (deploy.py already raises with stderr).
  - Guard: Publish disabled while a build/deploy is in flight.

### B5. Failure modes & guards

- Source-edit endpoints locked to `web/ahb123/{content,assets}` subtree; reject `..`, symlinks, non-allow-listed extensions.
- Fragment write is atomic (tmp + rename); parse-failure → 422, file untouched.
- Overrides apply defensively: bad selector/JSON → skipped + logged, never breaks page load.
- Editor JS is inert when edit mode is off except the small apply-overrides pass.
- `edit.js` excluded from Claw fs-watcher noise concerns (normal review applies; nothing special).
- Dashboard restart NOT needed for override changes (DB + static JS); template changes during implementation DO need `systemctl restart baza-dashboard`.

### B6. Testing

- pytest (follow existing dashboard test patterns in `tests/`):
  - overrides CRUD, upsert semantics, revert/reset, history, page-key normalization
  - upload guards (extension/size), path-traversal attempts on preview + edit endpoints
  - fragment editor: edit_id→node resolution, text/image/reorder rewrites, atomicity on parse failure
  - preview build stamping: ids stable across rebuilds, absent from `dist/`
  - publish endpoint with injected fake runner (pattern already used by `test_ahb123_deploy.py`)
  - Part A template-consistency test (dropdown == sub-tab bar keys)
- Manual: full click-through of inspector on `/ahb123`, `/datahub`; edit + publish round-trip on ahb123.com preview against staging (`*.pages.dev`).

### B7. Delivery phases

1. **A** — nav fixes (all of Part A). Small, ships first.
2. **B-i** — overrides core: DB + API + `edit.js` (select, text, image, style, hide, link) + `/web` page skeleton with Baza Dash history/revert.
3. **B-ii** — reorder drag + rename affordances + stale-detection + reset/undo polish.
4. **B-iii** — ahb123.com: preview build + stamping, source-edit endpoints, iframe editor wiring, Draft/Publish.

Each phase lands independently usable.

## Explicit non-goals (v1)

- No structural re-architecting of dashboard pages (moving a form into a different tab) — overrides are cosmetic/arrangement.
- No editing of nova.ahb123.com chat UI.
- No site-wide CSS/theme editor for ahb123.com (per-element only).
- No new-section/new-page creation on ahb123.com (edit existing pages; page creation stays a dev task).
- No multi-user/permissions model — single operator (Serge).
