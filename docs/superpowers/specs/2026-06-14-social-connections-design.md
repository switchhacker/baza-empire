# Social Connections + Direct Posting + Feed Browser — Design

Date: 2026-06-14
Status: Phase 1 in progress

## Goal
Let Serge connect social accounts and post composed content directly from the
Social tab, plus browse each connected account's recent content in-page.
Platforms: Instagram, Facebook Page, YouTube Shorts, TikTok.

## Hard constraints
- Direct posting needs cloud APIs — an explicit, user-requested exception to the
  local-first rule (like the existing Telegram/Gmail/yt-dlp paths).
- Posting is outward-facing → publish endpoints require an explicit `confirm`.
- Tokens are sensitive → stored on disk (perms 600) under `social-pipeline/`,
  never in the `/cloud` UI, never returned by list endpoints.
- Each platform is gated by a developer app **Serge** registers; the build must
  degrade gracefully (clear "needs credentials" state) and never hard-code secrets.

## Architecture
New blueprint module `dashboard/social_connect.py`, registered on `social_bp`
alongside the other `social_*` submodules.

### Storage
- `social_connections` table in `baza_projects.db`:
  `id, platform, account_label, account_ref, status, scopes, connected_at, meta(JSON)`.
- Tokens: `social-pipeline/accounts/<platform>/<account_ref>/token.json` (600).
- App client secrets: `social-pipeline/credentials/<platform>.json` (600);
  Google falls back to `email-pipeline/credentials.json` if a YT secret is absent.

### Endpoints (all under `/api/ahb/social`)
- `GET  /connections` — list rows (no tokens).
- `GET  /connections/app-creds` — per-platform booleans (configured?), no secrets.
- `PUT  /connections/app-creds` — store a platform's OAuth client JSON.
- `POST /connections/<platform>/auth/start` — YouTube: Google Flow → `{flow_id, auth_url, redirect_uri}`.
- `POST /connections/<platform>/auth/finish` — `{flow_id, redirect_url}` → exchange, store token, create row.
- `GET  /connections/oauth/callback` — loopback landing (browser-on-baza path).
- `DELETE /connections/<id>` — remove row + token file.
- `GET  /connections/<id>/feed` — recent items for the account (YT: channel uploads).
- `POST /posts/<pid>/publish` — `{connection_id, confirm:true}` → platform publish; records `posted_url`, status.
- `GET  /posts/<pid>/manual-export` — universal fallback: caption+hashtags+first_comment + asset/bundle/cover download links.

### Google/YouTube specifics
- Scopes: `youtube.upload`, `youtube.readonly`.
- Redirect: `http://localhost:8888/api/ahb/social/connections/oauth/callback`.
- Publish = upload the post's rendered **video** asset as a Short (requires a
  rendered video asset; otherwise 400 "render the post first").
- Feed = `youtube.search/playlistItems` recent uploads.

## Frontend (Social tab)
- New **Connect** sub-section: a card per platform.
  - YouTube: Connect (OAuth paste-back modal mirroring the Gmail flow), connected
    accounts list, Disconnect, Browse feed.
  - Instagram / Facebook / TikTok: "Phase 2/3" state + **Manual export** button now.
- Library posts gain a **🚀 Publish** action: pick a connected account → confirm →
  publish; plus **Manual export** (copy caption, download asset) for any platform.

## Phasing
1. **Phase 1 (this build):** connections framework + storage + app-creds panel +
   YouTube end-to-end (OAuth/publish/feed) + universal manual-export fallback.
2. **Phase 2:** Meta (Instagram + Facebook Page) publish + feed.
3. **Phase 3:** TikTok publish + browse.

## Testing
Backend endpoints unit-tested with the Google layer mocked: connections CRUD,
app-creds set/get, OAuth start/finish (mock Flow), manual-export, publish
validation + confirm gate, feed (mock API).
