# Design: Email Attachments/Preview + Universal Share

**Date:** 2026-06-29
**Author:** Claude + Serge
**Status:** Approved for planning
**Scope:** Baza dashboard (`baza-empire/agent-framework-v3/dashboard/`)

## Goal

Two related improvements, built in order (A then B, they share plumbing):

- **A.** Email tab gains real attachment support: upload files when composing/replying, and click-to-preview inbound attachments inline.
- **B.** A single reusable **Share sheet** (Link / Email / Telegram) backed by one backend service, dropped into Data Hub, Projects, and Cloud — so any non-private file can be shared by link, email, or Telegram from anywhere.

## Current State (verified)

- **Email** — `templates/email.html` (3-pane) + `email_studio.py` (full Gmail read/compose/AI/multi-account).
  - Inbound attachments: listed as download chips (`renderAttachments`, `email.html:782`), **no preview**. Download via `api_attachment` (`email_studio.py:716`), `Content-Disposition: attachment`.
  - Outbound attachments: `_resolve_attachments()` (`email_studio.py:800`) + `_mime_message()` (`email_studio.py:835`) exist but only handle **internal refs** (`invoice_pdf`/`quote_pdf`/`estimate_pdf`/`artifact`). 25 MB total cap (`_MAX_ATTACH_BYTES`, `email_studio.py:35`). **No file-upload UI** in the compose modal (`email.html:496-536`).
