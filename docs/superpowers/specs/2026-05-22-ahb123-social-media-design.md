# ahb123 — Social Media Studio (TikTok + Instagram)

**Status:** Design approved 2026-05-22
**Owner:** Serge
**Surface:** new sub-tab in `dashboard/templates/ahb123.html` (`#tab-social`)
**Backend:** new module `dashboard/social_studio.py` mounted from `dashboard/app.py`
**DB:** new tables in `dashboard/baza_projects.db`
**Scope phase 1:** draft + render + organize + auto-pilot drafting for TikTok and Instagram (Reel / Feed / Story). Direct API publishing is **Phase 2**.

---

## 1. Goals

Build a content-studio tab inside ahb123 that turns Serge's existing work-photo library, projects, and reviews into ready-to-post TikTok and Instagram content. Local-first AI, multi-platform variants from a single source, presets, optional auto-pilot.

### Non-goals (Phase 1)

- Direct posting via TikTok Content Posting API or Meta Graph API. (Requires Business accounts + OAuth + app review. Phase 2.)
- LinkedIn, YouTube Shorts, X, Threads, Facebook. (Easy to add later; same data model.)
- Built-in music licensing or Spotify/Apple Music integration.
- Comment / DM management.

### Success criteria

1. Serge selects a project + phase, hits one button, gets four platform-correct renders + captions in under 90 seconds (without SD image generation).
2. Auto-Pilot, when enabled, produces drafts on schedule that land in Library awaiting one-tap approval.
3. All AI work uses local Ollama models by default; cloud is opt-in per setting.
4. Render output is genuinely paste-ready: a human can copy the caption file + open the .mp4 in the IG app and post in under 30 seconds.

---

## 2. Where it lives

### Sub-tab placement

`ahb123.html` sub-nav, inserted between Media (`tab-photos`) and Reviews (`tab-reviews`):

```
📊 Dashboard · 👥 Clients · 🏗️ Projects · 🚜 Heavy Eq · 🏦 Treasury · 📅 Calendar ·
📌 Sticky · 🎙️ Voice · 💬 Chat · 🎥 Media · 📣 Social · ⭐ Reviews
```

- New nav item: `<div class="sub-tab" data-tab="social" onclick="switchTab('social')"><span class="sub-tab-icon">📣</span> Social</div>`
- New pane: `<div class="tab-pane" id="tab-social">…</div>`
- **All modals declared at body level** (per the existing hard rule — `tab-photos`-style nesting causes invisibility from other tabs).

### Sub-sub-tabs inside `#tab-social`

| Tab | id | Purpose |
| --- | --- | --- |
| Composer | `social-composer` | Build a post from selected sources |
| Library | `social-library` | Filter/manage drafts, approved, scheduled, posted |
| Scheduler | `social-scheduler` | Calendar view of scheduled posts |
| Presets | `social-presets` | Recipes editor |
| Auto-Pilot | `social-autopilot` | Master toggles, preset cadences, telemetry |

Default landing tab: Composer.

---

## 3. Composer (the heart)

Three-column workspace.

### 3.1 Left column — Source Picker (320px)

Reuses existing Media tab data sources:
- Project + Phase + Date range + Type filters (photo/video, same controls as `#tab-photos`)
- Grid of thumbnails from `image_captions` joined with `project_media`
- Multi-select with checkboxes; selected thumbnails appear in a re-orderable "Shot List" rail at the bottom
- Additional source actions:
  - **Upload** — same uploader as Media tab (`uploadMediaFiles`)
  - **Baza pick** — existing `pickMediaFromBaza()` flow
  - **AI image (SD)** — opens generate modal that calls `sam_imaging`; gated on SD service status
  - **Stock pick** — opens a small curated free-stock browser (Pixabay/Pexels link-out; copy local on use)

### 3.2 Center column — Live Preview (flex)

- Phone-shell `<div>` with `aspect-ratio` CSS bound to the active platform tab
- Platform tabs at the top: TikTok 9:16 · IG Reel 9:16 · IG Feed 1:1 · IG Feed 4:5 · IG Story 9:16
- Inside the phone shell: `<canvas>` for stills (with text overlay) or `<video>` for clips; auto-resizes on tab switch
- Bottom strip: clip timeline (thumbnail per clip), drag to reorder, click to set per-clip duration
- "Hook" text input layered over the top 1/3 of the preview (overlay text shown in mockup; baked-in at render)

