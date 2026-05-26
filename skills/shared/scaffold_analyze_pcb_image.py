"""Skill: vision-analyze a PCB / board / circuit photo for a pcb_vision scaffold node.

Reads `{node_id, mode}` via SKILL_ARGS (`mode` ∈ {merge,reset}, default merge).
Loads the image referenced by the node's payload, downscales it, sends it to
qwen3-vl (fallback llava:13b) with the baza_components_library vocabulary, and
writes the resulting overlays back to the node's payload_json.

`merge` mode preserves overlays the user has marked `user_corrected: true`
and merges new detections by IoU > 0.5. `reset` mode replaces everything.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core import baza_components_library as bcl  # noqa: E402

DB_PATH       = REPO / "dashboard" / "baza_projects.db"
OLLAMA_URL    = os.environ.get("OLLAMA_URL", "http://localhost:11434")
PRIMARY_MODEL = "qwen3-vl:latest"
FALLBACK      = "llava:13b"
MAX_LONG_EDGE = 1600
TIMEOUT_S     = 90
MIN_CONFIDENCE = 0.30
IOU_MERGE      = 0.5

VALID_STATUSES = {"pending", "running", "completed", "awaiting_input", "blocked", "failed"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _emit(message: str) -> None:
    print(message, flush=True)


def _con():
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.row_factory = sqlite3.Row
    return con


def _load_node(node_id: int) -> dict[str, Any]:
    with _con() as c:
        row = c.execute(
            "SELECT id, project_id, node_type, title, status, payload_json "
            "FROM project_scaffold_nodes WHERE id = ?", (node_id,)
        ).fetchone()
    if not row:
        raise RuntimeError(f"node {node_id} not found")
    if row["node_type"] != "pcb_vision":
        raise RuntimeError(f"node {node_id} is type {row['node_type']!r}, expected pcb_vision")
    payload = json.loads(row["payload_json"] or "{}")
    return {"id": row["id"], "project_id": row["project_id"],
            "title": row["title"], "status": row["status"], "payload": payload}


def _save_node(node_id: int, payload: dict, status: str) -> None:
    if status not in VALID_STATUSES:
        status = "completed"
    with _con() as c:
        c.execute(
            "UPDATE project_scaffold_nodes SET payload_json = ?, status = ?, updated_at = ? "
            "WHERE id = ?",
            (json.dumps(payload), status, _now_iso(), node_id),
        )
        c.commit()


def _downscale_to_jpeg(src: Path, max_long: int = MAX_LONG_EDGE) -> Path:
    """Re-encode to a temp JPEG ≤ max_long edge. Raises if unreadable."""
    try:
        from PIL import Image, ImageOps
    except ImportError as e:
        raise RuntimeError(f"Pillow required for image processing: {e}")
    img = Image.open(src)
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > max_long:
        scale = max_long / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix="pcb_vision_")
    os.close(fd)
    img.save(tmp_path, "JPEG", quality=88)
    return Path(tmp_path)


def _build_vocabulary_block() -> str:
    """Compact catalog of part_id + display name + keywords for the LLM."""
    rows = []
    for comp in bcl.list_components():
        kws = ", ".join(comp.get("match_keywords") or [])
        rows.append(f"- {comp['id']} — {comp['name']} (category: {comp['category']})"
                    + (f" — keywords: {kws}" if kws else ""))
    return "\n".join(rows)


SYSTEM_PROMPT = """You are Claw Batto's vision analyst inspecting a photo of an
electronic board, circuit, or PCB. Identify the board itself (what model / what it
does in one short phrase each) and every visible component with a bounding box.

Use this canonical part vocabulary when you can recognize a match; if a component
is visible but doesn't match any catalog id, use a free-text label and leave
suggested_part_id null.

CATALOG:
{vocab}

Return STRICTLY this JSON shape, no prose, no markdown fences:
{{
  "board_label": "<short name of the board>",
  "board_function": "<one sentence describing what it does>",
  "components": [
    {{
      "label": "<human-readable name>",
      "bbox": [x, y, w, h],
      "confidence": 0.0-1.0,
      "suggested_part_id": "<catalog id or null>"
    }}
  ]
}}

