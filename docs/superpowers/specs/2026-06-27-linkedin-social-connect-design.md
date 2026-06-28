# LinkedIn — Social Connect (Track A) Design

**Date:** 2026-06-27
**Status:** Approved in brainstorming, pending spec review
**Scope:** Add LinkedIn as a first-class publishing platform in the existing Social
Studio "Connections" framework (`dashboard/social_connect.py` + Social tab in
`templates/ahb123.html`). Member (personal profile) **and** Organization (Company
Page) posting.

This is **Track A** of a 3-track effort. Track B (Thumbtack + Angi lead/review
intake) and Track C (profile-link directory) are separate specs, built after A
ships. Build order: A → B → C.

---

## 1. Why LinkedIn fits the social publisher (and Thumbtack/Angi do not)

LinkedIn is a publishing channel: connect an account, push a post, read a feed —
the same shape as YouTube / Meta / TikTok. It slots into `social_connect.py` with
the established pattern (connect → publish-dispatch → feed, all network calls
isolated into monkeypatchable boundary helpers).

Key advantage over Meta/TikTok: **LinkedIn media upload is a push** — we register
an asset then `PUT` the bytes straight to LinkedIn's upload URL. No
`SOCIAL_PUBLIC_BASE_URL` / public origin is required, so publishing works from
behind baza's firewall.

## 2. LinkedIn API facts this design relies on

- **OAuth 2.0 authorization-code** (3-legged). Authorize:
  `https://www.linkedin.com/oauth/v2/authorization`; token exchange:
  `https://www.linkedin.com/oauth/v2/accessToken`. Plain `requests` — no SDK.
- **Identity:** OpenID `userinfo` (`https://api.linkedin.com/v2/userinfo`, scope
  `openid profile email`) → member `sub` → `urn:li:person:{sub}`.
- **Admined Company Pages:** `organizationAcls?q=roleAssignee&role=ADMINISTRATOR&
  state=APPROVED` → org URNs; `organizations/{id}` for the display name.
- **Posting:** `POST https://api.linkedin.com/rest/posts` with headers
  `LinkedIn-Version: <YYYYMM>` and `X-Restli-Protocol-Version: 2.0.0`. Body:
  `author` (person or organization URN), `commentary`, `visibility: PUBLIC`,
  `lifecycleState: PUBLISHED`, and `content.media.id` = the uploaded asset URN.
- **Media:** `images?action=initializeUpload` / `videos?action=initializeUpload`
  → `{uploadUrl, image|video URN}`; `PUT` bytes to `uploadUrl`; videos need a
  finalize call.
- **Scopes / products (Serge's LinkedIn dev app must add):**
  - *Sign In with LinkedIn using OpenID Connect* → `openid profile email` (self-serve)
  - *Share on LinkedIn* → `w_member_social` (self-serve) — member posting works now
  - *Community Management API* → `w_organization_social` + `r_organization_admin`
    + `r_organization_social` — **requires LinkedIn approval**; Company-Page
    posting and org feed are gated behind it.

> Exact `rest/posts` field names and the `LinkedIn-Version` value must be pinned
> against current LinkedIn docs at implementation time (LinkedIn versions monthly).

## 3. Prerequisite (Serge, one-time)

Create a LinkedIn Developer app (developer.linkedin.com): client id + secret, set
the redirect URI to baza's `OAUTH_REDIRECT_URI`, add the three products above
(request Community Management API; the other two are self-serve). Client id/secret
are entered through the **same "Add app credentials" panel YouTube uses**, stored
at `CREDS_DIR/linkedin.json` (0600). No code change needed when the Community
Management API is later approved — org posting starts working once the token
carries the org scopes.

## 4. Architecture — backend (`dashboard/social_connect.py`)

### 4.1 Platform registration
- Add `"linkedin"` to `PLATFORMS` and `OAUTH_PLATFORMS`.
- `LI_SCOPES_MEMBER = ("openid", "profile", "email", "w_member_social")`.
- `LI_SCOPES_ORG = (... + "w_organization_social", "r_organization_admin",
  "r_organization_social")`. Request the union; LinkedIn grants the subset the
  app is approved for, so member posting still works before org approval.
- Base URLs overridable via env (`LINKEDIN_API_BASE`, `LINKEDIN_OAUTH_BASE`) for
  tests, mirroring `META_GRAPH` / `TIKTOK_API`.

### 4.2 Boundary helpers (isolated, monkeypatched in tests)
- `_li_build_authorize_url(state)` → consent URL.
- `_li_exchange_token(code)` → `{access_token, expires_in, ...}`.
- `_li_userinfo(token)` → `{person_urn, name, email}`.
- `_li_list_orgs(token)` → `[{org_urn, name}]` (empty if Community Mgmt not approved).
- `_li_register_image(token, owner_urn)` / `_li_register_video(token, owner_urn)`
  → `{upload_url, asset_urn}`.
- `_li_put_bytes(upload_url, path)` → uploads the asset bytes.
- `_li_create_post(token, author_urn, commentary, media_urn, is_video)` → `{id, url}`.
- `_li_member_feed(token, person_urn, limit)` / `_li_org_feed(token, org_urn, limit)`.

### 4.3 OAuth flow generalization
`_finish_oauth` is currently YouTube-only (calls `_yt_channel_label`,
hard-codes YT scopes). Generalize: branch on `entry["platform"]`. For
`linkedin`, after `flow`-less token exchange we do **not** auto-create one
connection — we stash the token + discovered targets (member + orgs) in a
short-lived server-side session (mirrors `_meta_sessions`) and return choices to
the UI for selection. YouTube path unchanged.

### 4.4 Routes (mirror existing patterns)
- Reuse `/<platform>/auth/start` + `/auth/finish` (now multi-platform).
  `auth/start` currently calls the YouTube-only `_yt_build_flow()`; generalize it
  to branch on platform — for `linkedin` build the consent URL via
  `_li_build_authorize_url(state)` and stash `{status, platform, state, created}`
  (no Google `flow` object) in `_oauth_flows`. For LinkedIn, `auth/finish`
  exchanges the code via `_li_exchange_token`, discovers member + orgs, and
  returns `{ok, ref, member:{...}, orgs:[...]}` instead of finalizing — like
  `meta/token` returning page choices.
- **New** `POST /api/ahb/social/connections/linkedin/add` — body
  `{ref, target: "member"|org_urn}`; writes the token to
  `_token_path("linkedin", <ref>)` and upserts a `social_connections` row. `meta`
  stores `{person_urn}` or `{org_urn, org_name}`. Parallels `meta/add`.
- **Publish dispatch** in `social_post_publish`: add
  `if platform == "linkedin": body, code = _publish_linkedin(r, post, pid); ...`.
- **Feed** in `social_conn_feed`: `linkedin` → `_li_org_feed` when the connection
  has an `org_urn`; member connections return a clear "personal-profile feed is
  not available via LinkedIn's API" message (502 with friendly text) rather than
  erroring opaquely.

### 4.5 `_publish_linkedin(conn_row, post, pid)` → `(body, code)`
1. Determine `author_urn` from connection meta (`org_urn` or `person_urn`).
2. Pick asset: prefer rendered video (`asset_path`, video ext) else image
   (`cover_path`, or non-video `asset_path`). Error 400 if neither rendered.
3. `register → PUT bytes → create_post` with caption+hashtags as `commentary`.
   **No public URL needed.**
4. On org connection when the token lacks org scopes (Community Mgmt not yet
   approved), LinkedIn returns 403 → translate to a friendly
   "Company Page posting is pending LinkedIn Community Management API approval —
   post to your personal profile, or use Manual export" (same spirit as TikTok's
   SELF_ONLY draft note).
