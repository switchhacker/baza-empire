"""
Baza Empire — Baza Projects (sub-project #4)

A "Baza Project" is a developer-grade workspace at:
    ~/baza-empire/projects/<project_id>/

Each project has:
- .baza-project.yaml     — manifest (id, name, type, commands, deploy_targets, created_by, created_at)
- .git/                  — auto-init'd repo so every change is reviewable
- README.md              — short description
- artifacts/             — local artifact directory (linked into dashboard/artifacts/<id>/ for Data Hub)
- events.jsonl           — append-only mirror of task_events for this project (best-effort)

Project types (v1):
    web-app | dashboard | esp-firmware | stm-firmware | lora-test | library | other

Only web-app and dashboard kinds get fully functional run/preview/test commands
in this first iteration — manifests for ESP/STM/LoRa are accepted but their
runtime support ships in a follow-up.

Auth model (matches meta-spec D5):
- Reads are open.
- Writes are scoped to the project sandbox dir.
- "Privileged" actions (deploy, flash, systemd registration) emit
  approval_requested events in the visibility pipeline; this module only
  performs the action when caller passes approved=True.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import uuid
from typing import Any

import yaml

logger = logging.getLogger("baza.projects")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK_DIR = os.path.dirname(_THIS_DIR)
EMPIRE_DIR = os.path.dirname(FRAMEWORK_DIR)
PROJECTS_ROOT = os.environ.get(
    "BAZA_PROJECTS_ROOT", os.path.join(EMPIRE_DIR, "projects")
)
DB_PATH = os.environ.get(
    "BAZA_TASK_EVENTS_DB",
    os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db"),
)
DASHBOARD_ARTIFACTS_DIR = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")

PROJECT_KINDS = ("web-app", "dashboard", "esp-firmware", "stm-firmware", "lora-test", "library", "other")
RUNTIME_SUPPORTED = ("web-app", "dashboard", "library")  # commands actually execute for these

MANIFEST_NAME = ".baza-project.yaml"
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")


# ── Schema migration ─────────────────────────────────────────────────────────

def ensure_schema() -> None:
    """Add `kind` column to projects table; create projects-shape index."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN kind TEXT DEFAULT 'legacy-task'")
        except sqlite3.OperationalError:
            pass  # column exists
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN type TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN path TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN created_by TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"baza_projects ensure_schema failed: {e}")


# ── Manifest ─────────────────────────────────────────────────────────────────

def default_manifest(project_id: str, name: str, type_: str, created_by: str = "user") -> dict[str, Any]:
    type_ = type_ or "other"
    if type_ not in PROJECT_KINDS:
        type_ = "other"
    cmds = _default_commands(type_)
    return {
        "id": project_id,
        "name": name,
        "type": type_,
        "kind": "baza-dev",
        "commands": cmds,
        "deploy_targets": _default_deploy_targets(type_),
        "created_by": created_by,
        "created_at": _now_iso(),
        "schema_version": 1,
    }


def _default_commands(type_: str) -> dict[str, str]:
    if type_ == "web-app":
        return {
            "build": "npm install && npm run build",
            "test": "npm test --silent || true",
            "run": "npm run dev",
            "preview": "npm run preview",
            "deploy": "echo 'configure deploy target in manifest'",
        }
    if type_ == "dashboard":
        # Default to a Python/Flask-style dashboard like baza-dashboard itself
        return {
            "build": "[ -d venv ] || python3 -m venv venv; ./venv/bin/pip install -q -r requirements.txt 2>/dev/null || true",
            "test": "./venv/bin/python -m pytest -q || true",
            "run": "./venv/bin/python app.py",
            "preview": "./venv/bin/python app.py",
            "deploy": "echo 'configure deploy target in manifest'",
        }
    if type_ == "library":
        return {
            "build": "python3 -m build || true",
            "test": "python3 -m pytest -q || true",
            "run": "echo 'libraries do not run standalone'",
            "preview": "",
            "deploy": "",
        }
    if type_ == "esp-firmware":
        return {
            "build": "idf.py build",
            "test": "idf.py build && echo 'firmware build OK'",
            "run": "",
            "preview": "",
            "deploy": "echo 'configure deploy target in manifest'",
            "flash": "idf.py -p ${BAZA_FLASH_PORT:-/dev/ttyUSB0} flash",
        }
    if type_ == "stm-firmware":
        return {
            "build": "make",
            "test": "make test || true",
            "run": "",
            "preview": "",
            "deploy": "echo 'configure deploy target in manifest'",
            "flash": "make flash",
        }
    if type_ == "lora-test":
        return {
            "build": "",
            "test": "echo 'wire to hardware test rig'",
            "run": "",
            "preview": "",
            "deploy": "",
            "flash": "echo 'lora-test flash hook — set device-specific command in manifest'",
        }
    return {"build": "", "test": "", "run": "", "preview": "", "deploy": ""}