- **Reusable preview UI** — `templates/artifacts.html:890-910` previews images/PDF/video/audio/text; served inline via `app.py:2753` (`/api/artifacts/serve/...`).
- **Sharing is fragmented:**
  - **Cloud** — tokenized public links (`/api/cloud/files/share` `app.py:14094`; `cloud_shares` table; public handler `/s/<token>` `app.py:14277`) + direct Telegram (`/api/cloud/files/telegram` `app.py:14154`, Phil's bot, file-type → sendPhoto/Video/Audio/Document). **No email.**
  - **Data Hub** — browser-native `navigator.share()` only (`datahub.html:917-990`); vault endpoints `app.py:1407-1594`. No links/email/telegram.
  - **Projects** — no share UI; artifacts reachable only via internal routes.
- **Telegram core** — `_notify_agent()` (`app.py:2894`), token lookup `_agent_telegram_token()` (`app.py:2857`); Cloud telegram uses `TELEGRAM_PHIL_HASS`/`CLOUD_TELEGRAM_BOT` → `SERGE_CHAT_ID`.
- **Email-as-service** — `_mime_message`/`api_send` (`email_studio.py:835/872`) using the active account from `email_accounts`.

## Part A — Email tab: attachments & preview

### A1. Outbound upload (compose + reply)

- **Frontend (`email.html`):** add a file picker + removable attachment-chip list (filename + size + ✕) to the compose modal and the reply composer. Selected files upload immediately to a staging endpoint; the returned tokens are passed in the existing `attachments` array on send.
- **Staging endpoint (new):** `POST /api/email2/attachments/upload` (multipart/form-data). Saves to `email-pipeline/.outbox_uploads/<uuid>/<sanitized_name>`. Returns `{token, filename, size, mime}`. Per-file and total size sanity caps.
- **Resolve (`_resolve_attachments`, `email_studio.py:800`):** add ref `{type:'upload', token, filename}` → read staged file, enforce the existing 25 MB total cap across all refs.
- **Cleanup:** on successful `api_send`, delete the staged files for the used tokens. A TTL sweep (e.g. > 6 h old) removes orphaned `.outbox_uploads/*` on startup / periodically.

### A2. Inbound preview

- **Inline serve:** add `?inline=1` to `api_attachment()` (`email_studio.py:716`) → `Content-Disposition: inline` + correct `Content-Type`. Default (no param) stays `attachment` (download).
- **Preview modal (`email.html`):** body-level modal reusing the `artifacts.html` viewer markup/CSS. Routing by extension/mime:
  - image → `<img src=...?inline=1>`; pdf → `<iframe>`; video/audio → `<video>/<audio>`; text → fetch + `<pre>`; else → "Download" button.
  - Modal always shows a Download button.
- **Chips:** clickable → `openAttachmentPreview(msgId, attId, name, mime)`.

## Part B — Universal Share

### B1. Backend: `dashboard/share_service.py` (new)

- **`resolve_source(source, id) -> abs_path`** — safe path resolution per source:
  - `cloud` — path under the cloud root (`/mnt/empirepool/cloud/1/...`).
  - `artifact` — `{project_id, filename}` under `dashboard/artifacts/<project_id>/`.
  - `datahub` — a Data Hub file path.
  - Reuses existing realpath/allow-root guards. **Rejects `.private-inbound/` and `.vault_meta/`** (honors the privacy hard-rule). An allow-list of root prefixes bounds every resolution.
- **Channel handlers:**
  - `link(abs_path, expires_days)` — mint a token in `cloud_shares` for any allowed path; returns `{token, url, expires_at}`.
  - `email(abs_path, to, subject, note)` — attach the file via `_mime_message` + active Gmail account; **if size > 25 MB, auto-create a link and embed it in the body instead** of attaching.
  - `telegram(abs_path, chat_id)` — reuse Cloud's Phil-bot send (file-type → sendPhoto/Video/Audio/Document); `chat_id` defaults to Serge (`SERGE_CHAT_ID`), optional override.
- **Dispatch route:** `POST /api/share` `{source, id, channel, ...channel_args}` → returns channel result. Registered as a blueprint in `app.py`.
- **`/s/<token>` (`app.py:14277`)** generalized to serve the token's stored absolute path, bounded by the same allow-list (so non-cloud paths resolve). Cloud's existing endpoints remain functional.

### B2. Frontend: one share sheet

- **`templates/_share_sheet.html` (new):** a single **body-level** modal (per the "modals must be body-level" rule — never nested in a `#tab-*`). Three sub-tabs:
  - **Link** — Create link, expiry selector, copy-to-clipboard, list/revoke existing.
  - **Email** — To, Subject (prefilled `Shared: <filename>`), Note textarea, Send (active Gmail account).
  - **Telegram** — optional chat-id field (default = me), Send.
- **`openShareSheet({source, id, filename})`** — JS helper any tab calls; posts to `/api/share`.

### B3. Wiring

- **Data Hub (`datahub.html`)** — Share action per-file + in select mode → `openShareSheet({source:'datahub', id:<path>, filename})`. Replaces the bare `navigator.share()`.
- **Projects (`projects.html` / `artifacts.html`)** — Share button on project files/artifacts → `source:'artifact'`.
- **Cloud (`cloud.html`)** — point the existing Share button at the unified sheet (`source:'cloud'`) so it gains the Email channel; existing link/telegram endpoints back it.

## Data Flow

```
Share button (any tab)
  → openShareSheet({source, id, filename})
  → Share sheet modal (Link | Email | Telegram)
  → POST /api/share {source, id, channel, ...}
  → resolve_source (guarded, private-dir reject)
  → channel handler (link mints token / email attaches-or-links / telegram sends)
  → result (URL for link; ok for email/telegram)

Email compose upload:
  file picker → POST /api/email2/attachments/upload → {token}
  → send includes {type:'upload', token} → _resolve_attachments reads staged file
  → on success, staged files deleted
```

## Error Handling

- Upload: reject oversize / disallowed; clear error in compose UI.
- Resolve: path outside allow-list or in a private dir → 403, surfaced in the share sheet.
- Email: > 25 MB → silent fallback to link (message notes "link included because file exceeds 25 MB"); Gmail send failure → error toast.
- Telegram: missing bot token / bad chat_id → error toast.
- Link: token creation failure → error; revoke is idempotent.

## Testing (TDD)

Pure/unit-testable, network mocked:

- `resolve_source`: accepts allowed roots; rejects path-traversal, `.private-inbound/`, `.vault_meta/`, out-of-root paths.
- `link`: token row created with expiry; `/s/<token>` serves the stored path; revoke removes it.
- `email`: attach path for ≤ 25 MB; link-fallback path for > 25 MB (assert body contains link, no attachment).
- `telegram`: correct API method chosen per file type; chat_id default vs override.
- Email upload: `_resolve_attachments` reads `{type:'upload'}`; total-size cap enforced across mixed refs; cleanup deletes staged files after send.

Mock the Gmail/Telegram send layer and filesystem roots; follow the dashboard's existing test conventions/location.

## Files

**New**
- `dashboard/share_service.py` — resolver + channel handlers + `/api/share` blueprint.
- `dashboard/templates/_share_sheet.html` — body-level share modal + share JS.
- Tests (existing dashboard test location).

**Modified**
- `dashboard/email_studio.py` — `POST /api/email2/attachments/upload`; `_resolve_attachments` upload ref; `api_attachment` `?inline=1`; staged-file cleanup + TTL sweep.
- `dashboard/templates/email.html` — compose/reply file picker + chips; inbound preview modal + clickable chips.
- `dashboard/templates/datahub.html` — share action → `openShareSheet`; include share sheet.
- `dashboard/templates/projects.html` / `artifacts.html` — share button → `openShareSheet`.
- `dashboard/templates/cloud.html` — route Share to unified sheet; include share sheet.
- `dashboard/app.py` — register `share_service` blueprint; generalize `/s/<token>` to allow-listed roots.

## Constraints / Notes

- **Local-first:** no new cloud APIs; email/telegram use existing local Gmail-OAuth + Telegram bot paths.
- **Privacy hard-rule:** `.private-inbound/` and `.vault_meta/` are never shareable.
- **Template caching:** dashboard runs `debug=False` → `sudo systemctl restart baza-dashboard` after any `templates/*.html` edit.
- **Auto-git:** `claw-auto-git` commits `agent-framework-v3` hourly — don't manually commit unless time-sensitive.
- **Include partial once at body level** in whichever base layout the tabs share (verify during planning whether a shared base exists or the partial must be included per-template).

## Out of Scope (YAGNI)

- Saved contacts/recipient picker for Telegram/email (use chat-id override + To field).
- Sharing inbound email attachments via the universal sheet (later if wanted).
- Per-send attach-vs-link toggle (auto fallback only).
- Share analytics beyond the existing `cloud_shares.access_count`.
