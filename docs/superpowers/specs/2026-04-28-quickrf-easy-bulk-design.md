# QuickRF Easy Bulk Filing — Design

**Date:** 2026-04-28
**Owner:** Serge (AHB123)
**Status:** Approved (brainstorm); awaits implementation plan

## Goal

Make Quick RF receipt filing in the AHB123 dashboard "top-of-the-line" for the user's actual workflow: snap photos of receipts (sometimes one per image, sometimes two side-by-side), select all of them at once, and have the system queue every individual receipt as its own card with a correctly-cropped thumbnail visible before OCR finishes.

The current implementation has three upload buttons (Single / Dual / Bulk), a brittle landscape-ratio detector, and a naive 50/50 split that fails when the two receipts aren't equal width or when phone EXIF orientation is wrong. This spec replaces that with one button, a reliable detector, a smarter split, and a per-card recovery toolset.

## Non-Goals

- More than 2 receipts per image (user confirmed: only 1-up or 2-up).
- Top/bottom stacked dual layout (user confirmed: always side-by-side).
- Receipts at angles, on cluttered backgrounds, or anything requiring full contour detection / ML — out of scope.
- Replacing OCR or the post-confirmation receipt model.
- Touching `Re-Scan Existing`.

## User-Visible Changes

### Upload row (template `ahb123.html`, RECEIPTS tab)

The four-tile row (Single / Dual / Bulk / Re-Scan) becomes a two-tile row:

| Tile | Behavior |
|------|----------|
| **📦 Easy Bulk Upload** | Multi-select images; backend auto-detects 1-up vs 2-up per image and crops accordingly. |
| **🔍 Re-Scan Existing** | Unchanged. |

### Queue card additions

Existing actions stay: ✂ Split, 🔁 Rescan, 📂 File, 🗑 Erase. Three new actions are added per card:

- **🔄 Rotate 90°** — rotates the source image clockwise, re-runs orientation/split detection from scratch, re-queues OCR.
- **⇆ Merge** — only shown on cards whose `mode` indicates they are one half of an auto-split pair (e.g. `bulk-dual-left`, `bulk-dual-right`). Merging joins the two halves back into a single 1-up queue item, deletes the sibling, re-runs OCR.
- **⇔ Adjust Split** — only shown on auto-split halves. Opens a small modal with the original landscape image and a draggable vertical line (initialized at the auto-detected column). On confirm, both halves are re-cropped at the new column and re-OCRed.

The queue card thumbnail still loads from `/api/ahb/receipts/queue/image/<qid>` and is visible immediately after upload completes — OCR fields populate later.

## Architecture

### Component map

```
┌─ Frontend (ahb123.html) ─────────────────────────────────────────┐
│  submitReceiptsToQueue(files)   ← single entry point             │
│      → POST /api/ahb/receipts/process (mode='easy_bulk')         │
│  renderReceiptQueue()           ← adds rotate/merge/adjust btns  │
│  rqRotate(qid)                                                   │
│  rqMerge(qid)                                                    │
│  rqOpenAdjustSplit(qid) → modal with draggable line              │
│  rqApplyAdjustSplit(qid, splitCol)                               │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─ Backend (dashboard/app.py) ─────────────────────────────────────┐
│  api_ahb_receipts_process()                                      │
│      → for each file: _detect_and_queue(image)                   │
│  _detect_and_queue(file_storage):                                │
│      img = exif_transpose(Image.open(file))                      │
│      if H >= W: queue as 1-up (mode='bulk-single')               │
│      else:                                                       │
│          col = _find_split_column(img)                           │
│          left,right = img.crop(...) at col                       │
│          queue both (mode='bulk-dual-left'/'bulk-dual-right',    │
│                      pair_id=<uuid>, parent_image_path=<orig>)   │
│  _find_split_column(img) → int                                   │
│      gray = ImageOps.grayscale(img)                              │
│      mid_band = central 60% of width                             │
│      col_means = mean brightness per column in mid_band          │
│      return argmax(col_means)  # the brightest = whitespace gap  │
│      (fallback midpoint if max-min spread < threshold)           │
│  api_ahb_receipts_queue_<qid>_rotate()                           │
│  api_ahb_receipts_queue_<qid>_merge()                            │
│  api_ahb_receipts_queue_<qid>_adjust_split(split_col)            │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─ DB (ahb_receipt_queue, SQLite) ─────────────────────────────────┐
│  Existing cols: id, image_path, mode, status, result_json, ...   │
│  NEW cols (additive, nullable):                                  │
│    parent_image_path TEXT  -- original (un-cropped) image        │
│    pair_id           TEXT  -- shared by two halves of a split    │
│    split_col         INT   -- column where split was made        │
└──────────────────────────────────────────────────────────────────┘
```

### Data flow — Easy Bulk Upload happy path

1. User selects N images in browser (mix of portrait/landscape).
2. Frontend POSTs files to `/api/ahb/receipts/process` with `mode=easy_bulk`.
3. Backend, per file:
   a. EXIF-transpose the image so its pixels match its visual orientation.
   b. If H ≥ W: save as 1-up; insert one queue row with `mode='bulk-single'`.
   c. If H < W: find the brightest vertical column in the middle 60%; crop into left + right halves; save both crops + the original (under `parent_image_path`); insert two queue rows sharing a `pair_id` and recording `split_col`.
