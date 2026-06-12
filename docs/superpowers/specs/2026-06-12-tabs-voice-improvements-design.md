# Social / Media / Email tabs + Voice improvements — design

Date: 2026-06-12. Autonomous session (Serge away). Request: "improve social, media, and email tab, also improve voice with new features" + mid-session add: "fix multi-account Gmail — hit a bunch of roadblocks with Google."

## 1. Email tab (priority — explicit user ask)

**Root cause of the Google roadblocks:** `/api/email2/accounts/add/start` (email_studio.py:1132) uses `InstalledAppFlow.run_local_server` on a *random localhost port on baza*. Google redirects the user's browser to `http://localhost:<port>/` — which only resolves to baza if the browser runs on baza. From any remote browser (Tailscale, Mac, phone) the redirect dies and the flow hangs as "pending" forever. Secondary: the GCP OAuth app is in Testing mode, so non-test-user Gmail accounts get `Error 403: access_denied`.

**Fix:**
- Replace the random-port local-server flow with `google_auth_oauthlib.flow.Flow` + fixed redirect `http://localhost:8888/api/email2/oauth/callback`.
  - Same-machine / SSH-tunnel browsing: callback route completes the flow automatically (matched by `state`).
  - Remote browsing: the browser shows "can't connect to localhost:8888" — the user copies the full redirect URL from the address bar and pastes it into the modal; new `POST /api/email2/accounts/add/finish {flow_id, redirect_url}` extracts `code` and finishes.
- Detect `access_denied` and surface the actionable hint: add the address as a Test User in Google Cloud Console → OAuth consent screen, or publish the app.
- Tag cached emails with `account_id` on insert (api_thread, api_sync) so caches don't cross-pollute; legacy NULL rows remain visible.
- **Attachments (new):** thread reader lists attachments per message (filename, size); `GET /api/email2/attachment/<msg_id>/<att_id>` streams the bytes from Gmail.
- Frontend: rework the Add-account modal for the two-path flow (auto poll + paste-back), attachment chips in the reader.

## 2. Social tab

Two real gaps found (everything else is feature-rich already):
- `baza-social-autopilot.timer`/`.service` exist in the repo root but were never installed — autopilot literally never ticks. **Install + enable the timer.**
- Scheduled posts (`status='scheduled'`, `scheduled_at`) have **no publisher** — nothing sends them when due. **Extend `/api/ahb/social/autopilot/tick`** to also publish due scheduled posts via the existing `_send_post_to_telegram`, marking them `posted` (or `failed` with error in ai_meta). Tick response reports `published_scheduled` count.

## 3. Media tab (datahub.html)

Contained UX upgrades, no schema changes:
- Lightbox keyboard navigation: ←/→ moves through the current filtered grid, Esc closes; prev/next buttons in the modal.
- Caption + tags from `image_captions` shown in the lightbox (new cheap endpoint `GET /api/artifacts/caption?project_id&sub_path`).
- Sort control on the media tab: newest / oldest / name / size.
- Transcode cache for `/api/cloud/media/play` (app.py): cache HEVC→H.264 outputs under `<pool>/.transcode-cache/` keyed by path+mtime hash, so repeat plays don't re-transcode.

## 4. Voice (Fluid, vision worktree :8889)

New features on the existing orb/particles + SSE + TTS stack (dashboard/fluid_routes.py, static/fluid/*):
- **Wake word ("summon by voice"):** continuous lightweight recognition while idle; saying "hey baza" (or an agent name) starts a listening session — reuses existing VAD + STT path. Toggle in UI, off by default (mic etiquette).
- **Live captions / transcript drawer:** running transcript of user + agent turns with timestamps; "save transcript" posts the session to the journal/session log path already used by fluid db. Toggleable panel; survives within session.

Out of scope (YAGNI this round): platform publishing APIs (FB/IG), email scheduled-send, media map view, multi-user voice rooms.

## Verification
- email: round-trip add-account flow endpoints with curl (start → poll, finish with bogus code → clean error), attachments listing on a real thread.
- social: tick endpoint dry-run; timer installed + `list-timers` shows it.
- media/voice: Jinja parse + `node --check` on edited script blocks; dashboard restart; endpoint smoke tests.
- Restart `baza-dashboard.service` (template cache) and verify vision dev server for fluid changes.
