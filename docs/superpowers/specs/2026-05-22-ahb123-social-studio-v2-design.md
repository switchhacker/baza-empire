# ahb123 — Social Studio v2 (mega-expansion)

**Status:** Drafted 2026-05-22 (companion to the Phase 1 spec at `2026-05-22-ahb123-social-media-design.md`)
**Owner:** Serge
**Builds on:** Phase 1 (merged into main as commit `7de5519`)
**Scope:** All non-API expansion (Bundles A + B + C + D + F + G + H + I + J + manual analytics K). Direct API publishing (Bundle E) is **out of scope**, reserved for a future Phase 2 spec.

---

## 1. Goals

Take the working Phase 1 Social tab and turn it into a fully-featured, production-grade content studio: better UX, real media editing, expanded AI tooling, calendar/workflow layer, trend tracking, manual analytics, plus richer source inputs (webcam/screen/URL) — all local-first.

### Success criteria

1. **Polish:** every existing button has a toast notification (no more `alert()`), keyboard shortcuts work, render progress is visible.
2. **Media editing:** can take a single project's media and produce a polished 30-second Reel with auto-subtitles, voiceover, music bed, brand overlay, all from the dashboard.
3. **AI tools:** one click generates 3 hook variants, 3 caption A/B options, a storyboard, a CTA, a B-roll shot list, a multi-language translation set.
4. **Calendar:** drag-to-reschedule across a month view; bulk-approve/reject 10 drafts in one action.
5. **Trends:** can paste a competitor's TikTok URL and get metadata + a suggested hook in the same tone.
6. **Manual analytics:** for every posted item the user can enter views/likes/saves; the dashboard charts performance over time.
7. **New sources:** record from webcam, capture a screen region, or paste a YouTube URL and extract a clip without leaving the dashboard.
8. **Mobile usable:** the composer works on Serge's phone (touch drag, responsive layout).

### Non-goals

- Direct API publishing (TikTok Content / Meta Graph) — **Phase 2**.
- LinkedIn / X / YouTube / Threads / Facebook output channels — **Phase 2 stretch**.
- Built-in social inbox (DMs, comments) — **Phase 3**.
- Multi-tenant team workflows — **single-user system**.
- Cloud GPU video generation — **local-only**.
- Real-time collaboration (multiple cursors) — **single-user**.

---

## 2. Build phasing (suggested for the implementation plan)

Even though the user asked for "everything in one mega-spec," the implementation plan should split the work into three internal phases so we can do code review at sensible checkpoints. Each phase ends with a passing test suite and a green smoke pass on the live dashboard.

| Phase | Bundles | Approx tasks |
|---|---|---|
| **v2.0 — polish & preview** | A (polish) + F (preview) + J (mobile) | ~10 tasks |
| **v2.1 — media & AI** | B (media editing) + C (AI tools) + H (audio) + I (sources) | ~22 tasks |
| **v2.2 — workflow & trends** | D (calendar/workflow) + G (trends) + K (manual analytics) | ~14 tasks |

