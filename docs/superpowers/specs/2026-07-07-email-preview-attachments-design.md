# Email Studio — Full Preview, Navigation Fix, Attachments Everywhere

**Date:** 2026-07-07 · **Approved by:** Serge (in chat) · **Scope:** `dashboard/email_studio.py`, `dashboard/templates/email.html`, tests

## Problem

1. **Preview panel doesn't display the full email.** `_decode_body()` strips HTML emails to plain text; formatted mail (newsletters, receipts, vendor quotes) loses layout, images, and sometimes content. Earlier messages in a thread are collapsed.
2. **Simple actions are hidden.** Reply/forward/star/archive live in a toolbar that scrolls away; the AI strip crowds it. Worse, at 700–1100px viewport width, opening a thread adds `.show-reader` → `grid-template-columns:1fr` while the sidebar stays visible, so the reader wraps to a second grid row *underneath the mailboxes* and the Back button is off-screen — no way back to the mailbox.
3. **Attachments are invisible while browsing.** They only appear inside an opened thread; nothing in the list pane, no cross-mailbox view, no attachments from nested/forwarded messages (`message/rfc822`) or inline `cid:` parts, and no attachments from agent scaffold runs.
4. **Attachment actions are incomplete.** Only download + save-to-project/cloud exist. Serge wants: **share (Telegram / link / email), save (project / Baza cloud / Desktop), download, forward**.

## Design

### 1. Full email rendering
- **Backend:** keep the raw `text/html` MIME part. New endpoint `GET /api/email2/message/<gmail_id>/html?account=` returns sanitized HTML:
  - sanitizer (stdlib, no new deps): remove `<script>/<object>/<embed>/<iframe>/<form>`, all `on*` attributes, `javascript:`/`data:text` URLs; keep styles/tables/images.
  - rewrite `cid:` image URLs to `/api/email2/attachment/<msg_id>/<att_id>?inline=1` using the message's content-id map.
  - served with `Content-Security-Policy: script-src 'none'` belt-and-suspenders.
- **Frontend:** render in `<iframe sandbox="allow-same-origin">` (scripts blocked, height auto-sized from `contentDocument.scrollHeight` — whole email visible, no inner scrollbar). Per-message **HTML ⇄ Plain** toggle; plain path unchanged as fallback. **Expand all / collapse all** control for threads.

### 2. Navigation + always-visible actions
- Fix `@media(max-width:1100px)`: `.mail-shell.show-reader{grid-template-columns:200px 1fr}` (sidebar stays beside reader; <700px keeps single-pane with sidebar hidden). Back button becomes prominent (visible whenever the list pane is hidden).
- Reader toolbar becomes `position:sticky; top:0` inside the reader; AI strip collapsible (persisted in localStorage).
- Per-message quick actions (reply / forward / star) on each message card header.

### 3. Attachments while browsing
- **Cache:** add `has_attachments INTEGER` and `attachments_json TEXT` columns to `emails` (idempotent ALTER like existing extra columns); populated in `_hydrate_thread` and sync paths.
- **Collector:** `_collect_attachments` recurses into `message/rfc822` parts and records inline (`Content-ID`) parts with an `inline` flag (listed, and used for cid rewriting).
- **List pane:** 📎 badge with count on `.thread-item`; click expands an in-list chip row with the full action bar — no need to open the email.
- **Attachments view:** new sidebar entry "📎 Attachments" → `GET /api/email2/attachments/browse?account=&type=&q=&limit=&offset=` over the cached `attachments_json` (all mailboxes/accounts). Second tab **Agent files**: `GET /api/email2/attachments/agent-files` listing `dashboard/artifacts/**` (scaffold/agent outputs), **excluding `.private-inbound/`** (hard privacy rule) and `.vault_meta/`.

### 4. Unified attachment action bar
Same bar on: message chips, list-pane popover, Attachments view, preview modal.
- **Preview** — existing modal.
- **Download** — existing endpoint (device download).
- **Save** — existing save modal gains a **Desktop** destination (`/home/switchhacker/Desktop/Email-Attachments/`) alongside Project files and Baza cloud.
- **Share** — new `POST /api/email2/attachment/share` `{msg_id, att_id, name, mime, via: telegram|link|email, to?, note?}`; materializes the attachment to a temp/cloud path then reuses `share_service` (`share_telegram`, `create_link` 7-day, `share_email`). Link result is copied to clipboard + shown.
- **Forward** — new `POST /api/email2/attachments/restage` `{msg_id, att_id}` (or `{path}` for agent files) copies bytes into the send outbox staging (existing token system, 25MB cap) and opens compose with the chip pre-attached.

## Error handling
- HTML endpoint 404s → frontend falls back to plain text silently.
- Restage/share respect the 25MB cap and existing path guards (no traversal outside artifacts/cloud roots; agent-files endpoint canonicalizes and rejects paths escaping `artifacts/`).
- Attachments browse works purely off the local cache (no Gmail API calls) — empty until threads have been synced/opened; note shown in UI.

## Testing
TDD, pytest, following `tests/test_email_attachments.py` / `dashboard/tests/test_email_unified.py` patterns:
- sanitizer (script/on*/javascript: stripped, cid rewritten), rfc822 + inline recursion, attachments_json backfill on hydrate, browse endpoint filters + privacy exclusion, restage roundtrip + size cap, share via link (mock share_service), Desktop save destination.
- Manual: `sudo systemctl restart baza-dashboard` after template edits (Jinja cache), verify at >1100px, 900px, and phone width.

## Out of scope
- Background full-mailbox attachment indexing (browse view relies on synced cache).
- Thumbnails service; HTML email dark-mode transformation.
