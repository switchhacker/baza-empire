# Marketing & Media Super Skills — Design

**Date:** 2026-06-23
**Author:** Claude (with Serge)
**Status:** Approved design → ready for implementation plan
**Owner agent:** Sam Axe (VP Creative & Marketing); usable by any agent via `##SKILL##`

## Purpose

Give Baza agents a small set of high-leverage "super skills" that turn a topic,
a project, or a couple of job photos into **polished, on-brand AHBCO marketing
deliverables** — social campaigns, before/after showcases, and branded flyers/ads —
without leaving the local-first stack.

The skills orchestrate infrastructure that already exists (SD WebUI Forge on the
3070, the Sam imaging Tool Server, local Ollama, Social Studio, `artifact_save`)
behind four clean skill entry points plus one shared brand source of truth.

## Hard rules this design honors

- **Local-first (HARD):** all copywriting runs on a local Ollama model; all
  imagery runs on local SD WebUI Forge. No cloud LLM/API calls in new code.
- **Photo-first:** real AHBCO project photos are the base for project content;
  Stable Diffusion is used only for decorative backgrounds/textures or when no
  photo exists. Never fabricate fake "completed jobs" with AI.
- **Confirm-before-act:** skills render + save + (optionally) queue. They
  **never auto-publish** to a live platform. Human approval stays in Social Studio.
- **Artifact tracking:** every deliverable is saved via the existing
  `artifact_save` skill so it shows up in the dashboard.
- **Small skills convention:** thin orchestrator skills + one shared library,
  not a monolith.

## Architecture (layered)

```
brand.json  ──────────────┐  (source of truth: colors, fonts, logo, tagline, voice)
                          │
skills/shared/media_kit.py│  (shared Pillow + LLM + SD helpers; imported by skills)
   ├─ load_brand()        │
   ├─ canvas(platform)    │  IG square / reel / FB / YT thumb / flyer sizes
   ├─ draw_headline()     │  text w/ shadow, auto-fit, safe area
   ├─ place_logo()        │
   ├─ scrim() / gradient()│  legibility overlays
   ├─ write_copy(brief)   │  local Ollama, auto-picked model
   ├─ gen_background(p)   │  SD WebUI Forge via Sam tool/endpoint
   └─ load_photo(path)    │  open/crop/cover-fit real photos
                          │
skills/shared/            ▼
   ├─ brand_kit.py            detect|show|set  → writes brand.json
   ├─ social_campaign.py      topic|project → per-platform post pack + queue
   ├─ before_after_showcase.py  two photos|project → branded comparison graphic
   └─ marketing_flyer.py      offer/service → branded flyer/ad (print + digital)
```

All four skills follow the existing contract: args via `SKILL_ARGS` env JSON,
result as JSON on stdout, exit 0 = success. They are discovered by the filesystem
scan in `core/skills_engine.py` (shared lookup), no registry edit needed.

### Why a shared `media_kit.py`

Compositing, brand loading, copy generation, and SD calls are identical across
all three creative skills. Putting them in one importable module keeps each skill
to orchestration only (~80–150 lines), makes the hard rules enforceable in one
place, and is unit-testable without invoking a full skill. Skills import it via
`sys.path.insert(0, os.path.dirname(__file__)); import media_kit`.

## Components

### 0. `brand.json` (source of truth)
Location: `agents/sam_axe/brand/brand.json`. Versioned (`"version"`, `"updated"`).
Schema (illustrative):
```json
{
  "version": 1,
  "updated": "2026-06-23",
  "name": "All Home Building Co",
  "short_name": "AHBCO",
  "tagline": "Drown the competition.",
  "site": "https://ahb123.com",
  "colors": { "primary": "#0A3D62", "secondary": "#1E90FF",
              "accent": "#F39C12", "light": "#F5F7FA", "dark": "#13202E" },
  "fonts": { "headline": "/path/to/Heavy.ttf", "body": "/path/to/Regular.ttf" },
  "logo": "agents/sam_axe/brand/assets/logo.png",
  "voice": "confident, local, trustworthy, no jargon"
}
```

### 1. `brand_kit.py`
- `mode=detect`: fetch `https://ahb123.com`, parse HTML for logo
  (`og:image` → `<img>` with logo-ish class/alt → favicon), download to
  `assets/`. Extract dominant colors by feeding a rendered/representative image
  through Sam's existing `color-palette` tool (`POST /tools/sam/color-palette`),
  map to primary/secondary/accent. Pick bundled fonts. Write `brand.json`.
  **Fallback:** if the site is unreachable (residential DHCP / WAN drift is a
  known risk), write a sensible AHBCO default brand and report `source:"fallback"`.