Total ≈ 46 implementation tasks. The writing-plans skill produces this as one big plan (per the user's request); we'll choose whether to execute as 3 separate plans or one big run when the time comes.

---

## 3. Bundle A — Quick wins & UX polish

### A.1 Real Inter fonts
- Download Inter-Bold.ttf and Inter-Regular.ttf (OFL) from Google Fonts CDN at install time
- Replace the placeholder text-file stubs at `dashboard/static/fonts/Inter-Bold.ttf` and `Inter-Regular.ttf`
- Verify by running an end-to-end `render_still` with `hook_text="Test ✓"` and confirming the JPEG renders without ffmpeg errors

### A.2 Toast notification system
- New small module `SocialStudio.toast` with `info(msg)`, `success(msg)`, `error(msg)`, `progress(msg, jobId)` APIs
- Bottom-right toast stack, max 3 visible, auto-dismiss after 5s (info/success) or 8s (error), progress toasts stick until explicitly resolved
- Replace every `alert()` in the existing composer / library / postdetail / presets / settings / brand-kit / scheduler / autopilot modules
- CSS: dark theme matching the dashboard, accent colors per type (green/red/blue/amber)

### A.3 Keyboard shortcuts
- Module-level keymap registered when `#tab-social` is the active tab
- Shortcuts: `J/K` next/prev in current grid, `A` approve selected (Library only), `R` render current composer post, `/` focus search, `?` show shortcut overlay
- Bypass when focus is in `<input>`/`<textarea>`/`<select>`
- Help overlay: a `?`-triggered modal showing all bindings

### A.4 Render progress polling UI
- Modify `social_render_post` to optionally run async (when total source duration > 30s or > 3 clips, per Phase 1 spec §9)
- New table `ahb_social_render_jobs` (re-use Phase 1's `ahb_social_jobs` actually — kind='render')
- Backend: render endpoint returns either `{ok: true, asset_path, …}` synchronously (small jobs) or `{job_id: 17}` for async
- Frontend: when `job_id` returned, opens a progress toast that polls `/api/ahb/social/jobs/<id>` every 1.5s, shows percent + cancel button
- Cancel sends `DELETE /api/ahb/social/jobs/<id>` which terminates the ffmpeg subprocess and marks status='cancelled'

### A.5 Drag-to-reorder shot list
- The composer's source picker becomes two views: pickable grid (top) + shot-list rail (bottom)
- Items dragged from picker → rail are added; items in rail are draggable to reorder
- Implementation: HTML5 drag/drop + the existing `state.shotList` array; on reorder, `renderPreview()` re-runs
- Rail shows clip number, thumbnail, duration (for video), and a remove button

### A.6 Per-clip trim handles (for video sources)
- Each video item in the shot list opens a small modal on click: simple in/out slider over the video element
- In/out times stored on `state.shotList` as `{id, in_seconds, out_seconds}` objects (existing flat IDs become objects)
- Render pipeline uses per-clip trim values in the ffmpeg concat list via `-ss <in> -to <out>` filter

### A.7 "Render all platforms" one-click
- New button in the composer: "▶️ Render ALL platforms"
- Iterates over checked platforms, creates a post per platform, kicks render for each
- Shows aggregated progress toast: "Rendering 4/5 platforms…"
- Each platform's caption/hashtag block is used independently (the current per-platform state)

### A.8 A/B caption variations
- New button in the variant panel: "🧪 A/B"
- Calls `/api/ahb/social/ai/caption` twice with `temperature=0.9` and `seed` parameters, returns two distinct variants
- UI: modal showing both side-by-side with vote arrows; user picks one, the other is saved as `ai_meta.alt_caption` on the post

### A.9 Translate-in-composer
- New button in the variant panel: "🌐 ES" (and a "+" to add other languages)
- Calls `/api/ahb/social/ai/translate` (already exists from Phase 1) and stores result as `caption_es`, `caption_pt`, etc. on the post in a new `translations` JSON column
- Settings drawer adds a "Translation targets" multi-select for which languages to auto-translate to

---

## 4. Bundle F — Compatibility & preview

### F.1 Device frame mockups
- Composer preview now wraps the existing `#ss-preview-shell` in a selectable device frame: iPhone 15 (notch + dynamic island), Pixel 8 (hole punch), or "no frame"
- Pure CSS — no external assets — using `border-radius` + pseudo-elements for the notch
- Persisted in `localStorage['ss_preview_device']`

### F.2 Platform-native UI overlay preview
- A "Show TikTok overlay" / "Show IG overlay" toggle layers a translucent set of UI elements over the preview:
  - TikTok: right-side action rail (like/comment/share/sound), bottom-left caption area, top hashtag/sound bar
  - IG Reel: similar right rail, audio attribution, "Watch Reel" CTA
  - IG Feed: bottom info bar (likes, caption, hashtags), three-dot menu
- All overlay elements are static CSS (no images); they exist purely to show the user where their content will be obscured
- Toggle visibility per platform — different overlay for each platform tab

### F.3 Safe-area indicators
- A "Show safe zones" toggle draws translucent green/red borders showing:
  - Green: visible safe area (not covered by UI)
  - Red: areas obscured by platform UI (top status bar, bottom navigation, side action rails)
- Helps the user position text overlay (hook) in the safe zone

### F.4 Caption truncation preview
- A "Show caption truncation" indicator shows where TikTok / IG cut the caption (first ~120 chars on TT, first 125 on IG before "…more")
- Renders the caption with a visible "more" cutoff at the right position

### F.5 Cover-image grid preview (IG)
- For ig_feed_square / ig_feed_portrait variants, a side panel shows a 3×3 grid mockup of the user's current feed with the new post inserted top-left
- Other 8 slots are placeholders or pull from Library's most recent 8 posted items
- Helps visual coherence: does this cover match the user's grid aesthetic?

### F.6 Light/dark preview toggle
- Toggle changes the preview shell background — useful for stories which can be shown over light or dark IG themes

### F.7 Device pixel ratio toggle
- Toggle between @1× and @2× preview — shows whether thin lines / small text still legible at retina vs standard density

---

## 5. Bundle J — Mobile & accessibility

### J.1 Mobile-responsive composer
- The current `.ss-grid` collapses to single column < 1200px (already done). v2 makes it actually usable on touch:
  - Source picker becomes a horizontal swipe carousel under 768px
  - Variant panel becomes a swipe-up bottom-sheet under 768px
  - Preview shell stays centered at full viewport width minus 24px padding
- Touch-friendly hit targets: minimum 44×44px for every button (current 12px buttons are too small for touch)

### J.2 Touch drag/drop
- HTML5 drag/drop has poor touch support; use pointer events + manual translation
- Library: standard `PointerEvent` mousedown/move/up tracking with 8px deadzone before considering a drag
- Visual feedback: dragged item gets `opacity: 0.6` and follows the pointer

### J.3 Tooltip help bubbles
- Every button has `title` (native browser tooltip) AND a richer custom tooltip on long-press (touch) / hover-after-1s (desktop)
- Bubbles say what the button does + a 1-line tip ("✨ Caption: generates fresh copy via local Ollama")
- Auto-dismiss on click

### J.4 First-time user tour
- One-time overlay walking through the 5 sub-tabs and the composer's 3 columns
- Triggered when `localStorage['ss_tour_done']` is absent
- Skippable; "Skip tour" sets the flag; "Show me again later" via Settings drawer
- 6-7 steps with arrows pointing at UI elements

### J.5 Empty-state CTAs
- Each sub-tab's empty state currently shows "No posts yet." Replace with action-prompting CTAs:
  - Library empty: "No posts yet. [Pick media in Composer →]" (clicks switches to Composer sub-tab)
  - Scheduler empty: "Nothing scheduled. [Approve some drafts →]"
  - Presets empty: "No presets. [Install 8 seed presets →]" (button already exists; CTA points at it more prominently)
  - Auto-Pilot inactive: "Auto-Pilot is OFF. [Enable in Settings →]"

### J.6 ARIA labels
- Every interactive element gets `aria-label`, dropdowns get `role="combobox" aria-expanded`, modals get `role="dialog" aria-modal="true"`
- Focus trap inside modals (Tab cycles within the modal)
- Esc key closes modals (already partial — formalize)

### J.7 Keyboard navigation for grids
- Source grid: arrow keys move focus, Enter toggles selection
- Library grid: arrow keys, Enter opens detail modal
- Composer: J/K from A.3 layered on top

---

## 6. Bundle B — Media editing power

### B.1 Vision-driven cover-pick
- New endpoint `POST /api/ahb/social/ai/cover-pick` (`cover_vision.md` prompt already exists from Phase 1)
- Server: extract 5 candidate frames from the rendered video at evenly spaced timestamps (0%, 25%, 50%, 75%, 95%)
- For each frame: encode as base64, send to qwen3-vl via Ollama `/api/generate` with the cover_vision prompt
- Aggregate scores, pick the highest, write to `post.cover_path` (replacing the t=0.5s default)
- Library shows the picked cover; user can click "Pick again" to re-run with different temperature
- Synchronous endpoint (~10-20s with qwen3-vl); progress toast while running

### B.2 Whisper.cpp auto-subtitles + burn-in
- Use `faster_whisper` (already installed in venv) — small model by default (tiny.en or base.en for speed)
- New endpoint `POST /api/ahb/social/posts/<id>/subtitles` — extracts audio with ffmpeg, runs whisper, writes `.srt` next to the asset
- Composer settings: "Burn in subtitles" toggle (default ON per the spec's settings.json key `burn_in_subtitles_default`)
- When ON, render pipeline adds `subtitles=<path>.srt` to the filter graph
- Subtitle style configurable: font, size, color, outline, background — defaults to white-on-black-pill, mid-bottom positioning
- Cancel/edit option: user can edit the `.srt` file in the Library's post detail modal before rendering finalizes

### B.3 Piper TTS voiceover
- Install `piper-tts` Python package + 2 voice models (1 male, 1 female) at install time
- New endpoint `POST /api/ahb/social/ai/voiceover` body `{text, voice}` returns path to `voice.wav`
- Composer button: "🔊 Voiceover" opens a modal with text editor pre-filled from the caption, voice picker, generate button, audio preview
- On accept, voice.wav stored on the post as `voiceover_path`
- Render pipeline mixes voiceover into the final audio stream (replacing or adding to music)

### B.4 Music bed mixing + sidechain ducking
- New table `ahb_social_music_library` (path, title, artist, license_url, bpm, key, duration_seconds, mood, indexed_at)
- Music files dropped into `dashboard/static/social/music/free/` indexed automatically on dashboard startup (using `librosa` for BPM/key extraction)
- Composer's variant panel adds a "🎵 Music" button: opens picker modal filtered by mood/tempo
- On selection, music path stored as `music_path` on the post
- Render pipeline: music mixed under voiceover with sidechain ducking via ffmpeg's `sidechaincompress` filter
- Music volume normalized to -18 LUFS (under voiceover at -14 LUFS for clear speech)
- If no voiceover, music plays at -14 LUFS

### B.5 In-app image editor
- New modal for still-image sources: opens by clicking a "✏️" badge on each thumbnail
- Tools: crop (with platform aspect snaps), rotate (90° increments + free-rotate slider), brightness/contrast/saturation sliders, 5 filter presets (none/vivid/B&W/cinematic/warm)
- Edits stored as a JSON sidecar `<sub_path>.edits.json` and applied on render via ffmpeg filter chain (not destructive to the original)
- Apply button writes edits.json; Cancel discards
- Preview is real-time canvas-based (PIL-free; uses CSS filters for sliders + canvas for crop)

### B.6 Logo bug overlay
- Brand kit gets a `logo_path` field (already exists from Phase 1 schema)
- Brand kit modal adds an upload input — accepts PNG with transparency, max 1MB
- Settings adds: "Show logo bug" toggle, position (4 corners), opacity (0-100%)
- Render pipeline adds a `movie=logo.png[wm];[outv][wm]overlay=…` filter when enabled

### B.7 Custom intro/outro clip slots
- Brand kit modal adds intro_clip_path + outro_clip_path uploads (MP4, max 30MB each, < 5 sec each)
- Render pipeline pre-pends intro and appends outro to the concat list when present
- Length constraint enforced server-side (reject > 5s clips, suggest trimming first)
- Per-preset override (a preset can opt-out of intro/outro)

### B.8 Color LUTs
- Ship 5 cube LUTs in `dashboard/static/social/luts/`: cinematic.cube, vibrant.cube, moody.cube, bw.cube, warm.cube
- Composer adds a small LUT picker chip strip
- Render pipeline: `lut3d=<path>.cube` filter when a LUT is selected
- LUTs sourced from public-domain Cube LUT libraries (e.g. github.com/FreeFilmEmulation/free-film-emulation-luts), or generated programmatically (warm/cool = simple gain on R/B channels)

### B.9 Ken-Burns auto-zoom on stills
- For still-image sources in a video render: when the clip duration > 2s, apply ffmpeg's `zoompan` filter
- Zoom direction auto-picked by vision: if the qwen3-vl cover-pick result mentions a subject location, zoom toward it; otherwise center zoom-in
- Toggle in Settings: "Auto Ken-Burns on still photos" (default ON)

### B.10 Beat-sync cuts
- Use `librosa.beat.beat_track` on the chosen music bed to extract beat timestamps
- Composer adds a "Sync cuts to beat" toggle (only available when music is attached)
- Render pipeline trims each clip in the concat list to the nearest beat boundary
- Reduces awkward mid-clip cuts; gives the render a music-video feel

---

## 7. Bundle C — AI tools expansion

### C.1 Hook generator with named virality patterns
- New prompt file `dashboard/prompts/social/hooks_advanced.md`
- Accepts a `pattern` parameter: `curiosity_gap`, `contrarian`, `number_led`, `before_after`, `personal`, `mistake`, `bold_claim`
- Returns 3 hooks per pattern (so 21 total when "all" is requested)
- UI: a pattern picker chip strip; selecting one regenerates that pattern's 3 hooks

### C.2 CTA generator
- New prompt file `dashboard/prompts/social/cta_system.md`
- Generates 3 platform-appropriate CTAs per request
- IG: "Save this for later," "Comment below," "DM me to learn more"
- TT: "Follow for more," "Stitch this," "Duet your version"
- UI: button in variant panel: "🎯 CTA"

### C.3 Comment-bait generator
- New prompt file `dashboard/prompts/social/comment_bait.md`
- Generates a comment-engagement prompt that goes at the END of a caption: "Drop a 🏗️ if you've ever framed a wall"
- 3 variants per request, with a "low-key / moderate / high-effort" intensity scale
- UI button: "💬 Engage"

### C.4 Multi-language batch translate
- Settings drawer adds `translation_targets: ["es"]` (default; user can add up to 5 languages)
- Composer button "🌐 All" generates caption + hashtags for every target language in parallel (concurrent fetch calls)
- Stored on the post as `translations: {es: {caption, hashtags}, pt: {…}, …}`
- Library shows a language chip on each post; clicking a chip shows that language's text
- Bundle endpoint includes one caption file per language

### C.5 Voiceover script generator
- New prompt file `dashboard/prompts/social/voiceover_script.md`
- Takes the existing caption + source description, outputs a voiceover script with:
  - Spoken text (different from caption — shorter, more conversational)
  - Pacing markers `[pause]`, `[emphasis: word]`, `[fast]`
- Piper TTS module reads pacing markers (treats them as SSML-like hints)
- UI: in the Voiceover modal (B.3), a "🤖 Generate script" button calls this

### C.6 Storyboard generator
- New prompt file `dashboard/prompts/social/storyboard.md`
- Input: project description + duration + style
- Output: structured JSON shot list — 5-10 shots with shot_type (wide/medium/close-up/detail), subject, duration_sec, voiceover_line
- New tab in the composer: "📋 Storyboard" — shows the generated shot list as cards
- User can click a shot card to filter the source picker by tags (e.g., "close-up of trim" → filter media tagged "trim" + "detail")

### C.7 B-roll suggestions
- New prompt file `dashboard/prompts/social/broll.md`
- Input: existing media list + caption
- Output: 3-5 shot suggestions you should still capture ("you have framing photos but no closeups of the level — go shoot a 5s tight clip of a level resting on the top plate")
- UI: a "📸 B-roll" button in the composer; results displayed as a checklist in a side panel

### C.8 Performance prediction
- New endpoint `POST /api/ahb/social/ai/predict`
- Heuristic-based (no ML training): combines the AI score (Phase 1's `/ai/score`) with hook length, hashtag count, caption length, platform, time-of-day vs historic best-time
- Returns predicted view range (low/mid/high), confidence, and 3 specific improvements
- UI: a "🔮 Predict" button next to "🎯 Score" in the variant panel
- Stored on the post as `ai_meta.prediction`

### C.9 Best-time-to-post recommendation
- New endpoint `GET /api/ahb/social/best-times?platform=ig_reel`
- Queries the `ahb_social_analytics` table (K) for posts with engagement data; returns the hour-of-week buckets with highest engagement
- Until enough data accumulates, returns industry-standard recommendations (Mon-Fri 6-9am / 11am-1pm / 7-9pm; weekend mornings)
- UI: composer's "Schedule" picker shows recommended slots highlighted in green

### C.10 SD prompt builder UI (for AI image gen)
- The composer's "➕ AI image (SD)" button (currently grays out because SD is paused) opens a small builder modal:
  - Subject input
  - Style chip strip (photorealistic, illustration, watercolor, 3D, isometric, line-art)
  - Aspect picker (locked to current platform's aspect)
  - Negative prompt advanced field
- Generates via existing `sam_imaging` endpoint when SD is up; greys out with a Start SD button when SD is down

---

## 8. Bundle H — Audio pipeline

### H.1 Music library import + auto-index
- Watches `dashboard/static/social/music/free/` directory at boot
- For each new .mp3/.wav: probes with `librosa` for tempo (BPM), key, duration; writes to `ahb_social_music_library` table
- Manual upload endpoint `POST /api/ahb/social/music/upload` accepts files via multipart form
- Mood detection: simple heuristic on tempo (>140 = energetic, 90-140 = moderate, <90 = calm) + filename keywords ("chill" / "epic" / "trap" tagged automatically)
- Re-index button in Settings → Music library

### H.2 Music search & picker
- New endpoint `GET /api/ahb/social/music?mood=&min_bpm=&max_bpm=&q=`
- Picker modal opened from B.4 button: list with play preview (HTML `<audio>`), tempo, mood, duration; "Use this track" button writes path to post

### H.3 Background noise removal
- For voiceover sources from webcam/upload, optional noise removal pre-process
- Uses ffmpeg's `afftdn` filter (FFT-based denoising, no external dep)
- Toggle in voiceover modal: "Clean audio" (default ON for webcam recordings, OFF for clean uploads)
- Server-side preprocessing — adds ~5s to render time but improves quality dramatically

### H.4 Audio level normalization
- Standard targets per platform:
  - TikTok / IG Reel / IG Story: -14 LUFS (matches Spotify/YouTube norms)
  - IG Feed: -16 LUFS
- ffmpeg's `loudnorm` filter applied as a final pass on the audio stream
- Setting: `loudness_target` per platform (configurable in Settings)
- "Skip normalization" toggle for content where you want raw audio

### H.5 Sidechain ducking
- When voiceover + music both present, ffmpeg's `sidechaincompress` automatically reduces music volume by 12dB during voiceover speech
- Threshold and ratio tunable via render_params on the post (default threshold=0.05, ratio=8:1)

### H.6 Sound effects library
- Ship a small library of 10-15 SFX in `dashboard/static/social/sfx/`: whoosh, ding, swipe, click, pop, etc.
- Composer's storyboard view (C.6) gets an SFX picker per shot
- Render pipeline mixes SFX at the specified timestamp in the final audio track
- Licensed CC0 / public-domain only

### H.7 Voiceover voice picker
- Piper supports multiple voice models; ship 4 in `dashboard/static/social/piper-voices/`:
  - en_US-amy-medium.onnx (female, friendly)
  - en_US-ryan-high.onnx (male, professional)
  - en_GB-jenny-medium.onnx (British female)
  - en_US-lessac-medium.onnx (male, narrator)
- Voiceover modal (B.3) has a voice picker; preview button plays a short sample

---

## 9. Bundle I — Sources & inputs

### I.1 Webcam record in-app
- New "📹 Webcam" button in composer's source picker
- Opens a modal with `<video>` showing live preview + Record/Stop button
- Uses `MediaRecorder` API; records as WebM (browser native)
- On stop, uploads to `/api/ahb/social/sources/upload` (new endpoint) which saves under `dashboard/uploads/social/<date>/<uuid>.webm` and inserts a row into `image_captions` so it shows up in the source picker
- Auto-transcoded to MP4 server-side on upload (ffmpeg)

### I.2 Screen recording
- New "🖥️ Screen" button next to Webcam
- Uses `getDisplayMedia()` API; user picks tab/window/screen
- Same recording + upload pipeline as I.1
- Useful for tutorial-style content showing the dashboard or other software

### I.3 URL import (YouTube / TikTok)
- Install `yt-dlp` at deploy time (pip install)
- New "🔗 URL" button: modal accepts a URL, hits backend endpoint `POST /api/ahb/social/sources/url-import`
- Server-side: yt-dlp downloads at 1080p max, saves to `dashboard/uploads/social/<date>/<uuid>.mp4`, runs trim modal for the user to pick which seconds
- Optional: pre-fill the trim modal with timestamps the user paste (e.g., `?t=42` → pre-seek to 42s)
- Rate-limit: max 5 URL imports per hour to avoid yt-dlp abuse
- License notice: explicit warning in the modal that the user is responsible for content they import

### I.4 Drag-drop multiple files
- Whole composer becomes a drop zone (overlay activates on dragover)
- Multiple files dropped at once → each uploaded in parallel
- Progress bar per file
- Auto-classify by extension: video / image / audio

### I.5 Voice memo → caption
- "🎤 Voice memo" button in composer
- Records audio via MediaRecorder; uploads; runs through whisper for transcription
- The transcript fills the caption field (and optionally the voiceover script field)
- Useful for "I just walked off a jobsite, here's what we did" workflow

### I.6 SD prompt builder (covered in C.10)

---

## 10. Bundle D — Workflow & calendar

### D.1 Visual month-view content calendar
- New sub-sub-tab section (replacing the existing list-grouped Scheduler) — actually, KEEP the list view as one tab and ADD a calendar view as another
- Calendar component: 7×6 grid of days for the current month with prev/next navigation
- Each day cell shows up to 3 post chips colored by status (scheduled=blue, posted=green, pending_review=amber, draft=gray); "+N more" if > 3
- Click a chip → opens the post detail modal
- Drag a chip from one day to another → updates `scheduled_at`

### D.2 Bulk operations
- Library and calendar gain checkbox selection (shift-click for range select)
- Bulk action bar appears when ≥ 1 item selected:
  - Set status (approve/reject/schedule)
  - Set scheduled date
  - Delete
  - Telegram drop (sends all selected as one batched message)
  - Tag (apply tag(s) to all)
  - Export bundle (single ZIP containing all selected posts)
- Backend: bulk endpoint `POST /api/ahb/social/posts/bulk` body `{ids, action, params}`

### D.3 Saved drafts as reusable templates
- New table `ahb_social_post_templates`: id, name, caption_template, hashtag_set, platform_targets, music_id, voiceover_script, created_at
- "Save as template" button on post detail
- "Apply template" in composer: picker modal lists templates; on select, pre-fills the variant panel
- Variables in templates: `{{project_name}}`, `{{client_name}}`, `{{date}}`, `{{phase}}` (interpolated at apply-time)

### D.4 Recurring schedule templates
- Each preset can opt into a recurring schedule: "post every Tue + Thu at 9am"
- New columns on `ahb_social_presets`: `schedule_dow` (CSV of days 0-6), `schedule_time` (HH:MM)
- Auto-pilot tick respects this: only fires if today is in `schedule_dow` AND time is within ±30min of `schedule_time`

### D.5 Tags / collections / campaigns
- New table `ahb_social_tags` (id, name, color) and `ahb_social_post_tags` (post_id, tag_id) join table
- Tag picker in post detail; tag column in Library; filter by tag
- "Campaign view" = filter Library by a tag (e.g. "Spring 2026 launch") and see all related posts as a unit
- Bulk tag operation (D.2)

### D.6 Full-text search
- SQLite FTS5 virtual table mirroring `caption`, `hashtags`, `first_comment` of `ahb_social_posts`
- Triggers keep FTS in sync on INSERT/UPDATE/DELETE
- Library search box uses FTS for >2-char queries, simple LIKE for shorter

### D.7 Multi-step approval workflow
- Optional per-preset: `requires_review` (bool)
- When ON, drafts go status='pending_review'; only an explicit Approve action moves them to 'approved'
- Approval log table `ahb_social_approval_events`: id, post_id, action, actor, note, at
- Library shows the approval history on the post detail

### D.8 Version history per post
- Every PATCH to a post writes the prior row to `ahb_social_post_versions` (post_id, version_at, snapshot_json)
- Post detail modal gains "History" button showing diffs between versions
- "Restore" button copies a previous version back to current

### D.9 Auto-save on every edit
- The post detail modal currently has a manual Save button. Change to: 500ms-debounced auto-save on any field change
- Save button becomes "Saved ✓" badge that flashes when an auto-save fires
- Esc closes without prompt (no risk of lost edits since they were already saved)

---

## 11. Bundle G — Trends & discovery

### G.1 Competitor URL paste
- New sub-sub-tab "💡 Inspo" inside `#tab-social`
- Form field: paste a TikTok / IG Reel URL
- Backend: yt-dlp fetches metadata (title, description, hashtags, like/view counts) — does NOT download the video by default
- Result card shows: thumbnail, caption, hashtags, view count, days ago
- "Suggest similar hook" button → feeds the metadata into the hook generator with the prompt "match the structure and tone of this winning post"

### G.2 Hashtag trending tracker
- Manual entry: user pastes a list of hashtags they think are trending; saves to `ahb_social_hashtag_snapshots` (tag, observed_at, source_url)
- Time-series chart: which tags have been seen recently
- "Suggest from trending" button in Composer: picks 3-5 from the most recent 30 days of snapshots, filtered by relevance to current caption (Ollama call)

### G.3 Competitor watch list
- Save a list of competitor handles in `ahb_social_competitors` (handle, platform, notes)
- Manual snapshot button: user pastes a list of recent post URLs, system fetches metadata via G.1's pipeline
- Activity feed view: see all snapshots chronologically per competitor

### G.4 Inspiration feed
- Curated examples bundled with the dashboard: 20-30 "good post" exemplars stored as `dashboard/static/social/inspo/*.json` files
- Each has a category (renovation, day-in-life, before/after, etc.) + thumbnail + caption + hook + structural analysis
- Browser view in the Inspo tab: filter by category; click for detail view

### G.5 Sound trend tracker (manual)
- User pastes TikTok sound URL + the URL of a video using it
- Stored in `ahb_social_sound_snapshots`
- Activity feed of trending sounds
- Future: when TikTok Content API lands (Phase 2), sounds become auto-extractable

---

## 12. Bundle K — Manual analytics dashboard

### K.1 Manual stats entry
- Post detail modal (when status='posted') gains an "📊 Stats" panel:
  - Views, Likes, Comments, Saves, Shares (manual integer inputs)
  - Posted-at timestamp (auto-populated when status flipped to 'posted', editable)
  - Post URL (for reference)
- Auto-save 500ms debounce; stored in new table `ahb_social_analytics` (post_id PRIMARY KEY, views, likes, comments, saves, shares, posted_at, post_url, updated_at)

### K.2 Performance dashboard
- New sub-sub-tab "📊 Stats" inside `#tab-social`
- Top row: this-week vs last-week totals (views, engagement rate)
- Top performers chart: top 10 posts by views in the selected window (date picker: 7d, 30d, 90d, all)
- Per-platform breakdown: pie chart of views per platform
- Hashtag performance: list of hashtags with avg views per post

### K.3 Best-time-to-post heatmap
- 7×24 grid showing day-of-week × hour-of-day
- Cell color = avg engagement rate of posts posted in that bucket
- Powers the C.9 recommendation engine
- Empty cells (no data) shown in gray

### K.4 Engagement-rate trend
- Line chart: engagement_rate (= (likes + comments + saves + shares) / views) over time, weekly buckets
- Shows whether your content is improving

### K.5 Hashtag performance report
- For each unique hashtag used, aggregate stats: total uses, total views attributed, avg engagement
- Sorted by performance
- Power to refine the brand-kit `hashtag_floor` list based on data

### K.6 Manual analytics CSV import
- Bulk-import stats: CSV with columns `post_url,views,likes,...`
- New endpoint `POST /api/ahb/social/analytics/import-csv`
- Useful for backfilling old posts

---

## 13. Data model additions

New tables (all in `dashboard/baza_projects.db`, idempotent migrations via a new `_ensure_social_v2_tables()` function called next to `_ensure_social_tables()`):

```sql
CREATE TABLE IF NOT EXISTS ahb_social_music_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    title TEXT,
    artist TEXT,
    license_url TEXT,
    bpm INTEGER,
    key_signature TEXT,
    duration_seconds REAL,
    mood TEXT,
    tags TEXT,  -- json array
    indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ahb_social_post_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    caption_template TEXT,
    hashtag_set TEXT,
    platform_targets TEXT DEFAULT '[]',
    first_comment_template TEXT,
    music_id INTEGER,
    voiceover_script TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ahb_social_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT DEFAULT '#10b981',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ahb_social_post_tags (
    post_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (post_id, tag_id)
);

CREATE TABLE IF NOT EXISTS ahb_social_hashtag_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag TEXT NOT NULL,
    observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    source_url TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS ahb_social_competitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handle TEXT NOT NULL,
    platform TEXT NOT NULL,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ahb_social_sound_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sound_url TEXT,
    example_video_url TEXT,
    title TEXT,
    observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS ahb_social_analytics (
    post_id INTEGER PRIMARY KEY,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    saves INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    posted_at TEXT,
    post_url TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ahb_social_approval_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    actor TEXT,
    note TEXT,
    at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ahb_social_post_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    version_at TEXT DEFAULT CURRENT_TIMESTAMP,
    snapshot TEXT NOT NULL  -- full json snapshot of the post row
);

-- FTS5 virtual table for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS ahb_social_posts_fts USING fts5(
    caption, hashtags, first_comment,
    content='ahb_social_posts',
    content_rowid='id'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS ahb_social_posts_ai AFTER INSERT ON ahb_social_posts BEGIN
    INSERT INTO ahb_social_posts_fts(rowid, caption, hashtags, first_comment)
    VALUES (new.id, new.caption, new.hashtags, new.first_comment);
END;

CREATE TRIGGER IF NOT EXISTS ahb_social_posts_au AFTER UPDATE ON ahb_social_posts BEGIN
    INSERT INTO ahb_social_posts_fts(ahb_social_posts_fts, rowid, caption, hashtags, first_comment)
    VALUES('delete', old.id, old.caption, old.hashtags, old.first_comment);
    INSERT INTO ahb_social_posts_fts(rowid, caption, hashtags, first_comment)
    VALUES (new.id, new.caption, new.hashtags, new.first_comment);
END;

CREATE TRIGGER IF NOT EXISTS ahb_social_posts_ad AFTER DELETE ON ahb_social_posts BEGIN
    INSERT INTO ahb_social_posts_fts(ahb_social_posts_fts, rowid, caption, hashtags, first_comment)
    VALUES('delete', old.id, old.caption, old.hashtags, old.first_comment);
END;

CREATE INDEX IF NOT EXISTS idx_music_library_mood ON ahb_social_music_library(mood);
CREATE INDEX IF NOT EXISTS idx_music_library_bpm ON ahb_social_music_library(bpm);
CREATE INDEX IF NOT EXISTS idx_hashtag_snapshots_tag ON ahb_social_hashtag_snapshots(tag);
CREATE INDEX IF NOT EXISTS idx_post_versions_post ON ahb_social_post_versions(post_id);
```

New columns on existing tables:

```sql
ALTER TABLE ahb_social_posts ADD COLUMN translations TEXT DEFAULT '{}';
ALTER TABLE ahb_social_posts ADD COLUMN music_id INTEGER;
ALTER TABLE ahb_social_posts ADD COLUMN voiceover_path TEXT;
ALTER TABLE ahb_social_posts ADD COLUMN subtitles_path TEXT;
ALTER TABLE ahb_social_posts ADD COLUMN lut_name TEXT;
ALTER TABLE ahb_social_presets ADD COLUMN requires_review INTEGER DEFAULT 0;
ALTER TABLE ahb_social_presets ADD COLUMN schedule_dow TEXT;  -- "0,1,2,3,4,5,6"
ALTER TABLE ahb_social_presets ADD COLUMN schedule_time TEXT;  -- "HH:MM"

-- Phase 1's ahb_social_jobs needs a PID column so we can kill in-flight ffmpeg renders.
ALTER TABLE ahb_social_jobs ADD COLUMN pid INTEGER;
```

---

## 14. API surface additions

All new routes mounted on the existing `social_bp` Blueprint:

```
# Render pipeline async
GET    /api/ahb/social/jobs/<id>                            (already exists from Phase 1; reused for async render polling)
DELETE /api/ahb/social/jobs/<id>                            (cancel: kills ffmpeg subprocess via stored PID, marks status='cancelled')
POST   /api/ahb/social/posts/<id>/render-async              (returns job_id)
POST   /api/ahb/social/posts/render-all                     (kicks N renders for N platforms)

# Bundle B — media editing
POST   /api/ahb/social/ai/cover-pick                        (vision per-frame)
POST   /api/ahb/social/posts/<id>/subtitles                 (whisper transcribe)
POST   /api/ahb/social/ai/voiceover                         (piper TTS)
POST   /api/ahb/social/sources/<id>/edits                   (save image-editor edits.json)
DELETE /api/ahb/social/sources/<id>/edits                   (revert)

# Bundle C — AI tools
POST   /api/ahb/social/ai/hook                              (named patterns)
POST   /api/ahb/social/ai/cta
POST   /api/ahb/social/ai/comment-bait
POST   /api/ahb/social/ai/translate-all                     (multi-lang batch)
POST   /api/ahb/social/ai/voiceover-script                  (caption → script)
POST   /api/ahb/social/ai/storyboard
POST   /api/ahb/social/ai/broll
POST   /api/ahb/social/ai/predict
POST   /api/ahb/social/ai/sd-image                          (SD prompt builder)
GET    /api/ahb/social/best-times                           (per-platform)

# Bundle D — workflow
POST   /api/ahb/social/posts/bulk                           (multi-id action)
GET    /api/ahb/social/templates
POST   /api/ahb/social/templates
PUT    /api/ahb/social/templates/<id>
DELETE /api/ahb/social/templates/<id>
POST   /api/ahb/social/templates/<id>/apply                 (returns pre-filled post draft)
GET    /api/ahb/social/tags
POST   /api/ahb/social/tags
PUT    /api/ahb/social/tags/<id>
DELETE /api/ahb/social/tags/<id>
POST   /api/ahb/social/posts/<id>/tags                      (set tags)
GET    /api/ahb/social/posts/<id>/versions
POST   /api/ahb/social/posts/<id>/versions/<v>/restore
GET    /api/ahb/social/posts/<id>/approval-history

# Bundle G — trends
POST   /api/ahb/social/inspo/import-url                     (yt-dlp metadata)
GET    /api/ahb/social/hashtag-snapshots
POST   /api/ahb/social/hashtag-snapshots                    (manual entry)
GET    /api/ahb/social/competitors
POST   /api/ahb/social/competitors
DELETE /api/ahb/social/competitors/<id>
GET    /api/ahb/social/inspo/library                        (curated examples)
GET    /api/ahb/social/sound-snapshots
POST   /api/ahb/social/sound-snapshots

# Bundle H — audio
POST   /api/ahb/social/music/upload
POST   /api/ahb/social/music/reindex
GET    /api/ahb/social/music                                (search w/ filters)
GET    /api/ahb/social/sfx                                  (library list)

# Bundle I — sources
POST   /api/ahb/social/sources/upload                       (multipart from webcam/screen/file picker)
POST   /api/ahb/social/sources/url-import                   (yt-dlp download)
POST   /api/ahb/social/sources/voice-memo                   (audio upload + whisper)

# Bundle K — manual analytics
GET    /api/ahb/social/posts/<id>/analytics
PUT    /api/ahb/social/posts/<id>/analytics
POST   /api/ahb/social/analytics/import-csv
GET    /api/ahb/social/analytics/summary?window=30d
GET    /api/ahb/social/analytics/heatmap
GET    /api/ahb/social/analytics/hashtags
```

---

## 15. Frontend module structure additions

New IIFE modules to register under `window.SocialStudio.modules`:

- `toast` — global notification system (Bundle A.2)
- `keymap` — keyboard shortcut registry (Bundle A.3)
- `progress` — render job polling UI (Bundle A.4)
- `shotlist` — drag-reorder + trim handles (Bundle A.5, A.6)
- `device` — device frame mockups (Bundle F.1, F.6, F.7)
- `overlay` — platform UI overlay (Bundle F.2-F.4)
- `gridpreview` — IG cover grid preview (Bundle F.5)
- `tour` — first-time user tour (Bundle J.4)
- `mobile` — mobile layout / touch handlers (Bundle J.1, J.2)
- `imageditor` — in-app image editing modal (Bundle B.5)
- `voiceover` — TTS modal (Bundle B.3)
- `subtitles` — subtitle editor (Bundle B.2)
- `music` — music library picker (Bundle B.4, H.2)
- `recorder` — webcam/screen capture (Bundle I.1, I.2)
- `urlimport` — URL paste modal (Bundle I.3)
- `voicememo` — voice → caption (Bundle I.5)
- `sdpromptbuilder` — SD image gen UI (Bundle C.10)
- `calendar` — month view (Bundle D.1)
- `bulk` — multi-select + action bar (Bundle D.2)
- `templates` — saved templates picker (Bundle D.3)
- `tags` — tag manager (Bundle D.5)
- `search` — FTS-powered search (Bundle D.6)
- `versions` — post history viewer (Bundle D.8)
- `inspo` — trends sub-tab (Bundle G)
- `stats` — analytics dashboard (Bundle K)
- `prediction` — performance predict UI (Bundle C.8)
- `storyboard` — storyboard generator UI (Bundle C.6)

Each module is a self-contained IIFE that registers itself; each has its own `_esc()` helper for HTML escaping; modal modules mount into body-level slots (existing or new — list of slots to add to `ahb123.html`):

```
#socialToastStack
#socialImageEditor
#socialVoiceoverV2 (replaces stub from Phase 1)
#socialSubtitlesEditor
#socialMusicPicker
#socialRecorder
#socialURLImport
#socialSDPromptBuilder
#socialBulkActionBar
#socialTemplatesPicker
#socialTagsManager
#socialSearchOverlay
#socialVersionsViewer
#socialTour
#socialShortcutsHelp
```

---

## 16. Backend module structure additions

Splitting `dashboard/social_studio.py` would be ideal (currently ~1100 lines after Phase 1). For v2 we add new sibling modules to keep concerns separate:

- `dashboard/social_studio.py` — keep as the Blueprint mount point; route registrations stay here but logic delegates to:
- `dashboard/social_ai.py` — all `/ai/*` route logic moves here (hook, caption, hashtag, cover-pick, storyboard, etc.)
- `dashboard/social_render.py` — already exists; gains filter-graph builders for subtitles, music, LUTs, logo bug, intro/outro
- `dashboard/social_audio.py` — new: voiceover (piper), denoise, normalize, music library indexer
- `dashboard/social_sources.py` — new: webcam/screen/URL/voice-memo upload + yt-dlp orchestration
- `dashboard/social_workflow.py` — new: templates, tags, bulk ops, versions, approval log
- `dashboard/social_analytics.py` — new: stats CRUD, summary aggregations, heatmap, hashtag perf
- `dashboard/social_trends.py` — new: inspo URL parsing, hashtag snapshots, competitors, sounds
- `dashboard/social_settings.py` — already exists; gains accessors for new settings keys

Routes still register on the single `social_bp` Blueprint but live in their topical module file. The Blueprint imports each module at registration time.

---

## 17. New dependencies

To be installed at deploy time (`pip install …` into the existing `venv`):

- `piper-tts` — TTS (and download 4 voice ONNX files from rhasspy/piper releases)
- `yt-dlp` — URL import
- Pillow (`pip install pillow`) — used by image editor for preview thumbnails (faster than re-encoding through ffmpeg for slider feedback)

Already installed (verified):
- `ffmpeg` / `ffprobe` (system)
- `faster_whisper` (Python — provides whisper.cpp-equivalent via CTranslate2 runtime)
- `librosa` (Python — BPM/key/beat detection for music library + beat-sync)

No `rnnoise` needed — we use ffmpeg's `afftdn` filter for noise removal.

Music files, SFX, and LUTs are downloaded at first run from public mirrors. The deploy script `dashboard/social_install_assets.sh` will:
1. Pip-install the 3 packages above
2. Download 4 Piper voice ONNX files into `dashboard/static/social/piper-voices/`
3. Download real Inter fonts (replacing placeholders)
4. Download 5 free LUTs into `dashboard/static/social/luts/`
5. Symlink or copy 10-15 CC0 SFX into `dashboard/static/social/sfx/`

Failure of any non-essential step (e.g., piper voices) prints a warning but doesn't abort — the affected feature gracefully degrades (voiceover button shows "piper not installed" with install hint).

---

## 18. Risks + mitigations

| Risk | Mitigation |
| --- | --- |
| Adding 46 features in one cycle = thin code review per feature | Split into 3 internal phases (v2.0 / v2.1 / v2.2) with code review checkpoints between |
| Whisper/Piper/yt-dlp install fragility on first-run | Each install step is idempotent + non-blocking; features gracefully degrade with install hints |
| Faster-whisper memory pressure (CPU model = ~150MB; loaded per-request without caching = slow) | Cache the model in a process-global var, lazy-loaded on first use; tiny.en model = 75MB only |
| yt-dlp can fetch copyrighted content | Add a usage notice in the URL Import modal; rate-limit to 5/hour; log every import to `task_journal` for audit |
| Bundle E (publishing) absence means scheduled posts never auto-post | The Scheduler view shows "Scheduled for X — drop to phone manually until Phase 2 publishing lands". Telegram drop endpoint (Phase 1) is the manual posting workflow until then. |
| Calendar drag-to-reschedule UX is hard to do mobile-friendly | Provide a "tap to set date" fallback alongside drag |
| Storage growth: many videos accumulate | Bundle K adds a "Library cleanup" admin view in the Stats sub-tab: lists posts with status='posted' older than a configurable window (default 90d) and offers archive (move file to `archive/`) or delete |
| In-app image editor canvas may struggle with very large source images (24MP DSLR shots) | Downscale to max 2048px on the preview canvas; full-res only at render time |
| Mobile touch-drag may conflict with browser swipe gestures | Use `touch-action: none` on drag handles; explicitly handle scrolling on non-handle areas |
| Beat-sync cuts may produce awkward results with non-music content | Toggle is OFF by default; user opts in per render |
| ARIA + tooltip + tour need testing with real screen readers | We SHIP J.4 (tour) and J.6 (ARIA labels) in v2.0; formal screen-reader QA is best-effort during acceptance. Re-audit if accessibility complaints surface. |
| FTS5 not always compiled into SQLite | Detect at startup; fall back to LIKE if FTS5 unavailable; log the limitation |
| Webcam/screen capture only works on HTTPS or localhost | Dashboard runs on http://127.0.0.1:8888 — browsers treat localhost as secure context. Good. |
| URL imports may fail for sites yt-dlp doesn't support | Catch yt-dlp errors gracefully; show clear error message with suggestion |
| Multi-language translate may hit Ollama timeout when fanning out to 5 languages | Run sequentially with progress toast; allow user to cancel mid-batch |
| In-app image editor saves edits.json as a sidecar — risks lost edits if the source file is deleted | Edits are tied to source ID, not file path; on file delete, edits are orphaned but harmless |

---

## 19. Acceptance test plan

A representative checklist (the full v2 checklist will be assembled in the writing-plans skill). High-signal items:

1. **A** Open Composer; render a post; see a progress toast that fills to 100% then becomes a success toast.
2. **A** Press `?` from the Social tab — keyboard shortcut overlay appears.
3. **A** Click "Render ALL platforms" — 4 posts appear in Library within 3 min, statuses correct.
4. **B** Render a Reel with a 30s clip; subtitles burn in; voiceover plays under music; music ducks during voiceover.
5. **B** Crop a still image in the image editor; the edits show on the render output but the original file is unchanged.
6. **C** Generate hooks with `pattern=curiosity_gap` — receive 3 distinct hooks matching the pattern.
7. **C** Generate a storyboard for a project; click a shot card; source picker filters appropriately.
8. **C** "Predict" returns view-range estimate + 3 improvements.
9. **D** Drag a scheduled post from one day to another on the calendar; `scheduled_at` updates.
10. **D** Multi-select 5 drafts; bulk approve them; all 5 flip to `approved`.
11. **D** Save a draft as a template; create a new post from that template; pre-filled correctly.
12. **D** Search "framing" in Library; relevant posts surface via FTS.
13. **F** Toggle iPhone frame; preview shows notch + dynamic island.
14. **F** Toggle TikTok overlay; right-rail UI elements visible over preview.
15. **F** Toggle safe-zone indicators; red bands at top and bottom.
16. **G** Paste a TikTok URL; result card with thumbnail/caption/hashtags appears; "Suggest similar hook" returns a hook in the same tone.
17. **H** Drop an MP3 into `dashboard/static/social/music/free/`; restart dashboard; track appears in music picker with BPM/duration detected.
18. **H** Render with music + voiceover; final mix has audible ducking during speech.
19. **I** Click Webcam button; record 5s clip; clip appears in source picker and is usable in a render.
20. **I** Paste a YouTube URL; trimmed clip appears in source picker.
21. **J** Open Social tab on phone (responsive testing); composer is touch-usable; bottom-sheet variant panel slides up.
22. **K** Mark a post as posted; enter analytics (views, likes); Stats dashboard updates.
23. **K** Heatmap shows engagement-rate buckets across days × hours.
24. **K** Top performers list ranks correctly.

---

## 20. Implementation phasing detail

Suggested groupings for the writing-plans phase (the actual plan may split these further into per-task chunks):

### Phase v2.0 — polish & preview (Bundles A + F + J)

Tasks ~1-10. Each task is small (~1-2 hours). Order:

1. Install real Inter fonts (A.1)
2. Toast system (A.2)
3. Keyboard shortcuts (A.3) + help overlay
4. Render async + progress polling UI (A.4)
5. Drag-reorder shot list (A.5)
6. Per-clip trim handles (A.6)
7. Render-all-platforms button (A.7)
8. A/B variations + translate-in-composer (A.8, A.9)
9. Device frames + platform overlays + safe zones (F.1-F.4)
10. Caption truncation + cover grid + light/dark + DPR toggles (F.4-F.7)
11. Mobile-responsive composer + touch drag (J.1, J.2)
12. Tooltips + tour + empty-state CTAs + ARIA + keyboard nav (J.3-J.7)

### Phase v2.1 — media & AI (Bundles B + C + H + I)

Tasks ~13-34. Larger chunks:

13. Schema migrations for v2 (data model §13)
14. Install script for piper/yt-dlp/pillow + asset downloads (§17)
15. Module split: extract social_ai.py, social_audio.py, social_sources.py from social_studio.py (§16)
16. Vision cover-pick (B.1)
17. Whisper subtitles + burn-in (B.2)
18. Piper voiceover (B.3, H.7)
19. Music library indexer (H.1) + music picker (H.2)
20. Sidechain ducking + audio normalization + noise removal (H.3-H.5)
21. SFX library (H.6)
22. In-app image editor (B.5)
23. Logo bug + intro/outro + LUTs (B.6-B.8)
24. Ken-Burns + beat-sync (B.9-B.10)
25. Hook patterns + CTA + comment-bait (C.1-C.3)
26. Multi-language translate + voiceover script gen (C.4-C.5)
27. Storyboard generator (C.6)
28. B-roll suggestions (C.7)
29. Performance prediction + best-times (C.8-C.9)
30. SD prompt builder UI (C.10)
31. Webcam recorder (I.1)
32. Screen recorder (I.2)
33. URL import via yt-dlp (I.3) + voice memo (I.5)
34. Drag-drop multi-file upload (I.4)

### Phase v2.2 — workflow & trends (Bundles D + G + K)

Tasks ~35-46:

35. Schema additions for workflow tables (templates, tags, versions, approval, analytics)
36. Visual month calendar with drag-reschedule (D.1)
37. Bulk operations (D.2)
38. Templates CRUD (D.3)
39. Recurring schedule templates (D.4)
40. Tags / collections (D.5)
41. FTS5 search (D.6)
42. Approval workflow (D.7)
43. Version history (D.8)
44. Auto-save (D.9)
45. Inspo URL paste + competitor watch + hashtag/sound trackers + curated examples (G.1-G.5)
46. Manual analytics + dashboard + heatmap + hashtag perf + CSV import (K.1-K.6)

---

## 21. Open implementation decisions deferred to the plan

These are small enough that the plan can resolve them without going back to brainstorming:

- Exact Piper voice models to bundle (4 candidates listed in H.7; final list depends on which are available on the rhasspy/piper releases at install time)
- Exact LUT sources (5 candidates; can be generated programmatically as a fallback)
- Exact threshold for sidechaincompress (default given but may need ear-tuning on real content)
- Whether to use SQLite FTS5 or fall back to LIKE if FTS5 not compiled
- Whether the inspo curated examples ship in the repo (small) or download on first run (avoids bloat)
- Whether mobile bottom-sheet variant panel uses CSS `dvh` units (modern) or a polyfill
- Whether the tour uses Driver.js (dep) or hand-rolled overlays (no dep)

End of design.
