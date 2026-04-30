# Vision UI — Image Catalogue Engine (v1: Reference Retrieval)

**Status:** Draft, awaiting approval
**Date:** 2026-04-30
**Owner:** AHB
**Scope:** Server-side image ingestion + structured catalogue + Vision UI browser, on Baza.
**Out of scope (this spec):** CYD ESP32 firmware (separate project, on hold), IP-Adapter / ControlNet conditioning (roadmap), LoRA training loop (roadmap).

## 1. Problem statement

Baza already captures every Telegram-inbound image into `dashboard/artifacts/.private-inbound/<agent>/` and the existing `image_indexer.py` writes free-text captions + tags via qwen3-vl. That gives fuzzy text search over private images but nothing structured: agents can't ask "find me a blonde female in swimwear at the beach" and get a deterministic answer; folders can't be browsed by attribute; cropped reference parts (faces, hands, eyes) don't exist as first-class artifacts; and there's no mechanism for filling demand gaps when a category has zero or thin coverage.

We want to extend the existing private-image pipeline with:
- A structured attribute layer (controlled vocabulary).
- Cropped object library (faces, body parts) as their own first-class assets.
- A virtual folder hierarchy browseable from the dashboard.
- Specter as the gap-filler — scrapes safe sources, generates via SD Forge when GPU is idle, never writes into the public corpus.
- All sources (inbound, scraped, generated) flowing into the same catalogue.

## 2. Non-goals

- Public-facing access. Catalogue is gated by the same passphrase as today's `/datahub/private`.
- Real folder hierarchy on disk. Files stay where they are; the tree is a DB query, rendered.
- Real-time pipelines. Indexing is batch, every 30 min, low priority on the AMD GPU.
- Scraping the open web by HTML parsing. v1 ships with a curated allow-list of CC0/CC-BY image APIs only.
- Generating images of identifiable real people from scraped sources. Generation is for stand-ins and abstract attribute coverage only.

## 3. Architecture

```
                         ┌────────────────────────────────────┐
                         │   Baza Dashboard (Flask, app.py)   │
                         │  /datahub/private  →  /vision      │
                         │  templates/private.html → vision.html│
                         │  +  theme toggle (header)           │
                         └─────────────────┬──────────────────┘
                                           │ JSON API
       ┌───────────────────────────────────┼────────────────────────────────────┐
       │   /api/vision/tree     /api/vision/browse?path=...   /api/vision/search?q=...
       │   /api/vision/asset/<id>           /api/vision/specter/seed   ...      │
       └───────────────────────────────────┼────────────────────────────────────┘
                                           │
                            ┌──────────────┴──────────────┐
                            │   vision_engine (new pkg)   │
                            │  dashboard/vision/          │
                            │   ├─ engine.py              │
                            │   ├─ classifier.py          │   ← qwen3-vl structured JSON
                            │   ├─ cropper.py             │   ← InsightFace + qwen-bbox
                            │   ├─ taxonomy.py            │   ← virtual folder defs
                            │   ├─ search.py              │   ← FTS5 + attribute filters
                            │   └─ specter_seeder.py      │   ← gap fill / scrape / generate
                            └──────────────┬──────────────┘
                                           │
              ┌────────────────────────────┼─────────────────────────────┐
   ┌──────────┴─────────┐        ┌─────────┴──────────┐        ┌─────────┴─────────┐
   │ vision.db (new)    │        │ Filesystem assets   │        │ External tools    │
   │  · assets          │        │ artifacts/.private- │        │ · Ollama qwen3-vl │
   │  · attributes      │◄──FK──►│  inbound/<agent>/   │        │   (RX 6700 XT)    │
   │  · crops           │        │ artifacts/.vision-  │        │ · SD WebUI Forge  │
   │  · captions        │        │  generated/         │ (new)  │   (RTX 3070,      │
   │  · seed_demand     │        │ artifacts/.vision-  │        │   port 11435)     │
   │  · gpu_lease       │        │  scraped/           │ (new)  │ · Image APIs      │
   │  · ingest_log      │        │ artifacts/.vision-  │        │   (Specter)       │
   │  · assets_fts      │        │  crops/             │ (new)  │                   │
   └────────────────────┘        └─────────────────────┘        └───────────────────┘
```