def _default_deploy_targets(type_: str) -> list[dict]:
    if type_ in ("web-app", "dashboard"):
        return [{"name": "local", "path": "/srv/baza-apps/<id>", "service": ""}]
    return []


# ── Project CRUD ─────────────────────────────────────────────────────────────

def create_project(name: str, type_: str = "other", description: str = "",
                   created_by: str = "user", project_id: str | None = None,
                   template_id: str | None = None) -> dict[str, Any]:
    """Create a new Baza project on disk and register in the projects DB table.

    If `template_id` is given, materialize that template's files into the new
    project after the manifest+README scaffold. The template's declared type
    overrides `type_` if `type_` was the default ("other").
    """
    os.makedirs(PROJECTS_ROOT, exist_ok=True)
    if not project_id:
        project_id = _slug_id(name)
    if not PROJECT_ID_RE.match(project_id):
        raise ValueError(f"Invalid project id: {project_id!r}")
    proj_dir = os.path.join(PROJECTS_ROOT, project_id)
    if os.path.exists(proj_dir):
        raise FileExistsError(f"Project already exists: {project_id}")

    # Template can refine the type so the manifest commands match
    if template_id:
        try:
            from core import baza_project_templates as _tpl
            tpl_type = _tpl.template_type(template_id)
            if tpl_type and (type_ in (None, "", "other")):
                type_ = tpl_type
        except Exception:
            pass

    os.makedirs(proj_dir)
    os.makedirs(os.path.join(proj_dir, "artifacts"), exist_ok=True)

    manifest = default_manifest(project_id, name, type_, created_by=created_by)
    if description:
        manifest["description"] = description
    if template_id:
        manifest["template"] = template_id
    with open(os.path.join(proj_dir, MANIFEST_NAME), "w") as f:
        yaml.safe_dump(manifest, f, sort_keys=False)
    with open(os.path.join(proj_dir, "README.md"), "w") as f:
        f.write(f"# {name}\n\n{description or '(no description yet)'}\n\n"
                f"- Type: `{type_}`\n- Created: {manifest['created_at']}\n- ID: `{project_id}`\n")

    # Materialize template files (best-effort; create an empty project if it fails)
    if template_id:
        try:
            from core import baza_project_templates as _tpl
            _tpl.apply_template(template_id, proj_dir, project_id)
        except Exception as e:
            logger.warning(f"template apply failed for {template_id}: {e}")

    # Touch events.jsonl for project-scoped streaming
    open(os.path.join(proj_dir, "events.jsonl"), "a").close()

    # git init (best-effort — fail open)
    _run_quiet(["git", "init", "-q", "-b", "main"], cwd=proj_dir)
    _run_quiet(["git", "add", "-A"], cwd=proj_dir)
    _run_quiet(
        ["git", "-c", "user.email=baza@local", "-c", "user.name=Baza Project Bootstrap",
         "commit", "-q", "-m", "init: scaffold project"],
        cwd=proj_dir,
    )

    # Symlink artifacts into dashboard/artifacts/<id>/ so Data Hub sees them
    _link_artifacts(project_id, proj_dir)

    # Register in DB projects table
    _db_upsert_project(project_id, name, type_, proj_dir, created_by, description)

    return {
        "id": project_id,
        "name": name,
        "type": type_,
        "path": proj_dir,
        "manifest": manifest,
    }


