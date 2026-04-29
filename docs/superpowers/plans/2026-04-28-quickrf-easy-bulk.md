# QuickRF Easy Bulk Filing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current Single / Dual / Bulk receipt upload tiles with one "Easy Bulk Upload" tile that auto-detects 1-up vs 2-up images (via EXIF-aware orientation), splits 2-up landscape images at the brightest column ("white valley") instead of 50/50, and gives the user per-card recovery actions (Rotate 90°, Merge, Adjust Split).

**Architecture:** All backend changes live in `dashboard/app.py` (existing pattern — receipts code is already there). All frontend changes live in `dashboard/templates/ahb123.html`. SQLite migration uses the existing idempotent `alter_stmts` list pattern. Tests for the pure-image detection function go in a new `tests/` directory.

**Tech Stack:** Python 3.12, Flask, Pillow (PIL), SQLite, vanilla HTML/CSS/JS in Jinja2 template. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-28-quickrf-easy-bulk-design.md`

---

## Pre-flight

- [ ] **Read the spec end-to-end**: `docs/superpowers/specs/2026-04-28-quickrf-easy-bulk-design.md`
- [ ] **Activate venv**: `source /home/switchhacker/baza-empire/agent-framework-v3/venv/bin/activate`
- [ ] **Verify pytest available**: `python -c "import pytest; print(pytest.__version__)"` — install with `pip install pytest` if missing.
- [ ] **Confirm dashboard service runs**: `systemctl status baza-dashboard.service` (Active: running expected).

---

## Task 1: Database schema additions

**Files:**
- Modify: `dashboard/app.py:377-422` (the `alter_stmts` list inside `init_db()` / `_ahb_init`)

The new columns are nullable so existing rows keep working. The pattern is the same idempotent try/except already used for every other ALTER in this list.

- [ ] **Step 1: Add three ALTER statements**

In `dashboard/app.py`, find the `alter_stmts = [...]` list (currently ends with `commission_beneficiary` around line 421) and append three new entries before the closing `]`:

```python
        "ALTER TABLE ahb_receipt_queue ADD COLUMN parent_image_path TEXT",
        "ALTER TABLE ahb_receipt_queue ADD COLUMN pair_id TEXT",
        "ALTER TABLE ahb_receipt_queue ADD COLUMN split_col INTEGER",
```

- [ ] **Step 2: Restart dashboard so migrations run**

Run: `sudo systemctl restart baza-dashboard.service`
Expected: returns immediately with no output. Then:
Run: `sudo systemctl status baza-dashboard.service --no-pager | head -20`
Expected: `Active: active (running)`.

- [ ] **Step 3: Verify columns exist**

Run:
```bash
sqlite3 /home/switchhacker/baza-empire/agent-framework-v3/dashboard/baza_projects.db \
  "PRAGMA table_info(ahb_receipt_queue);"
```
Expected: output lists `parent_image_path`, `pair_id`, `split_col` among the columns.

- [ ] **Step 4: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/app.py
git commit -m "feat(quickrf): add parent_image_path, pair_id, split_col to receipt queue"
```

---

## Task 2: `_find_split_column` helper with tests (TDD)

**Files:**
- Create: `tests/__init__.py` (empty file)
- Create: `tests/test_split_detection.py`
- Modify: `dashboard/app.py` — add helper function near the existing receipt helpers (insert right above `@app.route('/api/ahb/receipts/process'...)`, around line 7080)

This is the only piece with non-trivial logic, so it gets full TDD.

- [ ] **Step 1: Create empty `tests/__init__.py`**

```bash
mkdir -p /home/switchhacker/baza-empire/agent-framework-v3/tests
touch /home/switchhacker/baza-empire/agent-framework-v3/tests/__init__.py
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_split_detection.py` with this exact content:

```python
"""Tests for the QuickRF receipt split-column detection algorithm."""
from PIL import Image, ImageDraw
import sys
import os

# Make dashboard importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'dashboard'))

from app import _find_split_column


def _make_two_receipts(left_w, gap_w, right_w, h=200, gap_brightness=255, receipt_brightness=80):
    """Synthesize a landscape image: dark receipt | bright gap | dark receipt."""
    w = left_w + gap_w + right_w
    img = Image.new('RGB', (w, h), color=(receipt_brightness,) * 3)
    draw = ImageDraw.Draw(img)
    # paint the gap brighter
    draw.rectangle([left_w, 0, left_w + gap_w, h], fill=(gap_brightness,) * 3)
    return img


def test_clean_centered_gap_returns_column_inside_gap():
    img = _make_two_receipts(left_w=400, gap_w=40, right_w=400)
    col = _find_split_column(img)
    # gap occupies x=[400, 440); allow ±2 for subsampling
    assert 398 <= col <= 442, f"expected col inside gap, got {col}"


def test_off_center_gap_returns_column_inside_off_center_gap():
    # left receipt is wider; gap is at x=[600, 640)
    img = _make_two_receipts(left_w=600, gap_w=40, right_w=300)
    col = _find_split_column(img)
    assert 598 <= col <= 642, f"expected col inside off-center gap, got {col}"


def test_no_clear_valley_falls_back_to_midpoint():
    # uniform-noise-ish image (no bright gap) → fallback to midpoint
    import random
    random.seed(0)
    w, h = 800, 200
    img = Image.new('RGB', (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            v = 80 + random.randint(-5, 5)  # tight range, no spread
            px[x, y] = (v, v, v)
    col = _find_split_column(img)
    assert col == w // 2, f"expected midpoint fallback {w // 2}, got {col}"


def test_search_band_excludes_image_edges():
    # bright stripe at far-left edge should NOT be picked (edges excluded)
    img = Image.new('RGB', (800, 200), color=(80, 80, 80))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 50, 200], fill=(255, 255, 255))   # bright at left edge
    draw.rectangle([395, 0, 405, 200], fill=(255, 255, 255))  # gap in middle
    col = _find_split_column(img)
    assert 393 <= col <= 407, f"expected center gap (393-407), got {col}"
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
source venv/bin/activate
python -m pytest tests/test_split_detection.py -v
```
Expected: `ImportError` for `_find_split_column` (or all 4 tests FAIL with `ImportError`).

