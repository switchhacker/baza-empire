"""Face + eye/lip crop pipeline using InsightFace SCRFD.

For each detected face:
  · save the face crop as <source-id>_face_<n>.jpg
  · save eye crop (combined eyes region) as ...eye_<n>.jpg
  · save lips crop as ...lips_<n>.jpg
  · register each as a child asset (source='crop', parent_id=<frame>)

Each crop inherits intrinsic parent attributes (gender, hair_color, age_band,
build, mood, nsfw) so /Catalogue/Faces/Female filters work without a 3-table
join. Done in Python, not a SQL trigger — see spec §5.3.
"""
from __future__ import annotations

import os
import time
from typing import Iterable, Optional

from PIL import Image

from dashboard.vision.db import DEFAULT_DB_PATH, connect

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CROPS_DIR = os.path.join(DASHBOARD_DIR, "artifacts", ".vision-crops")

INHERITABLE_KEYS = ("gender", "age_band", "hair_color", "hair_style",
                    "build", "mood", "nsfw", "ethnicity")

PADDING = 0.12   # 12% bbox expansion before crop


def clamp_bbox(x: int, y: int, w: int, h: int, *, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """Clip a bbox to image bounds. Returns (x, y, w, h) — possibly shrunk."""
    x2, y2 = x + w, y + h
    nx = max(0, x); ny = max(0, y)
    nx2 = min(img_w, x2); ny2 = min(img_h, y2)
    return nx, ny, max(0, nx2 - nx), max(0, ny2 - ny)


def expand_bbox(x: int, y: int, w: int, h: int, pct: float, *, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """Expand bbox by `pct` on each side, then clamp to image bounds."""
    dx = int(w * pct); dy = int(h * pct)
    return clamp_bbox(x - dx, y - dy, w + 2 * dx, h + 2 * dy, img_w=img_w, img_h=img_h)


def _save_crop(img: Image.Image, bbox, out_path: str) -> None:
    x, y, w, h = bbox
    if w <= 0 or h <= 0:
        return
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.crop((x, y, x + w, y + h)).save(out_path, "JPEG", quality=88)


def _eye_bbox_from_landmarks(face) -> Optional[tuple[int, int, int, int]]:
    """SCRFD landmark 5 = [le, re, nose, lm, rm]. Compose a bbox covering both eyes."""
    if face.kps is None or len(face.kps) < 5:
        return None
    lx, ly = face.kps[0]; rx, ry = face.kps[1]
    cx = int((lx + rx) / 2); cy = int((ly + ry) / 2)
    eye_w = int(abs(rx - lx) * 1.6)
    eye_h = int(eye_w * 0.45)
    return (cx - eye_w // 2, cy - eye_h // 2, eye_w, eye_h)


def _lips_bbox_from_landmarks(face) -> Optional[tuple[int, int, int, int]]:
    if face.kps is None or len(face.kps) < 5:
        return None
    lx, ly = face.kps[3]; rx, ry = face.kps[4]
    cx = int((lx + rx) / 2); cy = int((ly + ry) / 2)
    lip_w = int(abs(rx - lx) * 1.4)
    lip_h = int(lip_w * 0.5)
    return (cx - lip_w // 2, cy - lip_h // 2, lip_w, lip_h)


_FACE_APP = None


def _face_app():
    global _FACE_APP
    if _FACE_APP is None:
        from insightface.app import FaceAnalysis
        _FACE_APP = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _FACE_APP.prepare(ctx_id=-1, det_size=(640, 640))
    return _FACE_APP


def count_faces(path: str) -> int:
    """Cheap face-presence check used by the indexer's people-only filter.
    InsightFace SCRFD on CPU is ~100ms/image, far cheaper than a qwen3-vl
    classification call. Returns the number of faces detected, or 0 on
    any failure (corrupt image, missing model, etc) — failures should not
    block the indexer."""
    try:
        import numpy as np
        img = Image.open(path).convert("RGB")
        return len(_face_app().get(np.array(img)))
    except Exception:
        return 0


def _inheritable_attrs(con, parent_id: int) -> dict[str, tuple[str, float]]:
    rows = con.execute(
        "SELECT key, value, confidence FROM attributes WHERE asset_id=? AND key IN ({})".format(
            ",".join(["?"] * len(INHERITABLE_KEYS))
        ),
        (parent_id, *INHERITABLE_KEYS),
    ).fetchall()
    return {r["key"]: (r["value"], r["confidence"]) for r in rows}


def _register_crop(con, *, abs_path: str, parent_id: int, part: str,
                   bbox: tuple[int, int, int, int], detector: str) -> int:
    """Insert child asset row, crops row, and inherited attribute rows."""
    from dashboard.vision.ingest import observe
    asset_id = observe(abs_path, source="crop", db_path=None, parent_id=parent_id)
    # ingest.observe uses DEFAULT_DB_PATH; ensure same con can see it (we're WAL).
    # Insert crop row.
    x, y, w, h = bbox
    con.execute(
        """INSERT INTO crops (asset_id, part, bbox_x, bbox_y, bbox_w, bbox_h, detector)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(asset_id) DO UPDATE SET
               part=excluded.part, bbox_x=excluded.bbox_x, bbox_y=excluded.bbox_y,
               bbox_w=excluded.bbox_w, bbox_h=excluded.bbox_h, detector=excluded.detector""",
        (asset_id, part, x, y, w, h, detector),
    )
    # Denormalize inheritable parent attrs onto child.
    for k, (v, conf) in _inheritable_attrs(con, parent_id).items():
        con.execute(
            """INSERT INTO attributes (asset_id, key, value, confidence, source)
               VALUES (?, ?, ?, ?, 'inherited')
               ON CONFLICT(asset_id, key) DO NOTHING""",
            (asset_id, k, v, conf),
        )
    return asset_id


def crop_one(parent_path: str, parent_id: int, db_path: Optional[str] = None) -> int:
    """Detect faces in `parent_path`, save crops, register children. Returns
    the count of new crop assets created."""
    img = Image.open(parent_path).convert("RGB")
    img_w, img_h = img.size

    import numpy as np
    faces = _face_app().get(np.array(img))
    if not faces:
        return 0

    crops_root = os.path.join(CROPS_DIR, str(parent_id))
    os.makedirs(crops_root, exist_ok=True)

    con = connect(db_path)
    created = 0
    for n, f in enumerate(faces):
        x1, y1, x2, y2 = [int(v) for v in f.bbox]
        face_bbox = expand_bbox(x1, y1, x2 - x1, y2 - y1, PADDING, img_w=img_w, img_h=img_h)
        face_path = os.path.join(crops_root, f"face_{n}.jpg")
        _save_crop(img, face_bbox, face_path)
        _register_crop(con, abs_path=face_path, parent_id=parent_id,
                       part="face", bbox=face_bbox, detector="insightface-scrfd")
        created += 1

        eye = _eye_bbox_from_landmarks(f)
        if eye:
            eye = clamp_bbox(*eye, img_w=img_w, img_h=img_h)
            eye_path = os.path.join(crops_root, f"eye_{n}.jpg")
            _save_crop(img, eye, eye_path)
            _register_crop(con, abs_path=eye_path, parent_id=parent_id,
                           part="eye", bbox=eye, detector="insightface-landmarks")
            created += 1

        lips = _lips_bbox_from_landmarks(f)
        if lips:
            lips = clamp_bbox(*lips, img_w=img_w, img_h=img_h)
            lips_path = os.path.join(crops_root, f"lips_{n}.jpg")
            _save_crop(img, lips, lips_path)
            _register_crop(con, abs_path=lips_path, parent_id=parent_id,
                           part="lips", bbox=lips, detector="insightface-landmarks")
            created += 1

    con.execute(
        "INSERT INTO ingest_log (asset_id, step, ok, ts, detail) VALUES (?, 'crop', 1, ?, ?)",
        (parent_id, time.time(), f"created={created}"),
    )
    return created