def list_projects(kind: str = "baza-dev") -> list[dict[str, Any]]:
    """Return projects from filesystem joined with DB metadata. Filter by kind (default baza-dev)."""
    rows: list[dict[str, Any]] = []
    if not os.path.isdir(PROJECTS_ROOT):
        return rows
    for entry in sorted(os.listdir(PROJECTS_ROOT)):
        proj_dir = os.path.join(PROJECTS_ROOT, entry)
        if not os.path.isdir(proj_dir):
            continue
        man_path = os.path.join(proj_dir, MANIFEST_NAME)
        if not os.path.isfile(man_path):
            continue
        try:
            with open(man_path) as f:
                manifest = yaml.safe_load(f) or {}
        except Exception:
            manifest = {}
        if kind and manifest.get("kind") and manifest["kind"] != kind:
            continue
        rows.append({
            "id": entry,
            "name": manifest.get("name", entry),
            "type": manifest.get("type", "other"),
            "kind": manifest.get("kind", "baza-dev"),
            "description": manifest.get("description", ""),
            "created_by": manifest.get("created_by", ""),
            "created_at": manifest.get("created_at", ""),
            "path": proj_dir,
        })
    return rows


def get_project(project_id: str) -> dict[str, Any] | None:
    proj_dir = os.path.join(PROJECTS_ROOT, project_id)
    man_path = os.path.join(proj_dir, MANIFEST_NAME)
    if not os.path.isfile(man_path):
        return None
    try:
        with open(man_path) as f:
            manifest = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"manifest read failed for {project_id}: {e}")
        return None
    git_summary = _git_summary(proj_dir)
    holder = current_lock_holder(project_id)
    return {
        "id": project_id,
        "name": manifest.get("name", project_id),
        "type": manifest.get("type", "other"),
        "kind": manifest.get("kind", "baza-dev"),
        "description": manifest.get("description", ""),
        "manifest": manifest,
        "path": proj_dir,
        "git": git_summary,
        "lock": {"held_by": holder, "is_locked": bool(holder)},
    }