5. `_mark_posted(pid, url)` on success.

## 5. Architecture — frontend (`templates/ahb123.html`, Social tab)

- Add a card to `SocialStudio.modules.connect.PLATFORMS`:
  `{ id:'linkedin', name:'LinkedIn', icon:'💼', connect:'oauth', feed:true,
  note:'Posts to your profile now; Company Page activates once LinkedIn approves
  Community Management API.' }`. The `oauth` branch already renders an "Add app
  credentials" button when unconfigured and "Connect account" when configured.
- `connectOAuth('linkedin')` runs the standard start→consent→paste-back, then —
  because LinkedIn returns choices — opens a small **account-picker modal**
  (member + each admined Page, following the Meta picker), POSTing the chosen
  target to `/linkedin/add`. Body-level modal per the dashboard modal rule.
- Browse/Publish/Disconnect buttons reuse the existing per-account row controls.

## 6. Testing — `tests/test_social_linkedin.py`

Mirror `tests/test_social_connect.py` conventions (env fixture redirects
`ACCOUNTS_DIR`/`CREDS_DIR`/DB to tmp; every `_li_*` boundary monkeypatched; no
network, no creds). Cases:
- `auth/start` returns a LinkedIn consent URL.
- `auth/finish` returns member + org choices (no connection yet).
- `/linkedin/add` creates a **member** connection (person_urn in meta).
- `/linkedin/add` creates an **org** connection (org_urn in meta).
- publish to member with an **image** asset → success, marks posted.
- publish to org with a **video** asset → success.
- publish without confirm → 400; publish with no rendered asset → 400.
- org publish when token lacks org scopes (403) → friendly pending-approval body.
- feed: org → items; member → friendly "not available" message.
- disconnect removes row + token file.

## 7. Decisions / defaults (override on spec review)

- **Default post target = Company Page**, selectable per post; member is the
  immediately-working fallback. (AHB123 brand reach is the goal.)
- **Connect = OAuth paste-back** (not pasted-token) — cleaner, reuses the YouTube
  pattern, and is the only sane way to get a LinkedIn token + discover orgs.
- v1 publishes **one image OR one video** per post (matches the other platforms).

## 8. Out of scope (YAGNI for v1)

Scheduling, analytics, comments/reactions, multi-image carousels, document/article
posts, member-feed reading (LinkedIn-restricted). These can be follow-ups.

## 9. Constraints honored

- **Local-first rule:** social posting is inherently a cloud/outbound action; this
  sits in the existing tolerated social-publish path alongside YT/Meta/TikTok. No
  new local-replaceable logic is sent to the cloud.
- **Outward-facing confirm:** publish keeps the existing `confirm:true` gate.
- **Modal rule:** the account-picker is a body-level modal.
- **Auto-git:** this spec is committed by the hourly `claw-auto-git` timer — not
  manually (per repo CLAUDE.md), unless Serge wants it pushed sooner.