- [ ] **Step 4: Implement `_find_split_column`**

In `dashboard/app.py`, locate the existing `@app.route('/api/ahb/receipts/process', methods=['POST'])` decorator (around line 7080) and insert ABOVE it (between any preceding helper and the decorator):

```python
def _find_split_column(img):
    """Return the x-column index that best separates two side-by-side receipts.
    Strategy: find the brightest column (white valley) in the middle 60% of width.
    Falls back to image midpoint when no clear valley exists.

    img: PIL.Image (any mode; will be converted to L)
    returns: int column index in [0, w)
    """
    gray = img.convert('L')
    w, h = gray.size
    if w < 4:
        return w // 2
    pixels = gray.load()
    lo = int(w * 0.20)
    hi = int(w * 0.80)
    if hi <= lo:
        return w // 2
    # Subsample rows to keep this cheap on big images.
    row_step = max(1, h // 64)
    col_means = []
    for x in range(lo, hi):
        s = 0
        n = 0
        for y in range(0, h, row_step):
            s += pixels[x, y]
            n += 1
        col_means.append((x, s / max(1, n)))
    if not col_means:
        return w // 2
    vals = [m for _, m in col_means]
    spread = max(vals) - min(vals)
    if spread < 20:  # no clear bright/dark contrast = no obvious gap
        return w // 2
    return max(col_means, key=lambda t: t[1])[0]
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
python -m pytest tests/test_split_detection.py -v
```
Expected: `4 passed`.

- [ ] **Step 6: Commit**

```bash
git add tests/__init__.py tests/test_split_detection.py dashboard/app.py
git commit -m "feat(quickrf): add _find_split_column with white-valley detection + tests"
```

---

## Task 3: `_detect_and_queue` helper

**Files:**
- Modify: `dashboard/app.py` — insert right after `_find_split_column` (the function added in Task 2)

This is the orchestrator: takes a single uploaded `FileStorage`, EXIF-transposes, decides 1-up vs 2-up, crops, inserts queue rows, returns the new qids. Pulled out of the route so it's testable and reusable from the rotate/merge/adjust handlers later.

- [ ] **Step 1: Add the helper**

Insert this function in `dashboard/app.py` directly below `_find_split_column`:

```python
def _detect_and_queue(file_storage, conn, queue_dir):
    """Detect 1-up vs 2-up, crop accordingly, insert queue row(s).
    Returns list of new queue ids. Caller is responsible for conn.commit().

    file_storage: werkzeug FileStorage from request.files
    conn:         sqlite3 connection (open, autocommit off)
    queue_dir:    absolute path where crops are saved
    """
    from PIL import Image, ImageOps, UnidentifiedImageError
    safe_name = re.sub(r'[^\w.\-]', '_', file_storage.filename or 'receipt.jpg')

    # Try to decode + EXIF-rotate. Fall back to raw bytes if PIL chokes.
    try:
        img = Image.open(file_storage.stream)
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        img = img.convert('RGB')
    except UnidentifiedImageError:
        # Save raw bytes as a fallback 1-up
        file_storage.stream.seek(0)
        qid = str(uuid.uuid4())
        fpath = os.path.join(queue_dir, f"{qid}_{safe_name}")
        file_storage.save(fpath)
        conn.execute(
            "INSERT INTO ahb_receipt_queue (id, image_path, mode, status) "
            "VALUES (?, ?, 'bulk-fallback', 'pending')",
            (qid, fpath))
        return [qid]

    w, h = img.size

    # Portrait (or square) → 1-up
    if h >= w:
        qid = str(uuid.uuid4())
        fpath = os.path.join(queue_dir, f"{qid}.jpg")
        img.save(fpath, 'JPEG', quality=90)
        conn.execute(
            "INSERT INTO ahb_receipt_queue (id, image_path, mode, status) "
            "VALUES (?, ?, 'bulk-single', 'pending')",
            (qid, fpath))
        return [qid]

    # Landscape → 2-up. Save the original under parent_image_path for recovery.
    pair_id = str(uuid.uuid4())
    parent_fpath = os.path.join(queue_dir, f"{pair_id}_parent.jpg")
    img.save(parent_fpath, 'JPEG', quality=90)
    split_col = _find_split_column(img)
    new_ids = []
    for side, box in [('left', (0, 0, split_col, h)),
                      ('right', (split_col, 0, w, h))]:
        qid = f"{pair_id}-{side}"
        fpath = os.path.join(queue_dir, f"{qid}.jpg")
        img.crop(box).save(fpath, 'JPEG', quality=90)
        conn.execute(
            "INSERT INTO ahb_receipt_queue "
            "(id, image_path, mode, status, parent_image_path, pair_id, split_col) "
            "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (qid, fpath, f'bulk-dual-{side}', parent_fpath, pair_id, split_col))
        new_ids.append(qid)
    return new_ids
```

