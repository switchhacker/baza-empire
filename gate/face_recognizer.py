"""Host-side face recognition for baza gate.

Reuses the already-installed insightface buffalo_l model (same one
dashboard/vision/cropper.py uses). Each detected face exposes a 512-d
L2-normalized ArcFace embedding (`normed_embedding`); identity match is a
cosine similarity (== dot product for unit vectors).
"""
import logging
from io import BytesIO

import numpy as np
from PIL import Image

log = logging.getLogger("baza.gate.face_recognizer")

_APP = None


def _face_app():
    """Lazy singleton FaceAnalysis (CPU by default; mirrors cropper.py)."""
    global _APP
    if _APP is None:
        from insightface.app import FaceAnalysis
        _APP = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _APP.prepare(ctx_id=-1, det_size=(640, 640))
    return _APP


def embed(image_bytes: bytes) -> list[np.ndarray]:
    """Return one 512-d normed embedding per detected face (possibly empty)."""
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    faces = _face_app().get(np.array(img))
    out = []
    for f in faces:
        v = np.asarray(f.normed_embedding, dtype=np.float32)
        out.append(v)
    return out


def best_match(probe: np.ndarray, gallery: list[tuple[str, str, np.ndarray]],
               threshold: float) -> tuple[str | None, str | None, float]:
    """Cosine-match a probe embedding against (person, role, embedding) rows.

    `probe` may be unnormed; it is L2-normalized defensively. Returns
    (person, role, score) for the best row at/above threshold, else
    (None, None, best_score). Empty gallery -> (None, None, 0.0).

    Raises ValueError if `probe` is non-finite (NaN/Inf) so a degenerate
    embed() result fails loudly rather than silently denying. Gallery rows
    whose embedding shape differs from the probe are skipped (logged), not
    matched, so a malformed enrolled vector can't crash the unlock path.
    """
    if not np.isfinite(probe).all():
        raise ValueError("probe contains NaN/Inf - check embed() output")
    if not gallery:
        return None, None, 0.0
    p = probe / (np.linalg.norm(probe) + 1e-9)
    best: tuple[str | None, str | None, float] = (None, None, -1.0)
    for person, role, emb in gallery:
        if emb.shape != probe.shape:
            log.warning("skipping gallery row %s/%s: shape %s != probe %s",
                        person, role, emb.shape, probe.shape)
            continue
        e = emb / (np.linalg.norm(emb) + 1e-9)
        score = float(np.dot(p, e))
        if score > best[2]:
            best = (person, role, score)
    if best[2] >= threshold:
        return best
    return None, None, max(best[2], 0.0)