**Reused unchanged:** Flask app, session-based passphrase gate (`_is_private_unlocked()`), `private_inbound.is_private()`, `image_indexer.py` (still captions the public corpus exactly as today), `nuc-specter.service`, Ollama @ 11434, SD WebUI Forge @ 11435.

**New:** `vision.db` (separate SQLite alongside `image_captions.db`), `dashboard/vision/` package, `vision_indexer.py` + systemd unit/timer, three artifact subdirs (`.vision-generated/`, `.vision-scraped/`, `.vision-crops/`), `/vision` page, six JSON endpoints.

**Hardware split:** classification + cropping run on AMD RX 6700 XT (Ollama), generation runs on NVIDIA RTX 3070 (SD Forge). Different GPUs, no contention. The `gpu_lease` table coordinates Specter's RTX 3070 use against scheduled cron jobs.

## 4. Data model

### 4.1 Schema (`dashboard/vision.db`, SQLite + FTS5)

```sql
CREATE TABLE assets (
  id           INTEGER PRIMARY KEY,
  abs_path     TEXT NOT NULL UNIQUE,
  source       TEXT NOT NULL,               -- 'inbound'|'scraped'|'generated'|'crop'
  origin_agent TEXT,
  origin_url   TEXT,                        -- for scraped: where Specter found it
  parent_id    INTEGER REFERENCES assets(id),  -- for crops: link back to source frame
  width        INTEGER, height INTEGER,
  bytes        INTEGER,
  sha256       TEXT,
  mtime        REAL,
  created_at   REAL,
  classified_at REAL,
  status       TEXT NOT NULL DEFAULT 'pending',   -- 'pending'|'ok'|'failed'|'rejected'
  error        TEXT
);
CREATE INDEX idx_assets_status ON assets(status);
CREATE INDEX idx_assets_source ON assets(source);
CREATE INDEX idx_assets_sha    ON assets(sha256);

CREATE TABLE attributes (
  asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  source     TEXT NOT NULL DEFAULT 'qwen3-vl',  -- 'qwen3-vl'|'insightface'|'manual'
  PRIMARY KEY (asset_id, key)
);
CREATE INDEX idx_attrs_kv ON attributes(key, value);

CREATE TABLE captions (
  asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  caption  TEXT,
  tags     TEXT,
  model    TEXT
);

CREATE VIRTUAL TABLE assets_fts USING fts5(
  caption, tags, attrs_blob,
  content='', tokenize='porter unicode61'
);

CREATE TABLE crops (
  asset_id  INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  part      TEXT NOT NULL,           -- 'face','eye','hand','foot','torso','leg','lips',...
  bbox_x    INTEGER, bbox_y INTEGER,
  bbox_w    INTEGER, bbox_h INTEGER,
  detector  TEXT
);

CREATE TABLE seed_demand (
  id            INTEGER PRIMARY KEY,
  taxonomy_path TEXT NOT NULL,
  needed        INTEGER NOT NULL DEFAULT 6,
  reason        TEXT,                  -- 'empty'|'thin'|'agent-request'
  requested_at  REAL,
  fulfilled_at  REAL,
  fulfilled_by  TEXT                   -- 'scrape'|'generate'
);

CREATE TABLE gpu_lease (
  gpu         TEXT PRIMARY KEY,        -- 'rtx3070'|'rx6700xt'
  holder      TEXT NOT NULL,
  acquired_at REAL NOT NULL,
  expires_at  REAL NOT NULL,
  purpose     TEXT
);

CREATE TABLE ingest_log (
  id          INTEGER PRIMARY KEY,
  asset_id    INTEGER REFERENCES assets(id),
  step        TEXT NOT NULL,           -- 'ingest'|'classify'|'crop'|'index'|'seed'
  ok          INTEGER NOT NULL,
  duration_ms INTEGER,
  detail      TEXT,
  ts          REAL NOT NULL
);
```

### 4.2 Controlled-vocabulary attributes