def update_manifest(project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge patch into the manifest. Refuses to change id/created_at."""
    proj_dir = os.path.join(PROJECTS_ROOT, project_id)
    man_path = os.path.join(proj_dir, MANIFEST_NAME)
    if not os.path.isfile(man_path):
        raise FileNotFoundError(project_id)
    with open(man_path) as f:
        manifest = yaml.safe_load(f) or {}
    for k, v in (patch or {}).items():
        if k in ("id", "created_at"):
            continue
        manifest[k] = v
    manifest["updated_at"] = _now_iso()
    with open(man_path, "w") as f:
        yaml.safe_dump(manifest, f, sort_keys=False)
    return manifest


def delete_project(project_id: str, *, hard: bool = False) -> bool:
    proj_dir = os.path.join(PROJECTS_ROOT, project_id)
    if not os.path.isdir(proj_dir):
        return False
    if hard:
        shutil.rmtree(proj_dir, ignore_errors=True)
    else:
        # Soft delete: rename to <id>.deleted-<ts>
        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        os.rename(proj_dir, proj_dir + f".deleted-{ts}")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass
    # Unlink dashboard/artifacts/<id> if it's a symlink we made
    art_link = os.path.join(DASHBOARD_ARTIFACTS_DIR, project_id)
    try:
        if os.path.islink(art_link):
            os.unlink(art_link)
    except Exception:
        pass
    return True


# ── File operations within a project ────────────────────────────────────────

def list_files(project_id: str, subpath: str = "") -> list[dict[str, Any]]:
    base = _safe_join(project_id, subpath)
    if not base or not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name)
        st = os.stat(p)
        out.append({
            "name": name,
            "is_dir": os.path.isdir(p),
            "size": st.st_size,
            "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    return out


def read_file(project_id: str, relpath: str, max_bytes: int = 256 * 1024) -> str | None:
    p = _safe_join(project_id, relpath)
    if not p or not os.path.isfile(p):
        return None
    with open(p, "rb") as f:
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        return data[:max_bytes].decode("utf-8", errors="replace") + "\n…[truncated]"
    return data.decode("utf-8", errors="replace")


def write_file(project_id: str, relpath: str, content: str,
               agent_id: str | None = None, force: bool = False) -> dict[str, Any]:
    p = _safe_join(project_id, relpath)
    if not p:
        raise PermissionError("path escapes project sandbox")
    # Cooperative lock: if a different agent currently holds the lock and
    # the caller didn't pass force=True, refuse the write so two agents
    # don't trample each other's work mid-task.
    if agent_id and not force:
        holder = current_lock_holder(project_id)
        if holder and holder != agent_id:
            raise PermissionError(
                f"project '{project_id}' is currently locked by {holder}; "
                f"caller is {agent_id}. Pass force=True to override (and warn)."
            )
        if not holder:
            acquire_lock(project_id, agent_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    # Refresh the lock heartbeat so it stays held while work continues
    if agent_id:
        acquire_lock(project_id, agent_id)
    return {"path": p, "bytes": len(content.encode("utf-8"))}


# ── Cooperative project ownership/locking (sub-project N) ───────────────────
LOCK_FILENAME = ".baza-lock.json"
LOCK_TTL_SECONDS = int(os.environ.get("BAZA_PROJECT_LOCK_TTL", "1800"))  # 30 min


def _lock_path(project_id: str) -> str | None:
    proj_dir = os.path.join(PROJECTS_ROOT, project_id)
    if not os.path.isdir(proj_dir):
        return None
    return os.path.join(proj_dir, LOCK_FILENAME)


def current_lock_holder(project_id: str) -> str | None:
    """Return the agent_id currently holding the lock, or None if free.

    Stale locks (older than LOCK_TTL_SECONDS) are auto-cleaned so a crashed
    agent doesn't permanently freeze a project.
    """
    p = _lock_path(project_id)
    if not p or not os.path.isfile(p):
        return None
    try:
        with open(p) as f:
            info = json.load(f) or {}
        ts = info.get("acquired_at_ts", 0)
        if isinstance(ts, str):
            try:
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = 0
        import time as _t
        if _t.time() - ts > LOCK_TTL_SECONDS:
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass
            return None
        return info.get("agent_id")
    except Exception:
        return None


def acquire_lock(project_id: str, agent_id: str) -> dict[str, Any]:
    """Acquire/refresh the cooperative lock for this project. If another agent
    holds it (and it's still fresh), this is a no-op + returns holder info."""
    p = _lock_path(project_id)
    if not p:
        return {"ok": False, "error": "project not found"}
    holder = current_lock_holder(project_id)
    if holder and holder != agent_id:
        return {"ok": False, "holder": holder, "yours": False}
    import time as _t
    info = {
        "agent_id": agent_id,
        "acquired_at_ts": _t.time(),
        "acquired_at": _now_iso(),
        "ttl_seconds": LOCK_TTL_SECONDS,
    }
    with open(p, "w") as f:
        json.dump(info, f, indent=2)
    return {"ok": True, "holder": agent_id, "yours": True}


def release_lock(project_id: str, agent_id: str) -> bool:
    """Drop the lock if `agent_id` holds it. Returns True if released."""
    p = _lock_path(project_id)
    if not p or not os.path.isfile(p):
        return False
    holder = current_lock_holder(project_id)
    if holder != agent_id:
        return False
    try:
        os.unlink(p)
        return True
    except Exception:
        return False


# ── Run a command from the manifest ──────────────────────────────────────────

def exec_in_project(project_id: str, command: str, *, timeout: int = 60) -> dict[str, Any]:
    """Run an arbitrary shell command inside the project sandbox dir.

    The command is executed with the project root as cwd. Output and exit
    code are returned. Used by the Explore tab so the user can poke around
    a project's tree without leaving the dashboard.

    NOTE: this is **not** a security boundary against the agent — agents
    already have host shell access via the existing `shell` skill. The
    cwd-pin keeps the user's mental model "I am inside this project" and
    avoids accidental writes to sibling projects.
    """
    proj = get_project(project_id)
    if not proj:
        raise FileNotFoundError(project_id)
    cmd = (command or "").strip()
    if not cmd:
        return {"success": False, "error": "empty command"}
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=proj["path"],
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": (proc.stdout or "")[:8000],
            "stderr": (proc.stderr or "")[:4000],
            "command": cmd,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "success": False, "exit_code": -1, "error": f"timeout after {timeout}s",
            "stdout": (e.stdout or b"").decode("utf-8", errors="replace")[:8000] if isinstance(e.stdout, bytes) else (e.stdout or "")[:8000],
            "stderr": (e.stderr or b"").decode("utf-8", errors="replace")[:4000] if isinstance(e.stderr, bytes) else (e.stderr or "")[:4000],
            "command": cmd,
        }


def run_command(project_id: str, slot: str, *, approved: bool = False, timeout: int = 300) -> dict[str, Any]:
    """Run one of the manifest commands (build|test|run|preview|deploy).

    `deploy` and `flash` are gated — refuses unless approved=True. Returns
    dict with success/output/exit_code. Long-running `run`/`preview` are
    not supported here; use core.preview_supervisor for those.
    """
    proj = get_project(project_id)
    if not proj:
        raise FileNotFoundError(project_id)
    manifest = proj["manifest"]
    cmds = manifest.get("commands") or {}
    cmd = cmds.get(slot, "").strip()
    if not cmd:
        return {"success": False, "error": f"no command configured for slot={slot}"}
    if slot in ("deploy", "flash") and not approved:
        return {"success": False, "error": f"{slot} requires approved=True (privileged)"}
    if slot in ("run", "preview"):
        return {"success": False, "error": f"{slot} is long-running; use core.preview_supervisor.start"}

    proj_dir = proj["path"]
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=proj_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": (proc.stdout or "")[:8000],
            "stderr": (proc.stderr or "")[:4000],
            "command": cmd,
            "slot": slot,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "success": False,
            "error": f"timeout after {timeout}s",
            "stdout": (e.stdout or b"").decode("utf-8", errors="replace")[:8000] if isinstance(e.stdout, bytes) else (e.stdout or "")[:8000],
            "stderr": (e.stderr or b"").decode("utf-8", errors="replace")[:4000] if isinstance(e.stderr, bytes) else (e.stderr or "")[:4000],
            "command": cmd,
            "slot": slot,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_join(project_id: str, relpath: str) -> str | None:
    if not PROJECT_ID_RE.match(project_id or ""):
        return None
    proj_dir = os.path.realpath(os.path.join(PROJECTS_ROOT, project_id))
    target = os.path.realpath(os.path.join(proj_dir, relpath or ""))
    if not target.startswith(proj_dir + os.sep) and target != proj_dir:
        return None
    return target


def _slug_id(name: str) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", (name or "").lower()).strip("-_")
    base = base[:32] if base else "project"
    if not base[0].isalnum():
        base = "p-" + base
    suffix = uuid.uuid4().hex[:6]
    return f"{base}-{suffix}" if not PROJECT_ID_RE.match(base) else f"{base}-{suffix}"


def _now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _run_quiet(argv: list[str], cwd: str) -> int:
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, timeout=30).returncode
    except Exception as e:
        logger.debug(f"run_quiet {argv}: {e}")
        return -1