- [ ] **Step 2: Smoke-test by importing**

Run:
```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
source venv/bin/activate
python -c "import sys; sys.path.insert(0, 'dashboard'); from app import _detect_and_queue; print('ok')"
```
Expected: `ok` (no traceback).

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py
git commit -m "feat(quickrf): add _detect_and_queue orchestrator (EXIF + smart split + DB insert)"
```

---

## Task 4: Rewrite `/api/ahb/receipts/process` to Easy Bulk

**Files:**
- Modify: `dashboard/app.py:7080-7167` (the existing `api_ahb_receipts_process` function)

The route gets simpler: ignore the `mode` form field, run every file through `_detect_and_queue`. Keep the `_spawn_receipt_queue_worker()` kick.

- [ ] **Step 1: Replace the function body**

In `dashboard/app.py`, locate the function `def api_ahb_receipts_process()` (decorated with `@app.route('/api/ahb/receipts/process', methods=['POST'])`, around line 7081). Replace the **entire function body** (from `def api_ahb_receipts_process():` down to and including the `except Exception as e:` block ending at `return jsonify({'success': False, 'error': str(e)}), 500`) with:

```python
def api_ahb_receipts_process():
    """Easy Bulk receipt upload. Per file: EXIF-transpose, then portrait → 1-up,
    landscape → 2-up split at the brightest column (white valley between receipts).
    Queue cards appear immediately with cropped thumbnails; OCR drains in background."""
    try:
        files = request.files.getlist('files') or [request.files.get('file')]
        files = [f for f in files if f]
        if not files:
            return jsonify({'success': False, 'error': 'No files uploaded'}), 400

        queue_dir = os.path.join(DASHBOARD_DIR, 'uploads', 'ahb', 'receipts', 'queue')
        os.makedirs(queue_dir, exist_ok=True)
        conn = _ahb_db()
        queue_ids = []
        for f in files:
            try:
                queue_ids.extend(_detect_and_queue(f, conn, queue_dir))
            except Exception as _e:
                # Per-file failure shouldn't kill the batch; record it as an error row.
                qid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO ahb_receipt_queue (id, image_path, mode, status, error) "
                    "VALUES (?, '', 'bulk-error', 'error', ?)",
                    (qid, f"upload failed: {str(_e)[:200]}"))
                queue_ids.append(qid)
        conn.commit()
        conn.close()
        _spawn_receipt_queue_worker()
        return jsonify({'success': True, 'queue_ids': queue_ids, 'count': len(queue_ids)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
```

- [ ] **Step 2: Restart and smoke-test**

```bash
sudo systemctl restart baza-dashboard.service
sleep 2
curl -s http://localhost:8888/api/ahb/receipts/queue | head -c 200
```
Expected: a JSON array (existing items) — no 500, no traceback.

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py
git commit -m "refactor(quickrf): rewrite /receipts/process as Easy Bulk via _detect_and_queue"
```

---

## Task 5: `/queue/<qid>/rotate` endpoint

**Files:**
- Modify: `dashboard/app.py` — insert directly below the existing `api_ahb_receipts_queue_rescan` function (around line 7400)

Rotate the source image 90° clockwise, re-run `_detect_and_queue` on the rotated image, mark the old row(s) rejected.

- [ ] **Step 1: Add the route**

```python
@app.route('/api/ahb/receipts/queue/<qid>/rotate', methods=['POST'])
def api_ahb_receipts_queue_rotate(qid):
    """Rotate the source image 90° CW and re-detect. Replaces the old queue row(s)."""
    try:
        from PIL import Image
        conn = _ahb_db()
        row = conn.execute(
            "SELECT image_path, parent_image_path, pair_id FROM ahb_receipt_queue WHERE id=?",
            (qid,)).fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': 'queue item not found'}), 404
        # If this is half of a split pair, rotate the parent and recreate both halves.
        # If it's a single 1-up, rotate its own image.
        src = row['parent_image_path'] or row['image_path']
        if not src or not os.path.exists(src):
            conn.close()
            return jsonify({'success': False, 'error': 'source image missing'}), 410

        queue_dir = os.path.join(DASHBOARD_DIR, 'uploads', 'ahb', 'receipts', 'queue')
        os.makedirs(queue_dir, exist_ok=True)

        rotated = Image.open(src).rotate(-90, expand=True).convert('RGB')
        # Save rotated to a tmp file and feed through _detect_and_queue via a fake FileStorage.
        from io import BytesIO
        from werkzeug.datastructures import FileStorage
        buf = BytesIO()
        rotated.save(buf, 'JPEG', quality=90)
        buf.seek(0)
        fs = FileStorage(stream=buf, filename=f"rotated_{qid}.jpg", content_type='image/jpeg')
        new_ids = _detect_and_queue(fs, conn, queue_dir)

        # Reject old row(s): if pair, reject both halves; otherwise just this one.
        if row['pair_id']:
            conn.execute("UPDATE ahb_receipt_queue SET status='rejected' WHERE pair_id=?",
                         (row['pair_id'],))
        else:
            conn.execute("UPDATE ahb_receipt_queue SET status='rejected' WHERE id=?", (qid,))
        conn.commit()
        conn.close()
        _spawn_receipt_queue_worker()
        return jsonify({'success': True, 'queue_ids': new_ids})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
```

- [ ] **Step 2: Restart and smoke-check the route exists**

```bash
sudo systemctl restart baza-dashboard.service
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8888/api/ahb/receipts/queue/__bogus__/rotate
```
Expected: `404` (route exists, item not found).

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py
git commit -m "feat(quickrf): add /queue/<qid>/rotate endpoint (90° CW + re-detect)"
```

---

## Task 6: `/queue/<qid>/merge` endpoint

**Files:**
- Modify: `dashboard/app.py` — insert directly below the rotate handler from Task 5

For a card whose `pair_id` is set: reject both halves, save the parent image as a fresh 1-up, queue OCR.

- [ ] **Step 1: Add the route**

```python
@app.route('/api/ahb/receipts/queue/<qid>/merge', methods=['POST'])
def api_ahb_receipts_queue_merge(qid):
    """Merge two halves of an auto-split pair back into a single 1-up queue item."""
    try:
        conn = _ahb_db()
        row = conn.execute(
            "SELECT pair_id, parent_image_path FROM ahb_receipt_queue WHERE id=?",
            (qid,)).fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': 'queue item not found'}), 404
        if not row['pair_id'] or not row['parent_image_path']:
            conn.close()
            return jsonify({'success': False, 'error': 'item is not a split half'}), 400
        if not os.path.exists(row['parent_image_path']):
            conn.close()
            return jsonify({'success': False, 'error': 'parent image missing'}), 410

        # Reject both halves
        conn.execute("UPDATE ahb_receipt_queue SET status='rejected' WHERE pair_id=?",
                     (row['pair_id'],))

        # Save parent as a fresh 1-up
        queue_dir = os.path.join(DASHBOARD_DIR, 'uploads', 'ahb', 'receipts', 'queue')
        os.makedirs(queue_dir, exist_ok=True)
        new_qid = str(uuid.uuid4())
        new_fpath = os.path.join(queue_dir, f"{new_qid}.jpg")
        # Hard-link or copy the parent so the parent stays usable for any other recovery.
        import shutil
        shutil.copy2(row['parent_image_path'], new_fpath)
        conn.execute(
            "INSERT INTO ahb_receipt_queue (id, image_path, mode, status) "
            "VALUES (?, ?, 'bulk-merged', 'pending')",
            (new_qid, new_fpath))
        conn.commit()
        conn.close()
        _spawn_receipt_queue_worker()
        return jsonify({'success': True, 'queue_id': new_qid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
```

- [ ] **Step 2: Smoke-check**

```bash
sudo systemctl restart baza-dashboard.service
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8888/api/ahb/receipts/queue/__bogus__/merge
```
Expected: `404`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py
git commit -m "feat(quickrf): add /queue/<qid>/merge endpoint (rejoin auto-split pair)"
```

---

## Task 7: `/queue/<qid>/adjust-split` endpoint

**Files:**
- Modify: `dashboard/app.py` — insert directly below the merge handler from Task 6

Re-crop both halves of a pair at a user-supplied column, overwrite both halves' image files, reset to pending, kick OCR.

- [ ] **Step 1: Add the route**

```python
@app.route('/api/ahb/receipts/queue/<qid>/adjust-split', methods=['POST'])
def api_ahb_receipts_queue_adjust_split(qid):
    """Re-crop both halves of an auto-split pair at a new split column.
    Body: {"split_col": <int>}"""
    try:
        from PIL import Image
        data = request.json or {}
        try:
            split_col = int(data.get('split_col', 0))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'split_col must be an integer'}), 400

        conn = _ahb_db()
        row = conn.execute(
            "SELECT pair_id, parent_image_path FROM ahb_receipt_queue WHERE id=?",
            (qid,)).fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': 'queue item not found'}), 404
        if not row['pair_id'] or not row['parent_image_path']:
            conn.close()
            return jsonify({'success': False, 'error': 'item is not a split half'}), 400
        if not os.path.exists(row['parent_image_path']):
            conn.close()
            return jsonify({'success': False, 'error': 'parent image missing'}), 410

        img = Image.open(row['parent_image_path']).convert('RGB')
        w, h = img.size
        split_col = max(1, min(w - 1, split_col))  # clamp

        halves = conn.execute(
            "SELECT id, image_path, mode FROM ahb_receipt_queue WHERE pair_id=? AND status!='rejected'",
            (row['pair_id'],)).fetchall()
        if len(halves) < 2:
            conn.close()
            return jsonify({'success': False, 'error': 'pair incomplete (use rotate or re-upload)'}), 400

        for half in halves:
            side = 'left' if 'left' in (half['mode'] or '') else 'right'
            box = (0, 0, split_col, h) if side == 'left' else (split_col, 0, w, h)
            img.crop(box).save(half['image_path'], 'JPEG', quality=90)
            conn.execute(
                "UPDATE ahb_receipt_queue "
                "SET status='pending', result_json=NULL, error=NULL, split_col=? "
                "WHERE id=?",
                (split_col, half['id']))
        conn.commit()
        conn.close()
        _spawn_receipt_queue_worker()
        return jsonify({'success': True, 'split_col': split_col})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
```

- [ ] **Step 2: Smoke-check**

```bash
sudo systemctl restart baza-dashboard.service
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Content-Type: application/json" \
  -d '{"split_col":100}' http://localhost:8888/api/ahb/receipts/queue/__bogus__/adjust-split
```
Expected: `404`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py
git commit -m "feat(quickrf): add /queue/<qid>/adjust-split endpoint (drag-line re-crop)"
```

---

## Task 8: Frontend — replace upload tile row

**Files:**
- Modify: `dashboard/templates/ahb123.html:827-872` (the `<div style="display:flex;gap:12px;..."` block holding the four upload tiles)

Replace the four-tile row (Single / Dual / Bulk / Re-Scan Existing) with two tiles: Easy Bulk Upload + Re-Scan Existing.

- [ ] **Step 1: Replace the tile block**

In `dashboard/templates/ahb123.html`, find the div that opens with `<div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap">` (around line 827) and contains the four `<!-- Single Receipt Upload -->`, `<!-- Dual Receipt Upload -->`, `<!-- Bulk Upload -->`, `<!-- Scan Existing -->` tiles. Replace the **entire div** (from that opening `<div>` through its matching `</div>` — should end around line 872) with:

```html
    <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap">
      <!-- Easy Bulk Upload — handles both 1-receipt and 2-receipts-per-image automatically -->
      <div style="flex:2;min-width:280px;background:#0a0a18;border:1px solid #2a1a4a;border-radius:10px;padding:18px;text-align:center">
        <div style="font-size:28px;margin-bottom:6px">&#128230;</div>
        <div style="font-size:14px;font-weight:800;color:#e0d4ff;margin-bottom:4px">Easy Bulk Upload</div>
        <div style="font-size:11px;color:#888;margin-bottom:10px">
          Pick any mix of images — 1-up portrait or 2-up landscape.
          We auto-detect &amp; crop. Fix mistakes per card with Rotate / Merge / Adjust Split.
        </div>
        <label class="btn btn-primary" style="cursor:pointer">
          Select Images
          <input type="file" accept="image/*" multiple style="display:none" onchange="submitReceiptsToQueue(this.files)">
        </label>
      </div>
      <!-- Re-Scan Existing -->
      <div style="flex:1;min-width:200px;background:#0a0a18;border:1px solid #1a1a2e;border-radius:10px;padding:16px;text-align:center">
        <div style="font-size:24px;margin-bottom:6px">&#128269;</div>
        <div style="font-size:13px;font-weight:700;color:#ddd;margin-bottom:4px">Re-Scan Existing</div>
        <div style="font-size:11px;color:#555;margin-bottom:10px">OCR existing receipts (won't alter data)</div>
        <select id="rq-scan-cat" class="filter-select" style="width:100%;padding:6px 8px;margin-bottom:6px;font-size:11px">
          <option value="">All Categories</option>
          <option value="Materials">Materials</option>
          <option value="Tools">Tools</option>
          <option value="Fuel">Fuel</option>
          <option value="Food">Food</option>
        </select>
        <button class="btn btn-sm btn-secondary" onclick="scanExistingReceipts()">Start Scan</button>
      </div>
    </div>
```

- [ ] **Step 2: Update `submitReceiptsToQueue` signature**

In the same file, find `async function submitReceiptsToQueue(files,mode){` (around line 5306). Replace the **entire function** (from `async function submitReceiptsToQueue(files,mode){` through its closing `}`) with:

```javascript
async function submitReceiptsToQueue(files){
  if(!files||!files.length)return;
  const fd=new FormData();
  for(const f of files) fd.append('files',f);
  try{
    showToast(`Uploading ${files.length} image(s)...`);
    const res=await fetch('/api/ahb/receipts/process',{method:'POST',body:fd});
    const data=await res.json();
    if(data.success){
      showToast(`${data.count} receipt(s) queued — OCR running automatically`);
      if(!allProjects.length){
        try{ allProjects=await fetch('/api/ahb/projects').then(r=>r.json()); }catch(e){}
      }
      loadReceiptQueue();
      _rqStartPolling();
    }else showToast(data.error||'Upload failed','error');
  }catch(e){showToast('Error: '+e.message,'error');}
}
```

- [ ] **Step 3: Hard-refresh browser and verify upload**

Open `https://nova.ahb123.com/ahb123` (or `http://localhost:8888/ahb123`) → RECEIPTS tab. Upload one portrait image and one landscape image with two receipts. Verify:
- Single tile is visible labeled "Easy Bulk Upload"
- Re-Scan tile still works
- After upload: portrait → 1 queue card; landscape → 2 queue cards with side-by-side crops.

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/ahb123.html
git commit -m "feat(quickrf): replace 4-tile upload row with single Easy Bulk Upload tile"
```

---

## Task 9: Frontend — add Rotate / Merge / Adjust Split buttons to queue cards

**Files:**
- Modify: `dashboard/templates/ahb123.html:5437-5446` (the action buttons row inside `renderReceiptQueue`)

The buttons are conditionally shown:
- **Rotate**: always visible.
- **Merge**: visible only when `q.pair_id` is set.
- **Adjust Split**: visible only when `q.pair_id` is set AND `q.parent_image_path` is set.

- [ ] **Step 1: Update the action button row in `renderReceiptQueue`**

Find this block in `renderReceiptQueue` (around line 5437):
```javascript
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
            <span style="font-size:10px;color:#444;font-family:monospace">${escHtml(s.teller_name||'')} ${s.purchase_time?'· '+s.purchase_time:''}</span>
            <div style="display:flex;gap:6px">
              <button class="btn btn-sm btn-secondary" onclick="rqSplit('${q.id}')" title="Image actually contains 2 receipts — split down the middle and re-OCR">✂ Split</button>
              <button class="btn btn-sm btn-secondary" onclick="rqRescan('${q.id}')" title="Re-run OCR on this image">🔁 Rescan</button>
              <button class="btn btn-sm btn-primary" onclick="rqFile('${q.id}')">📂 File</button>
              <button class="btn btn-sm btn-danger" onclick="rejectQueueItem('${q.id}')">🗑 Erase</button>
            </div>
          </div>
```

Replace the inner `<div style="display:flex;gap:6px">...</div>` (the buttons container) with:

```javascript
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              <button class="btn btn-sm btn-secondary" onclick="rqRotate('${q.id}')" title="Rotate source 90° CW and re-detect">🔄 Rotate</button>
              ${q.pair_id?`<button class="btn btn-sm btn-secondary" onclick="rqMerge('${q.id}')" title="This was incorrectly split — merge halves back into one">⇆ Merge</button>`:''}
              ${q.pair_id&&q.parent_image_path?`<button class="btn btn-sm btn-secondary" onclick="rqOpenAdjustSplit('${q.id}')" title="Drag the split line to fix off-center crops">⇔ Adjust Split</button>`:''}
              <button class="btn btn-sm btn-secondary" onclick="rqSplit('${q.id}')" title="Force-split this image at the midpoint">✂ Force Split</button>
              <button class="btn btn-sm btn-secondary" onclick="rqRescan('${q.id}')" title="Re-run OCR on this image">🔁 Rescan</button>
              <button class="btn btn-sm btn-primary" onclick="rqFile('${q.id}')">📂 File</button>
              <button class="btn btn-sm btn-danger" onclick="rejectQueueItem('${q.id}')">🗑 Erase</button>
            </div>
```

- [ ] **Step 2: Add `rqRotate` and `rqMerge` JS functions**

Find `async function rqRescan(qid){` (around line 5498). Insert these two new functions immediately ABOVE it:

```javascript
async function rqRotate(qid){
  try{
    const res=await fetch('/api/ahb/receipts/queue/'+qid+'/rotate',{method:'POST'});
    const data=await res.json();
    if(data.success){showToast('Rotated — re-OCR queued');loadReceiptQueue();_rqStartPolling();}
    else showToast(data.error||'Rotate failed','error');
  }catch(e){showToast('Error: '+e.message,'error');}
}

async function rqMerge(qid){
  if(!confirm('Merge this half with its sibling back into one receipt?'))return;
  try{
    const res=await fetch('/api/ahb/receipts/queue/'+qid+'/merge',{method:'POST'});
    const data=await res.json();
    if(data.success){showToast('Merged — re-OCR queued');loadReceiptQueue();_rqStartPolling();}
    else showToast(data.error||'Merge failed','error');
  }catch(e){showToast('Error: '+e.message,'error');}
}
```

- [ ] **Step 3: Hard-refresh browser and verify**

- Upload a clean dual landscape image. Two cards appear with `⇆ Merge` and `⇔ Adjust Split` buttons visible.
- Click `🔄 Rotate` on a 1-up card → card refreshes, OCR re-runs.
- Click `⇆ Merge` on one half → both halves disappear, one new card appears, OCR re-runs.

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/ahb123.html
git commit -m "feat(quickrf): add Rotate / Merge buttons + conditional rendering on pair cards"
```

---

## Task 10: Frontend — Adjust Split modal with draggable line

**Files:**
- Modify: `dashboard/templates/ahb123.html` — add modal HTML near the bottom of the file (just before closing `</body>` is fine; if there's an existing modal-stack region, put it there); add `rqOpenAdjustSplit` and `rqApplyAdjustSplit` JS near the other `rq*` functions.

The modal shows the parent image full-width with a draggable vertical line. User drags, clicks Apply, both halves are re-cropped.

- [ ] **Step 1: Add modal HTML**

Find the closing `</body>` tag (near the end of `ahb123.html`). Insert this block immediately ABOVE the `</body>`:

```html
<!-- Adjust Split modal (QuickRF) -->
<div id="rq-adjust-modal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.85);z-index:9999;align-items:center;justify-content:center">
  <div style="background:#0a0a18;border:1px solid #2a2a4a;border-radius:10px;padding:18px;max-width:90vw;max-height:90vh;overflow:auto">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div style="font-size:13px;font-weight:700;color:#e0d4ff">Adjust Split — drag the orange line to where the gap is</div>
      <button class="btn btn-sm btn-danger" onclick="document.getElementById('rq-adjust-modal').style.display='none'">✕ Close</button>
    </div>
    <div id="rq-adjust-canvas-wrap" style="position:relative;display:inline-block;cursor:ew-resize;user-select:none">
      <img id="rq-adjust-img" style="max-width:80vw;max-height:70vh;display:block" alt="">
      <div id="rq-adjust-line" style="position:absolute;top:0;bottom:0;width:3px;background:#f5a623;box-shadow:0 0 6px #f5a623;pointer-events:none"></div>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;gap:10px">
      <span id="rq-adjust-info" style="font-size:11px;color:#888;font-family:monospace"></span>
      <button class="btn btn-primary" id="rq-adjust-apply">Apply Split</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add JS for the modal**

Find `async function rqRescan(qid){` (around line 5498). Insert this block immediately ABOVE it (next to the `rqRotate`/`rqMerge` functions added in Task 9):

```javascript
let _rqAdjustState=null;  // {qid, naturalW, displayW, splitColNatural}

async function rqOpenAdjustSplit(qid){
  // We need the parent image URL. The parent is stored on disk but not exposed
  // by an endpoint yet — reuse /queue/image/<qid> with a sibling lookup, or
  // fetch the parent via a dedicated url. Simplest: serve the parent at a
  // predictable path; here we hit /queue/image/<qid>?parent=1.
  const url='/api/ahb/receipts/queue/image/'+qid+'?parent=1';
  const modal=document.getElementById('rq-adjust-modal');
  const img=document.getElementById('rq-adjust-img');
  const line=document.getElementById('rq-adjust-line');
  const info=document.getElementById('rq-adjust-info');
  img.src=url;
  modal.style.display='flex';
  await new Promise(res=>{img.onload=res;img.onerror=()=>{showToast('Could not load parent image','error');res();}});
  const naturalW=img.naturalWidth;
  const naturalH=img.naturalHeight;
  const displayW=img.clientWidth;
  // Initialize at 50%; user drags from there.
  let splitColNatural=Math.floor(naturalW/2);
  _rqAdjustState={qid,naturalW,naturalH,displayW,splitColNatural};
  const updateLine=()=>{
    const ratio=displayW/naturalW;
    line.style.left=Math.round(splitColNatural*ratio)+'px';
    info.textContent=`split @ x=${splitColNatural} / ${naturalW}px (${Math.round(splitColNatural/naturalW*100)}%)`;
  };
  updateLine();
  const wrap=document.getElementById('rq-adjust-canvas-wrap');
  let dragging=false;
  const onMove=(ev)=>{
    if(!dragging)return;
    const rect=wrap.getBoundingClientRect();
    const x=Math.max(1,Math.min(displayW-1,ev.clientX-rect.left));
    splitColNatural=Math.max(1,Math.min(naturalW-1,Math.round(x*naturalW/displayW)));
    _rqAdjustState.splitColNatural=splitColNatural;
    updateLine();
  };
  wrap.onmousedown=(ev)=>{dragging=true;onMove(ev);};
  window.onmousemove=onMove;
  window.onmouseup=()=>{dragging=false;};
  document.getElementById('rq-adjust-apply').onclick=()=>rqApplyAdjustSplit();
}

async function rqApplyAdjustSplit(){
  if(!_rqAdjustState)return;
  const {qid,splitColNatural}=_rqAdjustState;
  try{
    const res=await fetch('/api/ahb/receipts/queue/'+qid+'/adjust-split',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({split_col:splitColNatural})
    });
    const data=await res.json();
    if(data.success){
      showToast('Split adjusted — re-OCR queued');
      document.getElementById('rq-adjust-modal').style.display='none';
      _rqAdjustState=null;
      loadReceiptQueue();_rqStartPolling();
    }else showToast(data.error||'Adjust failed','error');
  }catch(e){showToast('Error: '+e.message,'error');}
}
```

- [ ] **Step 3: Extend `/queue/image/<qid>` to serve parent on `?parent=1`**

The modal asks for the parent image via `?parent=1`. Update `dashboard/app.py` — find `def api_ahb_receipts_queue_image(qid):` (around line 7404). Replace its body with:

```python
def api_ahb_receipts_queue_image(qid):
    """Serve a queue item's image. ?parent=1 serves the original (pre-split) parent."""
    try:
        conn = _ahb_db()
        row = conn.execute(
            "SELECT image_path, parent_image_path FROM ahb_receipt_queue WHERE id = ?",
            (qid,)).fetchone()
        conn.close()
        if not row:
            return 'Not found', 404
        want_parent = request.args.get('parent') in ('1', 'true', 'yes')
        path = row['parent_image_path'] if want_parent else row['image_path']
        if not path or not os.path.exists(path):
            return 'Not found', 404
        return send_from_directory(os.path.dirname(path), os.path.basename(path))
    except Exception as e:
        return str(e), 500
```

- [ ] **Step 4: Restart, hard-refresh browser, end-to-end check**

```bash
sudo systemctl restart baza-dashboard.service
```

Then in the browser:
- Upload one landscape image with two clearly off-center receipts (or any test landscape).
- On either of the two resulting cards, click `⇔ Adjust Split`.
- Modal opens; orange vertical line appears over the original image at ~50%. Drag it left/right.
- Click "Apply Split". Modal closes; both halves re-cropped at the new column; OCR re-runs (cards go through processing → done).

- [ ] **Step 5: Commit**

```bash
git add dashboard/templates/ahb123.html dashboard/app.py
git commit -m "feat(quickrf): add Adjust Split modal with draggable line"
```

---

## Task 11: Final smoke test + integration checklist

**Files:** none — manual verification.

- [ ] **Step 1: Run the unit tests one more time**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
source venv/bin/activate
python -m pytest tests/test_split_detection.py -v
```
Expected: `4 passed`.

- [ ] **Step 2: Service health**

```bash
sudo systemctl status baza-dashboard.service --no-pager | head -20
```
Expected: `active (running)`, no recent stack traces in journal:
```bash
sudo journalctl -u baza-dashboard.service --since "5 minutes ago" --no-pager | tail -40
```

- [ ] **Step 3: End-to-end happy path**

In browser at the QuickRF tab:
1. Click "Easy Bulk Upload" → select 3 images: (a) portrait single, (b) landscape with two clearly-separated receipts, (c) sideways photo of a single receipt.
2. Within ~1s, queue shows 1 + 2 + 2 = 5 cards (because (c) gets misdetected as landscape-dual).
3. On one half from (c): click `🔄 Rotate`. Other half also disappears (both halves of pair rejected). One new 1-up card appears.
4. On one half from (b): click `⇔ Adjust Split`. Modal opens; drag the line; Apply. Halves re-crop; OCR re-runs.
5. On one half from (b): click `⇆ Merge`. Both halves disappear; one new 1-up card appears.
6. File one card with `📂 File`. Verify it appears in the main receipts kanban/table.

- [ ] **Step 4: Commit (if any final fixes needed)**

If anything in the smoke test fails, fix inline, then commit. Otherwise this task closes the work.

---

## Self-review notes

**Spec coverage:**
- Easy Bulk Upload tile + remove old tiles → Task 8 ✓
- EXIF + portrait/landscape detection → Task 3 ✓
- White-valley split → Task 2 ✓
- Queue card thumbnails immediate → already works with Task 4 (cropping happens before queue insert) ✓
- Rotate 90° → Tasks 5 + 9 ✓
- Merge → Tasks 6 + 9 ✓
- Adjust Split modal → Tasks 7 + 10 ✓
- DB schema additions → Task 1 ✓
- Tests → Task 2 ✓
- Migration via existing `alter_stmts` pattern → Task 1 ✓

**Type consistency:**
- `_find_split_column(img)` → returns `int`. Used in `_detect_and_queue` and `adjust-split`. ✓
- `_detect_and_queue(file_storage, conn, queue_dir)` → returns `list[str]`. Called from `process` and `rotate`. ✓
- DB columns `parent_image_path TEXT`, `pair_id TEXT`, `split_col INTEGER` — referenced in all SQL identically. ✓
- JS: `q.pair_id` and `q.parent_image_path` used consistently in `renderReceiptQueue`. ✓