| Key | Allowed values | Notes |
|---|---|---|
| `image_type` | `person`, `object`, `scene`, `mixed`, `text`, `meme` | top-level gate |
| `person_count` | `0`, `1`, `2`, `3+` | drives whether person-attrs apply |
| `gender` | `female`, `male`, `androgynous`, `unknown` | per primary subject |
| `age_band` | `child`, `teen`, `young-adult`, `adult`, `senior` | rough only |
| `hair_color` | `blonde`, `brown`, `black`, `red`, `gray`, `dyed-other` | |
| `hair_style` | `long`, `short`, `medium`, `up`, `bald`, `covered` | |
| `build` | `slim`, `athletic`, `average`, `curvy`, `heavy` | |
| `pose` | `standing`, `sitting`, `lying`, `crouching`, `walking`, `dancing`, `action` | |
| `viewpoint` | `front`, `back`, `left-profile`, `right-profile`, `three-quarter`, `top` | |
| `mood` | `neutral`, `smiling`, `serious`, `surprised`, `pensive`, `playful` | |
| `clothing_style` | `casual`, `formal`, `swimwear`, `sportswear`, `lingerie`, `costume`, `none` | |
| `setting` | `indoor`, `outdoor-urban`, `outdoor-nature`, `beach`, `studio`, `vehicle` | |
| `parts_visible` | comma-list: `face,eyes,torso,legs,hands,feet,...` | drives crop pass |
| `nsfw` | `safe`, `suggestive`, `explicit` | controls UI thumbnail blur |

Any extension to this vocabulary is one line in `classifier.py` plus a re-index. No DB migration needed (key/value rows are flexible by design).

### 4.3 Virtual folder taxonomy

Defined in `dashboard/vision/taxonomy.py` as a list of `Node(path, query, children=[...])`:

```python
TAXONOMY = [
    Node("/Inbound",       q={"source":"inbound"}),
    Node("/Generated",     q={"source":"generated"}),
    Node("/Scraped",       q={"source":"scraped"}),

    Node("/Catalogue", children=[
        Node("/Catalogue/People",  q={"image_type":"person"}, children=[
            Node("/Catalogue/People/Female", q={"gender":"female"}, children=[
                Node(".../Blonde",   q={"hair_color":"blonde"}),
                Node(".../Brunette", q={"hair_color":"brown"}),
                # ... auto-generated from controlled vocab
            ]),
            Node("/Catalogue/People/Male",   q={"gender":"male"}, children=[...]),
        ]),
        Node("/Catalogue/Faces",   q={"source":"crop","crops.part":"face"}, children=[
            Node(".../Female",  q={"gender":"female"}, children=[
                Node(".../Eyes", q={"crops.part":"eye"}),
                Node(".../Lips", q={"crops.part":"lips"}),
            ]),
        ]),
        Node("/Catalogue/Body", children=[
            Node(".../Torso",   q={"crops.part":"torso"}),
            Node(".../Hands",   q={"crops.part":"hand"}),
            Node(".../Feet",    q={"crops.part":"foot"}),
            Node(".../Legs",    q={"crops.part":"leg"}),
        ]),
        Node("/Catalogue/Style", children=[
            Node(".../Swimwear",   q={"clothing_style":"swimwear"}),
            Node(".../Formal",     q={"clothing_style":"formal"}),
            Node(".../Sportswear", q={"clothing_style":"sportswear"}),
        ]),
        Node("/Catalogue/Scenes", children=[
            Node(".../Beach",   q={"setting":"beach"}),
            Node(".../Studio",  q={"setting":"studio"}),
            Node(".../Outdoor", q={"setting":"outdoor-nature"}),
        ]),
        Node("/Catalogue/Mood", children=[
            Node(".../Smiling",  q={"mood":"smiling"}),
            Node(".../Pensive",  q={"mood":"pensive"}),
        ]),
    ]),
]
```

Browse query for any node = AND of all ancestor query dicts joined against `assets ⋈ attributes`. Crops appear under multiple bins naturally because they have their own attribute rows inherited from parent (denormalized via trigger at crop-creation time). Adding a node = one line; no migration.

## 5. Pipelines

### 5.1 Ingest

Three sources, all ending in an `assets` row with `status='pending'`:

| Source | Path | Trigger |
|---|---|---|
| Telegram | `artifacts/.private-inbound/<agent>/<file>` | existing — agents call `mark_private(path)`; `vision_engine.observe(path)` is added to that flow |
| Scraped | `artifacts/.vision-scraped/<source>/<date>/<file>` | Specter `--mode seed-fulfill` |
| Generated | `artifacts/.vision-generated/<bin-slug>/<file>` | Specter `--mode seed-fulfill` |

Every ingest computes sha256, dedups (`INSERT OR IGNORE`), and writes an `ingest_log` row. Crops are inserted later by the cropper as `source='crop', parent_id=<frame_asset_id>`.

### 5.2 Classify (`vision_indexer.py`)

Mirrors `image_indexer.py` shape — same systemd timer cadence (30 min), same nice/IO priority, same retry-after cooldown for failed rows, same downscale-to-384 trick.

For each `status='pending'` asset:

1. Downscale image to 384px long edge.
2. POST to Ollama @ 11434 with the structured-JSON prompt (full prompt in `classifier.py`):
   ```
   You are an image cataloguer. Respond with ONLY valid JSON:
   {
     "image_type": "...",
     "person_count": "...",
     ...all controlled-vocab keys...,
     "parts_visible": ["face","eyes","torso","hands","feet"],
     "caption": "<one sentence>",
     "tags": "<12 keywords>"
   }
   ```
3. Parse strictly. On invalid JSON or missing required keys → `status='failed', error=<reason>`, retried after 6h cooldown.
4. UPSERT one `attributes` row per key, one `captions` row per asset.
5. If `parts_visible` non-empty AND `image_type='person'` → enqueue for crop pipeline.
6. UPDATE `assets` set `status='ok', classified_at=now()`.

One inference per image. Cost ~5-30s per image on RX 6700 XT, identical to existing indexer.

### 5.3 Crop (`cropper.py`)

Second pass in same systemd run, after classify. Two detectors:

- **InsightFace SCRFD** for face bboxes (eyes/lips/face-as-whole). Face crop spawns sub-crops for each landmark cluster.
- **qwen3-vl bbox prompt** for non-face parts (hands, feet, torso, legs). Single second prompt per asset asking for bboxes of `parts_visible`. Slower than YOLO but no extra model deps.

For each detected region:
1. Crop image, save JPEG to `artifacts/.vision-crops/<part>/<source-asset-id>_<part>_<n>.jpg`.
2. INSERT child `assets` row, `source='crop'`, `parent_id=<frame>`.
3. INSERT `crops` row with `part`, bbox, detector.
4. `cropper.py` denormalizes inheritable parent attributes onto the child crop in application code at INSERT time (gender, hair_color, age_band, build, mood, nsfw — anything intrinsic to the person rather than the framing). This avoids a 3-table join on every browse query and keeps the rule in Python where it's easy to evolve. Done as code, not a SQL trigger — SQLite triggers chain awkwardly during multi-row inserts.
5. Child enters classify queue itself in next pass — eyes get their own caption ("hazel eyes, pensive expression") and tag set.

YOLO/MediaPipe deferred — InsightFace + qwen-bbox covers v1.

### 5.4 Index

Triggers on `attributes` and `captions` keep `assets_fts` synced. `attrs_blob` column is `"k:v k:v ..."` so a search like `blonde bikini beach` matches caption text + tag string + attribute values in one FTS hit. Attribute filters (`gender:female`) are applied as WHERE clauses post-FTS.

### 5.5 Specter — three modes

Specter is the only agent that writes into the vision tree. Run as long-lived service (`nuc-specter.service`) via:

```
main.py specter --mode seed-scan         # cron, every 6h
main.py specter --mode seed-fulfill      # cron, every 1h
main.py specter --mode seed-fulfill --once  # manual / on-demand from UI
```

**Mode 1 — `seed-scan` (gap detector).** Walks taxonomy. For every leaf node, runs the bin's query and counts `status='ok'` assets. If `count < node.target` (default 6), inserts a `seed_demand` row. Pure SQL, no GPU.

**Mode 2 — `seed-fulfill` (worker).** Picks oldest unfulfilled demand. Strategy by taxonomy path:

