"""FastAPI router for baza gate — mounted in tools/server.py under /edge/gate."""
import base64
import logging
import os

from fastapi import APIRouter
from pydantic import BaseModel

from gate import face_recognizer, gate_db, session_unlock, unlock_token

log = logging.getLogger("baza.gate.routes")
router = APIRouter(prefix="/edge/gate", tags=["gate"])

# Roles that authorize the physical/login unlock path.
UNLOCK_ROLES = ("door", "login_unlock")
# gate_event kinds logged by this router: "grant" | "deny" | "security" (fail-closed errors).


def _threshold() -> float:
    return float(os.environ.get("BAZA_GATE_FACE_THRESHOLD", "0.35"))


def _publish_auth(person: str) -> None:
    """Emit an authenticated-presence event so agents/dashboard can react.

    Best-effort: a Redis/event-bus hiccup must never block the unlock.
    """
    try:
        from core.event_bus import publish_sync
        publish_sync(agent_id="baza_gate", event_type="agent_alert",
                     data={"target": "all", "message": f"gate: {person} authenticated"})
    except Exception as e:  # noqa: BLE001 - best effort
        log.warning("auth event publish failed: %s", e)


class EnrollBody(BaseModel):
    person: str
    roles: list[str]
    images: list[str]  # base64-encoded JPEGs


class CaptureBody(BaseModel):
    node_id: str
    nonce: str
    image: str  # base64-encoded JPEG


@router.post("/enroll")
def enroll(body: EnrollBody):
    n = 0
    try:
        for img_b64 in body.images:
            raw = base64.b64decode(img_b64)
            for vec in face_recognizer.embed(raw):
                for role in body.roles:
                    gate_db.add_face(body.person, role, vec)
                    n += 1
    except Exception as e:
        log.warning("enroll error (person=%s): %s", body.person, e)
        return {"ok": False, "person": body.person, "error": str(e), "n_embeddings": n}
    return {"ok": True, "person": body.person, "n_embeddings": n}


@router.post("/capture")
def capture(body: CaptureBody):
    raw = base64.b64decode(body.image)
    _save_dir = os.environ.get("GATE_CAPTURE_SAVE_DIR")  # aiming/enroll: save raw frame
    if _save_dir:
        try:
            os.makedirs(_save_dir, exist_ok=True)
            with open(os.path.join(_save_dir, f"cap_{body.nonce[:12]}.jpg"), "wb") as f:
                f.write(raw)
        except Exception as e:  # noqa: BLE001 - never block capture on a save error
            log.warning("capture save failed: %s", e)
    try:
        probes = face_recognizer.embed(raw)
    except Exception as e:  # fail-closed
        log.warning("embed error: %s", e)
        gate_db.log_event(node=body.node_id, kind="security", verdict="deny",
                          detail=f"embed error: {e}")
        return {"verdict": "deny", "token": None}

    if not probes:
        gate_db.log_event(node=body.node_id, kind="deny", verdict="deny",
                          detail="no face")
        return {"verdict": "deny", "token": None}

    gallery = [(p, r, e) for p, r, e in gate_db.gallery_embeddings()
               if r in UNLOCK_ROLES]
    best = (None, None, 0.0)
    try:
        for probe in probes:
            person, role, score = face_recognizer.best_match(probe, gallery, _threshold())
            if person is not None and score > best[2]:
                best = (person, role, score)
    except Exception as e:  # fail-closed: any match error denies
        log.warning("match error: %s", e)
        gate_db.log_event(node=body.node_id, kind="security", verdict="deny",
                          detail=f"match error: {e}")
        return {"verdict": "deny", "token": None}

    person, role, score = best
    if person is None:
        gate_db.log_event(node=body.node_id, kind="deny", verdict="deny",
                          score=score, detail="below threshold")
        return {"verdict": "deny", "token": None}

    try:
        token = unlock_token.sign(body.nonce, action="OPEN")
    except Exception as e:  # malformed nonce etc. -> clean fail-closed deny + audit
        log.warning("nonce sign error (node=%s person=%s): %s", body.node_id, person, e)
        gate_db.log_event(node=body.node_id, kind="security", verdict="deny",
                          person=person, score=score, detail=f"sign error: {e}")
        return {"verdict": "deny", "token": None}
    # Actions are the unlock roles this person is actually enrolled for.
    person_roles = {r for (p, r, _) in gallery if p == person}
    actions = []
    if "login_unlock" in person_roles:
        actions.append("login")
    if "door" in person_roles:
        actions.append("door")
    session_ok = session_unlock.unlock_session() if "login" in actions else None
    _publish_auth(person)
    gate_db.log_event(node=body.node_id, kind="grant", verdict="grant",
                      person=person, score=score,
                      detail=f"actions={actions} session_unlocked={session_ok}")
    return {"verdict": "grant", "person": person, "token": token, "actions": actions}


@router.get("/gallery")
def gallery():
    return gate_db.gallery()


@router.delete("/gallery/{person}")
def delete_person(person: str):
    n = gate_db.delete_person(person)
    return {"ok": True, "removed": n}


@router.get("/events")
def events(limit: int = 50):
    return gate_db.recent_events(limit=limit)
