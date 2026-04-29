"""
Baza Edge — FastAPI router for ESP32 sensor nodes.

Drop-in for the Tool Server. In tools/server.py add:

    from tool_server_endpoints.edge_routes import router as edge_router
    app.include_router(edge_router)

Six endpoints:
    POST /edge/heartbeat          — node check-in (called every 30s)
    POST /edge/frame              — WROVER camera uploads JPEG on motion
    POST /edge/audio_alert        — S3 voice fires when RMS crosses threshold
    POST /edge/vibration_alert    — S3 power fires on accel anomaly
    GET  /edge/nodes              — list all nodes + status (for dashboard)
    GET  /edge/frames/{node_id}/latest — return the most recent JPEG

Storage:
    Redis    edge:node:{id}        HASH, EX 300s  — live status
             edge:alerts:recent    LIST, capped 100 — alert tail for dashboard
             edge:alerts (channel) PUB/SUB         — live alert stream
    Disk     /var/baza/edge_frames/{node_id}/*.jpg + latest.jpg symlink
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import redis
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("baza.edge")

FRAME_DIR = Path(os.environ.get("BAZA_EDGE_FRAME_DIR", "/var/baza/edge_frames"))
FRAME_DIR.mkdir(parents=True, exist_ok=True)

NODE_TTL_SEC = 300                 # node considered offline after 5 min silence
ALERT_TAIL_MAX = 100               # last N alerts kept for dashboard
FRAMES_PER_NODE_KEEP = 200         # rolling window per node (rest pruned)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(REDIS_URL, decode_responses=True)

router = APIRouter(prefix="/edge", tags=["edge"])


# ── Models ─────────────────────────────────────────────────────────────────

class Heartbeat(BaseModel):
    node_id: str
    type: str                                      # wrover_cam / s3_voice / s3_power
    fw_ver: str = "unknown"
    rssi: Optional[int] = None
    free_heap: Optional[int] = None
    uptime_s: Optional[int] = None
    ip: Optional[str] = None
    extra: dict = Field(default_factory=dict)


class AudioAlert(BaseModel):
    node_id: str
    rms: float
    peak: float
    duration_ms: int
    threshold: float
    label: Optional[str] = None


class VibrationAlert(BaseModel):
    node_id: str
    severity: str                                  # warning / critical
    axis: str                                      # x / y / z / mag
    magnitude: float
    baseline_mean: float
    baseline_std: float
    z_score: float


# ── Helpers ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node_key(node_id: str) -> str:
    return f"edge:node:{node_id}"


def _publish_alert(payload: dict) -> None:
    payload.setdefault("ts", _now_iso())
    blob = json.dumps(payload, separators=(",", ":"))
    try:
        r.publish("edge:alerts", blob)
        r.lpush("edge:alerts:recent", blob)
        r.ltrim("edge:alerts:recent", 0, ALERT_TAIL_MAX - 1)
    except redis.RedisError as e:
        logger.error(f"redis publish failed: {e}")


def _touch_node(node_id: str, fields: dict) -> None:
    fields = {k: ("" if v is None else str(v)) for k, v in fields.items()}
    fields["last_seen"] = _now_iso()
    fields["last_seen_unix"] = str(int(time.time()))
    try:
        r.hset(_node_key(node_id), mapping=fields)
        r.expire(_node_key(node_id), NODE_TTL_SEC)
        r.sadd("edge:nodes", node_id)
    except redis.RedisError as e:
        logger.error(f"redis hset failed: {e}")


def _prune_frames(node_dir: Path) -> None:
    files = sorted(node_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[FRAMES_PER_NODE_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/heartbeat")
async def heartbeat(hb: Heartbeat):
    _touch_node(hb.node_id, {
        "type":      hb.type,
        "fw_ver":    hb.fw_ver,
        "rssi":      hb.rssi,
        "free_heap": hb.free_heap,
        "uptime_s":  hb.uptime_s,
        "ip":        hb.ip,
        "extra":     json.dumps(hb.extra) if hb.extra else "",
    })
    return {"ok": True, "ttl": NODE_TTL_SEC}


@router.post("/frame")
async def frame(
    node_id: str = Form(...),
    motion_score: Optional[float] = Form(None),
    file: UploadFile = File(...),
):
    if file.content_type not in ("image/jpeg", "application/octet-stream"):
        raise HTTPException(400, f"expected JPEG, got {file.content_type}")
    body = await file.read()
    if len(body) < 200:
        raise HTTPException(400, "frame too small")

    node_dir = FRAME_DIR / node_id
    node_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    fname = f"{ts}.jpg"
    path = node_dir / fname
    path.write_bytes(body)

    latest = node_dir / "latest.jpg"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(fname)
    except OSError:
        latest.write_bytes(body)

    _prune_frames(node_dir)

    _touch_node(node_id, {
        "last_frame_ts":    ts,
        "last_frame_bytes": len(body),
        "last_motion":      motion_score,
    })

    _publish_alert({
        "kind":         "motion",
        "node_id":      node_id,
        "motion_score": motion_score,
        "frame":        f"/edge/frames/{node_id}/latest",
        "bytes":        len(body),
    })

    return {"ok": True, "path": str(path), "bytes": len(body)}


@router.post("/audio_alert")
async def audio_alert(a: AudioAlert):
    _touch_node(a.node_id, {
        "last_audio_rms":   a.rms,
        "last_audio_peak":  a.peak,
        "last_audio_label": a.label or "",
    })
    _publish_alert({
        "kind":      "audio",
        "node_id":   a.node_id,
        "rms":       a.rms,
        "peak":      a.peak,
        "duration":  a.duration_ms,
        "threshold": a.threshold,
        "label":     a.label,
    })
    return {"ok": True}


@router.post("/vibration_alert")
async def vibration_alert(v: VibrationAlert):
    _touch_node(v.node_id, {
        "last_vibe_severity": v.severity,
        "last_vibe_axis":     v.axis,
        "last_vibe_mag":      v.magnitude,
        "last_vibe_z":        v.z_score,
    })
    _publish_alert({
        "kind":      "vibration",
        "node_id":   v.node_id,
        "severity":  v.severity,
        "axis":      v.axis,
        "magnitude": v.magnitude,
        "baseline":  {"mean": v.baseline_mean, "std": v.baseline_std},
        "z":         v.z_score,
    })
    return {"ok": True}


@router.get("/nodes")
async def nodes():
    out = []
    try:
        ids = r.smembers("edge:nodes") or set()
    except redis.RedisError:
        ids = set()
    now_unix = int(time.time())
    for nid in sorted(ids):
        doc = r.hgetall(_node_key(nid)) or {}
        if not doc:
            # node expired — drop from set
            try:
                r.srem("edge:nodes", nid)
            except redis.RedisError:
                pass
            continue
        last_seen_unix = int(doc.get("last_seen_unix") or 0)
        age_s = now_unix - last_seen_unix if last_seen_unix else None
        doc["node_id"]  = nid
        doc["age_s"]    = age_s
        doc["online"]   = age_s is not None and age_s < 90
        doc["frame_url"] = f"/edge/frames/{nid}/latest" if (FRAME_DIR / nid / "latest.jpg").exists() else None
        out.append(doc)
    try:
        recent = [json.loads(x) for x in (r.lrange("edge:alerts:recent", 0, 49) or [])]
    except (redis.RedisError, ValueError):
        recent = []
    return {"nodes": out, "alerts": recent, "ts": _now_iso()}


@router.get("/frames/{node_id}/latest")
async def frame_latest(node_id: str):
    target = FRAME_DIR / node_id / "latest.jpg"
    if not target.exists():
        raise HTTPException(404, "no frame for node")
    return FileResponse(target, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})


@router.get("/frames/{node_id}")
async def frames_list(node_id: str, limit: int = 20):
    node_dir = FRAME_DIR / node_id
    if not node_dir.exists():
        return {"node_id": node_id, "frames": []}
    files = sorted(node_dir.glob("[0-9]*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "node_id": node_id,
        "frames": [
            {"name": f.name, "ts": int(f.stem), "bytes": f.stat().st_size}
            for f in files[:limit]
        ],
    }


@router.get("/frames/{node_id}/by_ts/{ts}")
async def frame_by_ts(node_id: str, ts: int):
    target = FRAME_DIR / node_id / f"{ts}.jpg"
    if not target.exists():
        raise HTTPException(404, "frame not found")
    return FileResponse(target, media_type="image/jpeg")


@router.get("/healthz")
async def healthz():
    try:
        r.ping()
        ok = True
    except redis.RedisError:
        ok = False
    return JSONResponse({
        "redis":      ok,
        "frame_dir":  str(FRAME_DIR),
        "ts":         _now_iso(),
    }, status_code=200 if ok else 503)