| Taxonomy match | Strategy | Reason |
|---|---|---|
| `/Catalogue/Scenes/*` | scrape first, generate fallback | scenery is plentiful + low-risk online |
| `/Catalogue/People/*`, `/Catalogue/Faces/*`, `/Catalogue/Body/*` | **generate only** | scraping real people = legal/privacy minefield |
| `/Catalogue/Style/*` (clothing) | scrape first | catalogues, lookbooks |
| `/Inbound`, `/Generated`, `/Scraped` (top-level) | never seed | source bins, not catalogue bins |

**Scrape sub-mode:** allow-list in `vision/scrape_sources.yaml` — Unsplash, Pexels, Pixabay, Wikimedia v1 only (CC0/CC-BY APIs, not HTML). Per-source rate limit 1 req / 2s. Same qwen3-vl pass classifies before insertion. Origin URL stored. Files land in `.vision-scraped/<source>/<YYYY-MM-DD>/`.

**Generate sub-mode:** acquires `gpu_lease('rtx3070', holder='specter', expires_at=now+10min)`. If lease held by anyone else, skip and requeue. Builds SD Forge prompt by reverse-mapping taxonomy_path (e.g. `/Catalogue/People/Female/Blonde` → `"professional photo of a blonde woman, neutral expression, studio lighting, photorealistic"`). Hardcoded negative prompt for v1 (anatomy + watermark filters). Hits `http://127.0.0.1:11435/sdapi/v1/txt2img`. Image saved to `.vision-generated/<bin-slug>/`. Lease released on completion or expiry. Generated assets enter classify pipeline as normal — Specter's prompt is a hint, the classifier confirms.

**Mode 3 — UI "Fill this folder now".** POST to `/api/vision/specter/seed` with taxonomy path. Inserts `seed_demand` row with `reason='agent-request'`, bumps to front of queue. UI shows toast + ETA.

### 5.6 GPU contention guard

Two GPUs:

| GPU | Owner | Vision use |
|---|---|---|
| AMD RX 6700 XT (Ollama @ 11434) | Ollama qwen3-vl + agents | classify + crop pass — `Nice=15`, low IO priority |
| NVIDIA RTX 3070 (SD Forge @ 11435) | SD WebUI for any agent | Specter generate only, gated by `gpu_lease` |

Lease protocol:
1. Before every txt2img call: `SELECT * FROM gpu_lease WHERE gpu='rtx3070' AND expires_at > now()`.
2. If empty → INSERT lease (10-min TTL) → call → DELETE on completion (or natural expiry on crash).
3. If held → log skip, requeue demand, sleep till next tick.

Existing cron jobs that use the 3070 will get retrofitted to write their own lease as we touch them. v1 ships table + Specter's enforcement only — no retrofit required for shipping.

## 6. Vision UI

### 6.1 Page (`templates/vision.html`)

Three-pane: left tree, center grid, top breadcrumb + search + filters. Vanilla JS + existing dashboard CSS. No SPA framework.

```
┌────────────────────────────────────────────────────────────────────────┐
│ Baza Dash    Home  Agents  Tasks ...  [☀/☾]  12:34  ahb123 ▾          │  ← header (theme toggle)
├────────────┬───────────────────────────────────────────────────────────┤
│            │  Vision ▸ Catalogue ▸ People ▸ Female ▸ Blonde            │
│ FOLDERS    │  ┌──────┬──────┬──────┬──────┬──────┬──────┐  [Search 🔍]│
│ ▸ Inbound  │  │ thumb│ thumb│ thumb│ thumb│ thumb│ thumb│              │
│ ▸ Generated│  └──────┴──────┴──────┴──────┴──────┴──────┘  [Filters]  │
│ ▸ Scraped  │  ┌──────┬──────┬──────┬──────┬──────┬──────┐  gender:f   │
│ ▾ Catalogue│  │      │      │      │      │      │      │  hair:blnd  │
│   ▾ People │  └──────┴──────┴──────┴──────┴──────┴──────┘   12 items  │
│   ▾ Female │  [⟵ prev]  page 1 / 3  [next ⟶]                          │
│   ▸ Blonde │                                                           │
│   ▸ Faces  │  ┌─ This folder is thin (3/6). ─────────────────────┐    │
│   ▸ Body   │  │ [Specter: fill this folder now]  (~5 min via SD) │    │
│ ▸ Inbox⚠   │  └───────────────────────────────────────────────────┘    │
└────────────┴───────────────────────────────────────────────────────────┘
```