Coordinates in `bbox` MUST be fractions of the image dimensions in [0,1].
If you can identify nothing with confidence ≥ 0.3, return components: [].
"""


def _call_ollama_vision(model: str, image_b64: str, system: str, user: str) -> str:
    # qwen3-vl (and other "thinking" models) burn the whole num_predict budget
    # on hidden reasoning unless we explicitly disable it. think:False is a
    # no-op for models that don't support thinking, so it's safe across both.
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user, "images": [image_b64]},
        ],
        "format": "json",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": 2500},
    }
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=TIMEOUT_S)
    r.raise_for_status()
    data = r.json()
    content = data.get("message", {}).get("content", "") or ""
    # Strip any leaked <think>...</think> blocks just in case
    import re as _re
    content = _re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
    return content


def _parse_vision_output(raw: str) -> dict:
    """Tolerant JSON extraction."""
    raw = raw.strip()
    if not raw:
        return {}
    # try direct parse
    try:
        return json.loads(raw)
    except Exception:
        pass
    # try to find the first {...} block
    start = raw.find("{")
    end   = raw.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(raw[start:end + 1])
        except Exception:
            return {}
    return {}


def _clamp_bbox(bbox: Any) -> list[float] | None:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(0.01, min(1.0 - x, w))
    h = max(0.01, min(1.0 - y, h))
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def _iou(a: list[float], b: list[float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _normalize_components(parsed: dict, start_id: int = 1) -> list[dict]:
    raw_components = parsed.get("components") or []
    valid_part_ids = {c["id"] for c in bcl.list_components()}
    out: list[dict] = []
    for i, c in enumerate(raw_components):
        if not isinstance(c, dict):
            continue
        bbox = _clamp_bbox(c.get("bbox"))
        if bbox is None:
            continue
        try:
            conf = float(c.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf < MIN_CONFIDENCE:
            continue
        label = str(c.get("label") or "").strip()[:120]
        if not label:
            continue
        spid = c.get("suggested_part_id")
        if spid and spid not in valid_part_ids:
            spid = None
        out.append({
            "id": f"ov_{start_id + i}",
            "label": label,
            "bbox": bbox,
            "confidence": round(conf, 3),
            "suggested_part_id": spid,
            "user_corrected": False,
        })
    return out


def _merge_overlays(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """Merge: keep user_corrected overlays as-is; update others by IoU > 0.5."""
    kept = [o for o in existing if o.get("user_corrected")]
    used_fresh: set[int] = set()
    for old in [o for o in existing if not o.get("user_corrected")]:
        best_i, best_iou = -1, 0.0
        for i, new in enumerate(fresh):
            if i in used_fresh:
                continue
            iou = _iou(old["bbox"], new["bbox"])
            if iou > best_iou:
                best_iou, best_i = iou, i
        if best_iou > IOU_MERGE and best_i >= 0:
            merged = dict(fresh[best_i])
            merged["id"] = old["id"]
            kept.append(merged)
            used_fresh.add(best_i)
        # else: drop the old non-corrected overlay (model no longer sees it)
    for i, new in enumerate(fresh):
        if i not in used_fresh:
            kept.append(new)
    # re-number ids to stay sequential after merge
    for idx, ov in enumerate(kept, start=1):
        ov["id"] = f"ov_{idx}"
    return kept


def main() -> int:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    node_id = int(args.get("node_id") or 0)
    mode = args.get("mode") or "merge"
    if mode not in ("merge", "reset"):
        mode = "merge"
    if not node_id:
        print(json.dumps({"ok": False, "error": "node_id required"}))
        return 1

    try:
        node = _load_node(node_id)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1

    payload = node["payload"]
    image_path = payload.get("image_path")
    if not image_path or not Path(image_path).exists():
        payload["analyze_error"] = "image_missing"
        _save_node(node_id, payload, "awaiting_input")
        print(json.dumps({"ok": False, "error": "image_missing"}))
        return 1

    _emit(f"[pcb_vision] node={node_id} mode={mode} image={image_path}")
    try:
        tmp = _downscale_to_jpeg(Path(image_path))
    except Exception as e:
        payload["analyze_error"] = f"preprocess_failed: {e}"
        _save_node(node_id, payload, "failed")
        print(json.dumps({"ok": False, "error": f"preprocess_failed: {e}"}))
        return 1

    try:
        image_b64 = base64.b64encode(tmp.read_bytes()).decode("ascii")
    finally:
        try: tmp.unlink()
        except OSError: pass

    system = SYSTEM_PROMPT.format(vocab=_build_vocabulary_block())
    user = ("Analyze this board photo. Identify the board and every visible "
            "component with bounding boxes. Respond with JSON only.")

    used_model = PRIMARY_MODEL
    raw = ""
    t0 = time.time()
    try:
        raw = _call_ollama_vision(PRIMARY_MODEL, image_b64, system, user)
    except Exception as e:
        _emit(f"[pcb_vision] primary {PRIMARY_MODEL} failed: {e} — trying fallback")
        used_model = FALLBACK
        try:
            raw = _call_ollama_vision(FALLBACK, image_b64, system, user)
        except Exception as e2:
            payload["analyze_error"] = f"both_models_failed: {e}; {e2}"
            payload["analyzed_at"] = _now_iso()
            _save_node(node_id, payload, "failed")
            print(json.dumps({"ok": False, "error": str(e2)}))
            return 1
    elapsed = time.time() - t0

    parsed = _parse_vision_output(raw)
    fresh = _normalize_components(parsed)

    existing = payload.get("overlays") or []
    if mode == "reset":
        merged = fresh
        for idx, ov in enumerate(merged, start=1):
            ov["id"] = f"ov_{idx}"
    else:
        merged = _merge_overlays(existing, fresh)

    payload["overlays"] = merged
    payload["board_label"] = parsed.get("board_label") or payload.get("board_label") or ""
    payload["board_function"] = parsed.get("board_function") or payload.get("board_function") or ""
    payload["analyzed_at"] = _now_iso()
    payload["model_used"] = used_model
    payload["analyze_elapsed_s"] = round(elapsed, 2)
    payload.pop("analyze_error", None)

    new_status = "completed" if merged else "awaiting_input"
    _save_node(node_id, payload, new_status)
    _emit(f"[pcb_vision] node={node_id} → {len(merged)} overlay(s) in {elapsed:.1f}s using {used_model}")

    print(json.dumps({
        "ok": True,
        "node_id": node_id,
        "status": new_status,
        "model_used": used_model,
        "elapsed_s": round(elapsed, 2),
        "overlay_count": len(merged),
        "board_label": payload.get("board_label", ""),
        "board_function": payload.get("board_function", ""),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