def git_status(project_id: str) -> dict[str, Any]:
    """Detailed git status for the Develop tab."""
    proj = get_project(project_id)
    if not proj:
        raise FileNotFoundError(project_id)
    proj_dir = proj["path"]
    files = []
    try:
        r = subprocess.run(["git", "status", "--porcelain=v1"], cwd=proj_dir,
                           capture_output=True, text=True, timeout=8)
        for line in (r.stdout or "").splitlines():
            if not line.strip():
                continue
            # Format: "XY path" — XY is 2-char status, then space, then path
            xy = line[:2]
            path = line[3:].strip()
            files.append({
                "status": xy,
                "path": path,
                "staged": xy[0] not in (" ", "?"),
                "modified": xy[1] not in (" ", "?"),
                "untracked": xy == "??",
            })
    except Exception as e:
        return {"error": f"git status failed: {e}", "files": []}
    summary = _git_summary(proj_dir)
    return {"files": files, **summary}


def git_commit(project_id: str, message: str, *, stage_all: bool = True,
               author_name: str = "Baza Project UI",
               author_email: str = "baza@local") -> dict[str, Any]:
    """Commit current changes. By default stages everything first."""
    proj = get_project(project_id)
    if not proj:
        raise FileNotFoundError(project_id)
    proj_dir = proj["path"]
    msg = (message or "").strip()
    if not msg:
        return {"committed": False, "error": "commit message is required"}
    if stage_all:
        try:
            subprocess.run(["git", "add", "-A"], cwd=proj_dir,
                           capture_output=True, text=True, timeout=15, check=True)
        except subprocess.CalledProcessError as e:
            return {"committed": False, "error": f"git add failed: {e.stderr or e}"}
    try:
        r = subprocess.run(
            ["git", "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
             "commit", "-m", msg],
            cwd=proj_dir, capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        return {"committed": False, "error": f"git commit failed: {e}"}
    if r.returncode != 0:
        return {"committed": False, "error": (r.stdout or "") + (r.stderr or ""),
                "exit": r.returncode}
    head_r = subprocess.run(["git", "log", "-1", "--format=%h %s"], cwd=proj_dir,
                            capture_output=True, text=True, timeout=5)
    return {"committed": True, "head": (head_r.stdout or "").strip(),
            "out": (r.stdout or "").strip()}


def _git_summary(proj_dir: str) -> dict[str, Any]:
    out: dict[str, Any] = {"branch": "", "commits": 0, "head": "", "dirty": False}
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=proj_dir,
                           capture_output=True, text=True, timeout=5)
        out["branch"] = (r.stdout or "").strip()
        r = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=proj_dir,
                           capture_output=True, text=True, timeout=5)
        out["commits"] = int((r.stdout or "0").strip() or 0)
        r = subprocess.run(["git", "log", "-1", "--format=%h %s"], cwd=proj_dir,
                           capture_output=True, text=True, timeout=5)
        out["head"] = (r.stdout or "").strip()
        r = subprocess.run(["git", "status", "--porcelain"], cwd=proj_dir,
                           capture_output=True, text=True, timeout=5)
        out["dirty"] = bool((r.stdout or "").strip())
    except Exception:
        pass
    return out


def _link_artifacts(project_id: str, proj_dir: str) -> None:
    try:
        os.makedirs(DASHBOARD_ARTIFACTS_DIR, exist_ok=True)
        link = os.path.join(DASHBOARD_ARTIFACTS_DIR, project_id)
        target = os.path.join(proj_dir, "artifacts")
        if not os.path.exists(link):
            os.symlink(target, link)
    except Exception as e:
        logger.debug(f"link_artifacts skipped: {e}")


def _db_upsert_project(project_id: str, name: str, type_: str, path: str,
                       created_by: str, description: str) -> None:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        ensure_schema()  # idempotent
        conn.execute(
            """
            INSERT INTO projects (id, name, description, status, kind, type, path, created_by, created_at)
            VALUES (?, ?, ?, 'active', 'baza-dev', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, description=excluded.description,
                kind=excluded.kind, type=excluded.type, path=excluded.path,
                created_by=excluded.created_by
            """,
            (project_id, name, description, type_, path, created_by, _now_iso()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"db upsert project failed: {e}")