Click thumb → modal: full image, all attributes, source, origin, "edit attributes" (manual override stored as `attribute.source='manual'`, confidence 1.0, beats classifier). Inbox icon flashes when classify queue depth > 0.

### 6.2 API endpoints

All gated by `_is_private_unlocked()`.

| Method | Path | Purpose | Returns |
|---|---|---|---|
| GET | `/api/vision/tree` | Full taxonomy with per-node counts | `{tree:[{path,label,count,children:[...]}, ...]}` |
| GET | `/api/vision/browse?path=...&page=1&limit=60` | Assets in node | `{node, assets:[...], total, page, pages}` |
| GET | `/api/vision/search?q=...&limit=60` | FTS5 + attribute query | `{assets:[...], total}` |
| GET | `/api/vision/asset/<id>` | Detail | `{asset, attributes:{}, crops:[...], parent}` |
| POST | `/api/vision/asset/<id>/attributes` | Manual override | `{ok, asset_id}` |
| POST | `/api/vision/specter/seed` `{path}` | Trigger fill-this-folder | `{ok, demand_id, eta_seconds}` |

Asset thumbnails served by extending the existing `/datahub/private/serve/<token>` route to handle `vision-*` subdirs. No new image-serving code.

### 6.3 Privacy

Same passphrase gate as `/datahub/private` today. Old `/datahub/private` becomes a 302 to `/vision`. Left-nav label changes from "Private" to "Vision". The toggle the user mentioned is the **theme toggle** in the header; the Private→Vision rename is permanent — Vision UI subsumes Private UI.

## 7. Theme toggle (separate, ships first)

Smallest possible implementation, ~50 lines total:

1. **`templates/_layout.html`** — `<html data-theme="{{ session.get('theme','dark') }}">`. Header button next to clock: sun icon in dark mode, moon in light.
2. **`static/css/theme.css`** — extract every hardcoded color in existing CSS into custom properties (`--bg`, `--bg-elev`, `--fg`, `--fg-dim`, `--accent`, `--border`, `--danger`, `--ok`, `--warn`). Two `:root` blocks: `[data-theme=dark]` (current values) and `[data-theme=light]` (inverted).
3. **`app.py`** — `@app.route('/settings/theme', methods=['POST'])` stores in `session['theme']` + sets a `theme=` cookie with 1y TTL. Accepts `?theme=` query for shareable links.
4. **`static/js/theme.js`** — click handler, optimistic toggle of `data-theme` attribute, POST in background.

Ships as PR 1, before any Vision work. Tiny, low-risk, validates the dev/deploy loop before the larger changes.

## 8. Build sequence

Each PR independently shippable.

1. **PR 1** — Theme toggle. ~2 hours.
2. **PR 2** — `vision.db` schema + `vision/migrate_existing.py` backfill (creates `assets` rows with `status='pending'` for everything already on disk; no classification yet).
3. **PR 3** — Classifier + `vision_indexer.py` + `baza-vision-indexer.service` + `.timer` (cloned from `baza-image-indexer.*`). On first run, processes the PR-2 backlog at 5-30s/image.
4. **PR 4** — Cropper. InsightFace deps if not already present; integrate as second pass.
5. **PR 5** — Vision UI page + API. New blueprint `dashboard/vision_routes.py`, `templates/vision.html`, six endpoints. `/datahub/private` redirects to `/vision`. Left-nav rename.
6. **PR 6** — Specter `seed-scan` + UI fill button (modes 1 + 3). No scrape/generate yet — just the demand ledger + UI feedback. Validates the loop.
7. **PR 7** — Specter generate mode (SD Forge integration, GPU lease, prompt mapping). v1 reference-retrieval is fully shippable without this if we want to sanity-check classification first.
8. **PR 8** — Specter scrape mode. Allow-list of CC0/CC-BY APIs only. Last because most likely to need iteration.