- `mode=show`: return current `brand.json`.
- `mode=set`: patch specific fields (e.g. `{"colors":{"accent":"#..."}}`),
  bump version. Never silently overwrite the logo.

### 2. `social_campaign.py`
- Input: `{ "topic": "...", "project_id": null, "platforms": ["ig_square","ig_reel","fb","yt_thumb"], "queue": true }`.
- Copy: `media_kit.write_copy()` → caption + hashtag set + first-comment, in brand voice.
- Image: if `project_id` or a photo path given → real photo base; else
  `gen_background()` via SD. Composite headline + logo + scrim per platform size.
- Output: one rendered image + copy per platform → `artifact_save` each; if
  `queue=true`, insert into Social Studio `ahb_social_posts` as **pending** (no publish).

### 3. `before_after_showcase.py`
- Input: `{ "before": "path", "after": "path", "project_id": null, "title": "...", "details": "...", "platforms": [...] }`.
- If `project_id`, pull representative job photos; else use given paths (photo-first, required — no AI fabrication of the work itself).
- Render branded side-by-side comparison (labeled BEFORE / AFTER, divider, project
  title, details strip, logo, CTA). Save artifact; optional Social Studio queue.

### 4. `marketing_flyer.py`
- Input: `{ "headline": "...", "subhead": "...", "bullets": [...], "cta": "...", "offer": "...", "photo": null, "sizes": ["flyer_portrait","ad_square","ad_landscape"] }`.
- Copy gaps (e.g. missing subhead/bullets) filled by `write_copy()` from `offer`.
- Base = real photo if given, else SD background. Composite branded layout:
  headline block, bullet benefits, CTA button, logo, contact strip. Save artifacts.

## Data flow (social_campaign example)

```
agent emits ##SKILL:social_campaign{"project_id":4,"queue":true}##
  → skills_engine subprocess (SKILL_ARGS)
    → media_kit.load_brand()              (brand.json)
    → media_kit.write_copy(brief)         (local Ollama, auto-picked)
    → photo base from project 4 photos    (photo-first)
    → media_kit.canvas + draw + logo      (Pillow, per platform)
    → ##SKILL:artifact_save## per variant  (dashboard-tracked)
    → insert ahb_social_posts (pending)   (Social Studio queue)
  → JSON result: {artifacts:[...], queued:[...], skill:"social_campaign"}
```

## Local model selection (copywriting)

`media_kit.write_copy()` queries Ollama (`GET /api/tags` on the primary instance
:11434) at runtime and selects the strongest installed general chat model by a
small preference ranking, with graceful fallback down the list and a final
fallback to a deterministic template if no model is reachable. No hardcoded model
dependency; no cloud.

## Error handling

- Site unreachable in `detect` → fallback brand, `source:"fallback"`, non-fatal.
- SD WebUI down → if photo base exists, proceed photo-only; else solid brand-color
  background + clear warning in result. Never hard-fail a render for a missing bg.
- Ollama down → template copy fallback, flagged in result.
- Missing required photos in `before_after_showcase` → hard error (no AI fabrication).
- All skills return JSON with an `errors`/`warnings` array; partial success returns
  the artifacts that did render.

## Testing strategy

- `media_kit` unit tests: brand load, canvas sizes, text auto-fit, logo placement,
  copy-model selection (mocked `/api/tags`), with rendered output written to a temp
  dir and asserted on dimensions/non-empty.
- Each skill: invoked with `SKILL_ARGS` against a temp output dir / `dry_run` flag;
  assert JSON shape, artifact paths exist, correct per-platform dimensions, and that
  `queue=false` never touches Social Studio.
- Fallback paths tested by pointing at an unreachable host / empty tag list.
- Follows the repo's existing TDD pattern (RED → GREEN, full regression run).

## Out of scope (YAGNI)

- Auto-publishing to live platforms.
- Video editing / motion graphics (Social Studio render engine already handles
  format conversion; this is still-image + copy focused).
- A dashboard UI tab (skills are agent-facing; UI can come later if wanted).
- Multi-language / localization.

## Dependencies

- Pillow 12.1.1 (present), `requests` (present).
- Bundled brand fonts under `agents/sam_axe/brand/assets/fonts/` (add 1 headline +
  1 body TTF with OFL/permissive license).
- Existing services: SD WebUI :7860, Sam Tool Server :8000, Ollama :11434,
  `artifact_save` skill, Social Studio `ahb_social_posts`.