4. Background OCR worker drains pending items as it does today.
5. Frontend polls `/api/ahb/receipts/queue` every 3s; renders cards with thumbnail (from cropped image) immediately, fields appear once OCR finishes.

### Data flow — recovery actions

- **Rotate 90°**: backend rotates `parent_image_path` (or `image_path` for 1-ups), re-runs `_detect_and_queue` on the rotated image, deletes the old queue row(s) (and sibling if part of a pair), inserts new row(s). Front end refresh shows new card(s).
- **Merge**: backend looks up the sibling via `pair_id`, marks both rows `rejected`, inserts a fresh 1-up row using `parent_image_path`, kicks the OCR worker.
- **Adjust Split**: backend re-crops `parent_image_path` at the user-supplied `split_col`, overwrites both halves' image files in place, resets both rows to `pending`, kicks the OCR worker.

### Modules touched

| File | Change |
|------|--------|
| `dashboard/app.py` | Replace `_split_dual` / `_save_single` logic with `_detect_and_queue` + `_find_split_column`; add three new endpoints (rotate, merge, adjust-split); add 3 nullable cols to `ahb_receipt_queue` via idempotent ALTER. |
| `dashboard/templates/ahb123.html` | Replace the upload-tiles row with a single Easy Bulk tile + Re-Scan; add `rqRotate`, `rqMerge`, `rqOpenAdjustSplit`, `rqApplyAdjustSplit` JS; render the three new buttons on relevant cards. |

No new Python or JS dependencies. PIL/Pillow is already imported.

## Algorithm: split-column detection

```python
def _find_split_column(img):
    """Return the x-column index that best separates two side-by-side receipts.
    Strategy: brightest column in the middle 60% of width is the white gap.
    Falls back to image midpoint when no clear valley exists."""
    gray = img.convert('L')          # luminance
    w, h = gray.size
    pixels = gray.load()
    # column means for the middle 60% of width
    lo, hi = int(w * 0.20), int(w * 0.80)
    col_means = []
    for x in range(lo, hi):
        s = sum(pixels[x, y] for y in range(0, h, max(1, h // 64)))  # subsample rows
        col_means.append((x, s))
    if not col_means:
        return w // 2
    spread = max(m for _, m in col_means) - min(m for _, m in col_means)
    if spread < 20:                  # no clear valley; receipts may overlap or be similar
        return w // 2
    return max(col_means, key=lambda t: t[1])[0]
```

Cheap enough to run inline (no thread). Numpy speeds it up by ~10× — use it if it's already imported, otherwise the pure-PIL form is fine for the ~1000-pixel-wide phone images we see.

## Error handling

- **Corrupt image / PIL can't open**: catch `UnidentifiedImageError`, fall back to saving raw bytes as a 1-up `bulk-fallback` queue row (mirrors current behavior).
- **EXIF-transpose throws**: ignore and continue with the raw orientation.
- **Empty file selection**: existing 400 response unchanged.
- **Adjust-split with `split_col` out of range**: clamp to `[1, w-1]`.
- **Merge on an item that has no sibling**: 400 with explanatory error.
- **Rotate on an item whose image file is missing**: 410 Gone, with hint to erase the queue item.

## Testing

A small test surface — most logic is image processing.

- **Unit (`tests/test_split_detection.py`)**:
  - Synthetic landscape image with clean white center → `_find_split_column` returns ~midpoint.
  - Synthetic landscape with off-center gap (left receipt 60%, right 40%) → returns column inside the gap, not midpoint.
  - Synthetic landscape with no whitespace (uniform noise) → returns midpoint (fallback path).
  - Portrait image → orientation detector classifies as 1-up (no split call).
- **Integration (manual checklist after deploy):**
  - Upload 3 mixed images (1 portrait single, 1 clean landscape dual, 1 sideways single) → 4 queue cards (1 + 2 + 1 wrong split).
  - On the wrong-split case: Rotate 90° → reduces to 1 card. Or Merge → reduces to 1 card. Verify OCR re-runs.
  - On the clean dual: open Adjust Split, drag the line, confirm → both halves re-cropped, OCR re-runs.

## Migration

The DB schema gets three nullable columns added via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` style logic (SQLite needs `try/except` around bare ALTERs). Existing queue rows continue to work — `parent_image_path` and `pair_id` are NULL for them, which simply means Merge / Adjust Split aren't shown on those cards. Rotate works on any row.

No data backfill needed.

## Rollout

Single deploy:

1. Apply schema additions on app startup (existing pattern in `_ahb_db()` migrations).
2. Replace the upload-tiles HTML, the JS upload handler, the queue card renderer, and the backend `process` route in one commit.
3. Restart the dashboard service (`baza-dashboard.service`).
4. Smoke-test with 3 mixed images per the integration checklist above.

No feature flag — the surface is small and the failure mode is "user falls back to manually erasing wrong cards," which they already have.