### 3.3 Right column — Platform Variants (340px)

One card per selected platform variant. Each card contains:

- **Caption** textarea (per platform; what works on TikTok ≠ IG)
- **Hashtags** chip input (with per-platform character/count limits enforced visually)
- **First comment** textarea (Instagram convention: post hashtags in first comment to keep caption clean)
- **Tone** dropdown: Hype · Pro · Casual · Educational · Trade · Funny
- **Length** dropdown: Short · Medium · Long
- **Style** dropdown: Trade · Lifestyle · Behind-the-scenes · Tutorial · Showcase
- AI actions row:
  - `✨ Generate caption` — calls `/api/ahb/social/ai/caption`
  - `# Suggest hashtags` — calls `/api/ahb/social/ai/hashtags`
  - `🪝 3× Hook ideas` — returns three options, click to swap into Hook overlay
  - `🧪 A/B variations` — fills two duplicate variant cards with the same media, different copy
  - `🎯 Score & critique` — runs critique model, returns 0–100 + 1-paragraph notes
  - `🌐 Translate` — Spanish (Serge's local market)
  - `🔊 Voiceover` — opens TTS modal
  - `🎵 Music suggest` — local curated catalog
- **Apply to all** toggle near the top of the column copies one card's caption/hashtags to peers
- **Render** button at the bottom: kicks the job

### 3.4 Settings drawer (slide-in from right edge)

- **Default model for copy:** `gpt-oss:20b` (primary), `gemma3:12b` (fast mode), `gpt-oss:120b-cloud` (cloud opt-in, off by default)
- **Vision model:** `qwen3-vl:latest` (fixed)
- **TTS engine:** piper (auto-detect; install instructions if missing) / edge-tts (cloud, opt-in)
- **SD service:** show systemctl status + Start/Stop button (`baza-sd-webui.service`)
- **Brand kit:** logo upload, primary/secondary hex colors, font (default + 3 web-safe alts), intro clip, outro clip
- **Defaults seeded from sq_bundle** (HIC# / founding date / brand colors) if `social_brand_kit.json` does not exist yet; user can override

---

## 4. AI capabilities matrix

| Capability | Local primary | Local secondary | Cloud opt-in |
| --- | --- | --- | --- |
| Caption / copy / hooks | `gpt-oss:20b` | `gemma3:12b` (fast) | `gpt-oss:120b-cloud` |
| Critique / scoring | same as caption model | — | same |
| Vision tag / cover-pick | `qwen3-vl:latest` | `llava:13b` | — |
| Translate | `gemma3:12b` | `qwen2.5:14b` | — |
| Image generation | `sam_imaging` → SD WebUI Forge | — | — |
| Voiceover TTS | piper | — | edge-tts |
| Auto-subtitles | whisper.cpp | — | — |

**HARD RULE:** No outside APIs may be called without an explicit per-feature opt-in toggle. Toggles default off. Toggles are stored per-user in dashboard settings, not in the preset, so cloud usage never sneaks into auto-pilot.

---

## 5. Presets engine

Each preset = a JSON recipe that the Composer can load with one click and that Auto-Pilot uses as a generator template.

### Preset record fields

```
id, name, description, platform_targets (json array),
prompt_template (Jinja-style with {{project}} {{phase}} {{count}} placeholders),
hashtag_pool (json array of candidate tags),
tone, length, style,
music_style (one of: hype, calm, trade-driving, none),
voiceover_style (one of: pro-male, pro-female, none, custom),
source_filter (json: project_ids, phases, date_range_days, media_types),
cadence (off | daily | n_per_week | on_trigger),
n_per_week (int, used when cadence=n_per_week),
max_per_day (int),
auto_approve (bool, default false),
score_threshold (int 0-100, default 75),
last_run_at, next_run_at,
active (bool),
created_at, updated_at
```

### Seed presets shipped at install

1. **Project Showcase** — best 6–10 photos from one project, 1:1 carousel + 9:16 Reel, pro tone
2. **Before / After Reel** — first-phase vs final-phase clips, 15s, hype tone, split-screen
3. **Heavy Equipment Spotlight** — single video, gear specs overlay, educational, 30s
4. **Process Explainer** — 30–60s how-we-do-it, educational + voiceover
5. **Customer Testimonial** — Reel + Feed pair, quote pulled from Reviews
6. **Day-in-the-Life** — montage from one day's media, casual, music-led
7. **Quick Tip** — single still + bold text overlay, 5–10 word hook
8. **Sub / Trade Shout-out** — tag a sub w/ photo of their work, casual

Users can clone any seed preset, edit, save as new. Deleting a seed is allowed but the system retains a hidden reference so an "Reset to defaults" admin action can restore them.

### Preset UI (sub-sub-tab `social-presets`)

- Two-pane: list of presets on left, editor on right
- Each row shows: name, platform pills, cadence, status (active/paused), last-run, next-run
- Editor includes a "Test run now" button that runs the preset against the current sources and lands a draft in Library; nothing is auto-approved from a test run

---

## 6. Auto-Pilot

### Cron + lifecycle

- New systemd user units in `agent-framework-v3/`:
  - `baza-social-autopilot.service` — oneshot, calls `POST http://127.0.0.1:8888/api/ahb/social/autopilot/tick`
  - `baza-social-autopilot.timer` — hourly trigger (`OnCalendar=hourly`)
- Tick logic (`dashboard/social_studio.py:autopilot_tick`):
  1. Load active presets ordered by `next_run_at`
  2. For each due preset: respect `max_per_day` (count posts created today for this preset), respect global daily cap, respect master kill switch
  3. Query sources via the preset's `source_filter`, excluding media already used by a post within the configurable cool-down window (default 14 days, settings-controlled)
  4. Generate draft → render → score → store with `status='pending_review'` (or `'approved'` if `auto_approve=true` and `score >= score_threshold`)
  5. Send a Telegram card to Serge with inline Approve / Reject / Edit buttons via the existing Specter bridge
  6. Update `last_run_at`, compute `next_run_at` from cadence

### Master kill switch

- One toggle in `social-autopilot` tab: **All auto generation: ON/OFF**
- Persisted as `autopilot_master` in `dashboard/social_settings.json` (see §13)
- When OFF, the cron tick is a no-op (logs only)

### Telemetry panel (top of `social-autopilot`)

- Drafts created today / this week
- Approval rate (approved / pending_review * 100)
- Top-scoring hook of the week
- Per-preset: last 5 outputs as thumbnails with status badges

---

## 7. Ready-to-post output bundle

Render writes to `dashboard/artifacts/social/<YYYY-MM-DD>/<post_id>/`:

- `tiktok.mp4` — 1080×1920, ≤60s, H.264, AAC, yuv420p, +faststart, optional baked subtitles
- `ig_reel.mp4` — 1080×1920, same encoding profile
- `ig_feed.jpg` or `ig_feed.mp4` — 1080×1080 (square) or 1080×1350 (4:5)
- `ig_story.mp4` — 1080×1920
- `caption_tiktok.txt` — caption + hashtag block
- `caption_instagram.txt` — caption + first-comment text appended after a `---` separator
- `cover.jpg` — best-frame cover (qwen3-vl pick)
- `manifest.json` — preset id, source_media_ids, model used, prompts, scores, render params, ffmpeg command, git sha of the render module

### Distribution

- `📥 Download bundle` — zips the directory, returns via Flask `send_file`
- `📲 Send to my phone` — uploads the bundle to Serge's Telegram chat (caption files inline as text messages, .mp4s as documents) via existing Specter bridge
- `🔗 Copy share link` — short URL `/social/share/<post_id>` (token-gated; reuses `share_tokens` table if it exists, else new lightweight `ahb_social_shares` table — to verify during implementation)

---

## 8. Data model (additions to `baza_projects.db`)

```sql
CREATE TABLE IF NOT EXISTS ahb_social_presets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT,
  platform_targets TEXT NOT NULL DEFAULT '["tiktok","ig_reel","ig_feed_square"]',
  prompt_template TEXT,
  hashtag_pool TEXT,
  tone TEXT DEFAULT 'pro',
  length TEXT DEFAULT 'medium',
  style TEXT DEFAULT 'trade',
  music_style TEXT DEFAULT 'none',
  voiceover_style TEXT DEFAULT 'none',
  source_filter TEXT DEFAULT '{}',
  cadence TEXT DEFAULT 'off',
  n_per_week INTEGER DEFAULT 0,
  max_per_day INTEGER DEFAULT 1,
  auto_approve INTEGER DEFAULT 0,
  score_threshold INTEGER DEFAULT 75,
  last_run_at TEXT,
  next_run_at TEXT,
  active INTEGER DEFAULT 1,
  is_seed INTEGER DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ahb_social_posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  preset_id INTEGER REFERENCES ahb_social_presets(id) ON DELETE SET NULL,
  project_id INTEGER,
  source_media_ids TEXT NOT NULL DEFAULT '[]',
  platform TEXT NOT NULL,
  variant TEXT NOT NULL,
  asset_path TEXT,
  cover_path TEXT,
  caption TEXT,
  hashtags TEXT,
  first_comment TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  score INTEGER,
  ai_meta TEXT DEFAULT '{}',
  render_params TEXT DEFAULT '{}',
  scheduled_at TEXT,
  posted_at TEXT,
  posted_url TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_social_posts_status ON ahb_social_posts(status);
CREATE INDEX IF NOT EXISTS idx_social_posts_project ON ahb_social_posts(project_id);
CREATE INDEX IF NOT EXISTS idx_social_posts_scheduled ON ahb_social_posts(scheduled_at);

CREATE TABLE IF NOT EXISTS ahb_social_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER REFERENCES ahb_social_posts(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  input TEXT NOT NULL DEFAULT '{}',
  output_path TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  error TEXT,
  model_used TEXT,
  tokens INTEGER,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_social_jobs_status ON ahb_social_jobs(status);
```

### Allowed `status` values for `ahb_social_posts`

`draft · pending_review · approved · scheduled · posted · rejected · failed`

### Allowed `platform` values

`tiktok · ig_reel · ig_feed_square · ig_feed_portrait · ig_story`

### Allowed `kind` values for `ahb_social_jobs`

`caption · hashtags · hooks · score · cover_pick · translate · voiceover · image_gen · render · publish`

Migrations are added to the existing `_ensure_docprep_tables()` function in `dashboard/app.py` (or a new sibling `_ensure_social_tables()` called next to it).

---

## 9. API surface

All routes mounted on the existing Flask app under `/api/ahb/social/…`. Backed by `dashboard/social_studio.py` (Blueprint) so it stays out of the ever-growing `app.py`.

```
GET    /api/ahb/social/sources?project_id=&phase=&days=&type=&q=
GET    /api/ahb/social/presets
POST   /api/ahb/social/presets
PUT    /api/ahb/social/presets/<id>
DELETE /api/ahb/social/presets/<id>
POST   /api/ahb/social/presets/<id>/run

GET    /api/ahb/social/posts?status=&platform=&project_id=&q=&limit=&offset=
POST   /api/ahb/social/posts
PATCH  /api/ahb/social/posts/<id>
DELETE /api/ahb/social/posts/<id>
POST   /api/ahb/social/posts/<id>/render
GET    /api/ahb/social/posts/<id>/bundle
POST   /api/ahb/social/posts/<id>/telegram

POST   /api/ahb/social/ai/caption        body: {source_ids, platform, tone, length, style, model?}
POST   /api/ahb/social/ai/hashtags       body: {caption, platform, count?}
POST   /api/ahb/social/ai/hooks          body: {source_ids, n?}
POST   /api/ahb/social/ai/cover-pick     body: {source_ids}
POST   /api/ahb/social/ai/score          body: {caption, hashtags, platform}
POST   /api/ahb/social/ai/translate      body: {text, target_lang}
POST   /api/ahb/social/ai/voiceover      body: {text, voice}
POST   /api/ahb/social/ai/image          body: {prompt, aspect}

GET    /api/ahb/social/jobs/<id>
GET    /api/ahb/social/jobs?post_id=&status=

POST   /api/ahb/social/autopilot/tick
GET    /api/ahb/social/autopilot/status
POST   /api/ahb/social/autopilot/toggle  body: {on: bool}

GET    /api/ahb/social/brand-kit
PUT    /api/ahb/social/brand-kit
```

### Synchronous vs async

- All `ai/*` endpoints are synchronous (LLM calls return in seconds and the UI shows a spinner)
- `posts/<id>/render`:
  - Sync return for stills + ≤30s single-clip jobs
  - Async (returns `{job_id}`) when total source duration > 30s or > 3 clips
- All async jobs use the `ahb_social_jobs` queue; UI polls `GET /api/ahb/social/jobs/<id>` every 1.5s while a job is open

---

## 10. Render pipeline

New module `dashboard/social_render.py`. Pure-Python orchestration of `ffmpeg` and PIL.

### Steps for a video render

1. **Resolve sources** — fetch absolute paths from `image_captions`/`project_media` for each `source_media_id`
2. **Per-clip preprocessing** — for each clip:
   - Probe (`ffprobe`) for duration, resolution, aspect
   - Aspect crop to target (9:16, 1:1, 4:5) with chosen fill mode (blurred-bg / brand-color / letterbox)
   - Per-clip duration trim (default = source length, capped so total ≤ 60s)
3. **Concat** clips via `ffmpeg -f concat -safe 0 -i list.txt`
4. **Music bed** — mix into audio track at -18 LUFS; if voiceover present, sidechain duck by -12 dB
5. **Voiceover** — synthesize via piper to `voice.wav`, mix into audio
6. **Subtitles** — generate via whisper.cpp on the music-free voiceover; render `.srt`; burn-in via `subtitles=` filter (optional, per preset)
7. **Overlay text** — hook text via `drawtext` filter on the top 25% region; brand corner via `overlay=`
8. **Encode** — H.264 baseline-high 4.1, yuv420p, AAC 192k, +faststart, max bitrate 8 Mbps
9. **Cover frame** — `ffmpeg -ss <t> -frames:v 1` at qwen3-vl's recommended timestamp, saved as `cover.jpg`
10. **Write manifest** + return paths

### Fonts

Ship `dashboard/static/fonts/Inter-Bold.ttf` and `Inter-Regular.ttf` (Inter is OFL) so `drawtext` works without depending on system fonts. Brand kit may upload an additional font.

### Aspect crop strategy

Per platform variant:
- `9:16` → smart-crop centered around vision-detected face/subject; fall back to center crop
- `1:1` → center square
- `4:5` → center crop top 4:5 to favor product/work visibility
- Fill mode applies only when source aspect is wider than target (otherwise crop)

---

## 11. Frontend module layout (single file, organized)

All code lives inside `ahb123.html` (consistent with existing tabs).

### Globals

```js
window.SocialStudio = {
  state: { activeSub: 'composer', sources: [], shotList: [],
           platforms: {tiktok:true, ig_reel:true, ig_feed_square:true, ig_feed_portrait:false, ig_story:false},
           variants: {/* keyed by platform: {caption, hashtags, firstComment, tone, length, style, hook} */},
           settings: {/* model, tts, sd_on, brand_kit, autopilot_on */} },
  Composer: { init, switchPlatform, addToShotList, removeFromShotList, render },
  Library:  { init, load, filter, openPost, approve, reject, schedule, delete },
  Scheduler:{ init, loadCalendar, dragMove },
  Presets:  { init, list, edit, save, clone, runOnce, delete },
  AutoPilot:{ init, refreshTelemetry, toggleMaster, togglePreset },
  AI:       { caption, hashtags, hooks, score, translate, voiceover, coverPick, image },
  Render:   { kick, pollJob, downloadBundle, sendTelegram },
};
```

### Body-level modals

- `#socialPresetEditor`
- `#socialBrandKit`
- `#socialImageGen`
- `#socialVoiceover`
- `#socialSettings`
- `#socialPostDetail`

Each modal uses the existing `.modal-bg` / `.modal` pattern from ahb123.html with the same close/escape behavior. Modals must be declared **outside** any `<div class="tab-pane">` (per the existing hard rule about modal ancestor `display:none` invisibility).

### CSS

- New section appended to existing `<style>` block in `ahb123.html`
- Naming prefix `.ss-` (social-studio) to avoid collisions with existing rules
- Phone shell uses `aspect-ratio` + CSS variables for platform switching
- Variant card layout is CSS grid; collapses to single-column under 1200px viewport

### Live preview rendering

- Stills: `<canvas>` with `drawImage` of the selected source, then `fillText` of the hook overlay at the same coordinates the renderer uses (font + size scaled to canvas px)
- Clips: `<video>` element with `object-fit: cover`, source crops simulated via parent overflow + transform
- Switching platform changes the parent `aspect-ratio` CSS var; canvas/video reflow

---

## 12. Brand kit

Persisted at `dashboard/social_brand_kit.json`:

```json
{
  "logo_path": "dashboard/static/social/brand/logo.png",
  "primary_color": "#10b981",
  "secondary_color": "#0e0e1e",
  "font_default": "Inter-Bold",
  "intro_clip_path": null,
  "outro_clip_path": null,
  "hashtag_floor": ["#allhomebuilding", "#ahbco", "#newyorkhomes"],
  "first_comment_floor": "—\nDM for a free estimate.",
  "hic_number": "(read from sq_bundle)",
  "founded_year": "(read from sq_bundle)"
}
```

Bootstrap reads `sq_bundle` for the HIC # and founding year; user can override anything in Settings.

---

## 13. Settings (per-user, persisted)

Stored in `dashboard/social_settings.json` (single-user system; if multi-user appears later, key by user_id):

```json
{
  "default_copy_model": "gpt-oss:20b",
  "fast_copy_model": "gemma3:12b",
  "vision_model": "qwen3-vl:latest",
  "tts_engine": "piper",
  "cloud_models_enabled": false,
  "cloud_copy_model": "gpt-oss:120b-cloud",
  "autopilot_master": false,
  "daily_post_cap": 4,
  "cool_down_days": 14,
  "burn_in_subtitles_default": true
}
```

UI to edit is the Settings drawer in the Composer (gear icon top-right of the tab).

---

## 14. Risks + mitigations

| Risk | Mitigation |
| --- | --- |
| SD service inactive (currently the case) | UI shows status banner with one-click Start button (`systemctl --user start baza-sd-webui.service`); image-gen actions gray out cleanly. Render pipeline works without SD. |
| Whisper.cpp not installed | Subtitle generation falls back to manual entry; install command shown in Settings. |
| Piper not installed | Voiceover button shows install hint; falls back to "no voiceover" silently. |
| ffmpeg version drift | `dashboard/social_render.py` probes `ffmpeg -version` at startup; logs a warning if < 5.0; refuses to start if missing. |
| Auto-pilot Telegram spam | Master kill switch + per-preset cooldown + daily cap + drafts-only default + bundled notifications (one card per cron tick, not one per draft). |
| LLM hallucinating wrong project facts | Caption prompts include the project's stored Scope + Phase + Address as grounded context; system prompt instructs model to use only provided facts. |
| Vision verbose for shorts | Vision is used only for tags + cover-pick; copy is generated by chat model with a tuned "social media writer" system prompt (see prompts/ section below). |
| SD service contention with LLM pool | UI surfaces current GPU pool state from `gpu_pool` health endpoint; warns if SD start will interrupt LLM serving. |
| Music licensing | Curated free-to-use list only (Pixabay Music + YouTube Audio Library link-outs); music files stored under `dashboard/static/social/music/free/` with license metadata sidecar JSON. |
| Local model context overrun on long shot lists | Cap source_media_ids at 12 per generation request; if more selected, sample top-12 by vision score; surface this in UI. |

---

## 15. Prompts (system prompts for AI endpoints)

Stored in `dashboard/prompts/social/` as plain `.md` files so they can be edited without a deploy.

| File | Used by |
| --- | --- |
| `caption_system.md` | `ai/caption` — defines voice, prohibits hallucination, enforces platform char limits |
| `hashtag_system.md` | `ai/hashtags` — outputs JSON array only, mixes niche + broad + branded |
| `hooks_system.md` | `ai/hooks` — outputs 3 hook variants, each ≤ 60 chars |
| `score_system.md` | `ai/score` — outputs JSON `{score: int, notes: str}`; rubric for hook/clarity/CTA/hashtag-fit |
| `cover_vision.md` | `ai/cover-pick` — instructions to qwen3-vl to pick the most arresting frame and explain why |

Each prompt file gets a `version` header; the version + the prompt sha get written into the `manifest.json` of every render so we can reproduce outputs.

---

## 16. Telegram integration

Reuses the existing Specter bridge:

- `POST /api/ahb/social/posts/<id>/telegram` packages the bundle and POSTs to the bridge's `/notify` endpoint with `kind=social_draft`
- The bridge formats a card with caption preview, cover thumb, hashtags, score, and inline buttons: `✅ Approve`, `✏️ Edit`, `❌ Reject`
- Button taps webhook back to `PATCH /api/ahb/social/posts/<id>` with the appropriate status
- "Send to my phone" reuses the same bridge but sends the .mp4 + caption.txt as document attachments

---

## 17. Build order (for the writing-plans skill to pick up)

The implementation plan (next phase) should walk through roughly this sequence; this is a hint, not a contract:

1. DB migrations + Blueprint scaffolding (`dashboard/social_studio.py` mounted in `app.py`)
2. Tab pane scaffold + sub-sub-tab routing in `ahb123.html`
3. Source picker (read-only from existing media data)
4. Composer skeleton + live preview (no AI yet)
5. Caption / hashtag / hooks AI endpoints + UI wiring
6. Render pipeline (stills first, then video)
7. Library + Post detail + status transitions
8. Presets CRUD + sub-tab
9. Brand kit + Settings
10. Scheduler view
11. Auto-Pilot cron + telemetry
12. Telegram drop + bundle download
13. Polish: empty states, error toasts, keyboard shortcuts
14. **Restart `baza-dashboard`** (template cache rule) — bake this into the migration commit message

---

## 18. Out-of-scope explicit list

- Direct API publishing (Phase 2)
- Comment / DM management (Phase 3 if ever)
- Analytics ingest from native platform stats (Phase 2 after publishing)
- Multi-user authorization (single-user system today)
- Cross-account posting (one TikTok account + one IG account assumed)
- LinkedIn / X / Threads / YouTube / Facebook (data model supports it; add later)

---

## 19. Acceptance test plan (for verification-before-completion)

Manual smoke (post-implementation):

1. Open `ahb123` → Social tab loads, lands on Composer
2. Pick a project that has at least 5 work photos + 1 video, multi-select 4 photos + 1 video, see them in Shot List
3. Toggle the four default platform variants on
4. Click `✨ Generate caption` on each platform card — see distinct, platform-fit captions returned in < 10s each (local model)
5. Click `# Suggest hashtags` — get 15–25 hashtags per platform with branded floor included
6. Click `🪝 3× Hook ideas` — get three hook options, click one to load into the overlay
7. Click `🎯 Score & critique` — get a 0–100 score and a paragraph
8. Click `▶️ Render package` — within 90s see all four files in the artifacts dir, plus captions and manifest
9. Open Library — the new post is there in `draft` status
10. PATCH status to `approved`, then `📲 Send to phone` — Telegram message arrives with files
11. Create a new preset, set cadence=daily, auto_approve=false; manually invoke `/autopilot/tick`; one new draft lands in Library with status `pending_review` and a Telegram card arrives
12. Flip master kill switch off; invoke tick again; no new drafts
13. Restart `baza-dashboard.service` — settings, presets, posts all persist
14. Verify modals open from any sub-sub-tab (ancestor visibility check)
15. Verify SD-off path: image-gen button shows banner, render still succeeds for non-SD sources

---

## 20. Open implementation decisions deferred to the plan

The writing-plans skill should resolve these during planning (each is small but I don't want to lock them in design):

- Exact name of the migration function (`_ensure_social_tables` vs adding to `_ensure_docprep_tables`)
- Whether to share `share_tokens` table or add `ahb_social_shares` — needs a 2-minute look at the existing schema
- Specific FFmpeg filter graph for blurred-bg fill (a few candidates; pick by visual test)
- Exact piper voice model choice (one male, one female; the plan should download both)
- Whether the autopilot tick uses Flask request from cron or a Python entry point with its own DB connection (Flask request is simpler; Python entry point avoids spinning up an HTTP client on the same host)

End of design.