## 9. Files

**New:**
```
dashboard/vision/__init__.py
dashboard/vision/engine.py
dashboard/vision/classifier.py
dashboard/vision/cropper.py
dashboard/vision/taxonomy.py
dashboard/vision/search.py
dashboard/vision/specter_seeder.py
dashboard/vision/scrape_sources.yaml
dashboard/vision/migrate_existing.py
dashboard/vision_routes.py
dashboard/vision.db                   (gitignored, created by migrate)
dashboard/vision_indexer.py
dashboard/templates/vision.html
dashboard/static/js/vision.js
dashboard/static/js/theme.js
dashboard/static/css/theme.css
baza-vision-indexer.service
baza-vision-indexer.timer
```

**Modified:**
```
dashboard/app.py                      (register vision blueprint, theme route, /datahub/private→/vision redirect, left-nav rename)
dashboard/templates/_layout.html      (data-theme attr + toggle button)
dashboard/static/css/*.css            (extract colors → custom props)
.gitignore                            (vision.db, .vision-* artifact subdirs)
agent-framework-v3/scripts/           (Specter mode registration if applicable)
```

**Untouched:**
```
image_indexer.py                      (public corpus, unchanged behavior)
private_inbound.py                    (capture path unchanged)
nuc-specter.service                   (only main.py specter modes change)
existing /datahub/private endpoints   (become redirects)
```

## 10. Deploy

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now baza-vision-indexer.timer
# dashboard reload via existing flow (likely systemctl restart baza-dashboard.service)
```

No new ports, no new firewall rules.

## 11. Rollback

Each PR reverted by single `git revert`. `vision.db` is a separate file — deleting loses derived data only, not source images. `.vision-*` artifact subdirs deletable independently of `.private-inbound/`. Worst case:

```bash
sudo systemctl disable --now baza-vision-indexer.timer
rm dashboard/vision.db
rm -rf dashboard/artifacts/.vision-*
git revert <PR commits>
```

## 12. Testing

- **Unit:** `taxonomy.py` query composition, `classifier.py` JSON parsing (good + malformed inputs), `cropper.py` bbox math, `gpu_lease` acquire/release race.
- **Integration:** end-to-end ingest of 5 sample images through classify + crop + index; verify FTS query "blonde beach" returns expected assets; verify "fill this folder" produces a `seed_demand` row.
- **Manual:** browse `/vision` after backfill, click through `/Catalogue/People/Female`, edit one asset's attributes, verify it moves bins.
- **Load:** classify 100 backlog images, confirm <1% failed, mean latency <30s on RX 6700 XT, no agent inference disruption (watch `nvidia-smi`/`rocm-smi` during run).

## 13. Roadmap (out of scope for v1)

- **v2 — Generation conditioning:** SD Forge IP-Adapter + ControlNet pipeline. Catalogue assets become visual conditioning for new generations. Cropped face/style/scene assets feed the engine so generated images match the library's look.
- **v3 — LoRA training corpus:** when bins reach N items (e.g. 30 per category), Specter triggers a LoRA training job. Resulting LoRAs get loaded into SD Forge automatically. Per-bin LoRAs combinable at generation time.
- **Mobile/tablet Vision UI** (existing `mobile.html` is a separate beast).
- **Telegram chat queries** ("specter, find me a blonde at the beach" → returns thumbnails inline).
- **Multi-Baza federation** of vision.db.

## 14. Hardware aside (LoRa ESP32-S3 nodes)

User asked whether 5 LoRa ESP32-S3 nodes would speed up cataloguing. They wouldn't — ingestion bottleneck is GPU inference, not capture or parallelism. ESP32-S3 has ~512KB SRAM and no GPU; LoRa radio is kbps, not the Mbps required for image transport. Where ESP32-S3 nodes (specifically S3-CAM modules **over WiFi, not LoRa**) would help: as additional inbound sources growing the corpus, not as accelerators. Path to faster cataloguing: smaller/faster vision model (moondream2), batched calls, or letting Specter use the idle RTX 3070 in parallel with the RX 6700 XT for indexing.
