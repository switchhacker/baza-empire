"""
Baza Empire — Preview Supervisor (sub-project #4.5)

Starts and manages long-running `run` / `preview` processes for Baza Projects
without blocking dashboard requests. Each project can have at most one
running preview at a time.

State lives in:
- `~/baza-empire/projects/<id>/.preview.json`  — pid, port, slot, started_at, command
- `~/baza-empire/projects/<id>/.preview.log`   — stdout+stderr appended

Public API used by dashboard:
- start(project_id, slot="preview") -> dict status
- stop(project_id) -> dict status
- status(project_id) -> dict status (probes pid, returns running=True/False)
- tail_logs(project_id, lines=200) -> str

Port allocation: scans 9000-9099 and picks the first free TCP port.

The supervisor doesn't daemonize per se — it spawns the project's command
with `subprocess.Popen` using `start_new_session=True` so it survives the
parent Flask request, and writes a pidfile we use to reap it later.

Iteration 1 limits:
- No auto-stop / idle reaper. The user (or a future cron) clears stale runs.
- No CPU/mem caps; trusts project commands.
- No reverse-proxy. Preview URL points at the local port directly.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import time
from typing import Any

from core import baza_projects as bp

PORT_RANGE = (9000, 9100)
PIDFILE = ".preview.json"
LOGFILE = ".preview.log"


def _proj_dir(project_id: str) -> str | None:
    proj = bp.get_project(project_id)
    return proj["path"] if proj else None


def _pidfile_path(proj_dir: str) -> str:
    return os.path.join(proj_dir, PIDFILE)


def _logfile_path(proj_dir: str) -> str:
    return os.path.join(proj_dir, LOGFILE)


def _read_pidfile(proj_dir: str) -> dict | None:
    try:
        with open(_pidfile_path(proj_dir)) as f:
            return json.load(f)
    except Exception:
        return None


def _write_pidfile(proj_dir: str, info: dict) -> None:
    with open(_pidfile_path(proj_dir), "w") as f:
        json.dump(info, f, indent=2)


def _delete_pidfile(proj_dir: str) -> None:
    try:
        os.unlink(_pidfile_path(proj_dir))
    except FileNotFoundError:
        pass


def _is_pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


def _free_port() -> int | None:
    for port in range(PORT_RANGE[0], PORT_RANGE[1]):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return None


def status(project_id: str) -> dict[str, Any]:
    proj_dir = _proj_dir(project_id)
    if not proj_dir:
        return {"running": False, "error": "project not found"}
    info = _read_pidfile(proj_dir)
    if not info:
        return {"running": False}
    alive = _is_pid_alive(info.get("pid", 0))
    if not alive:
        # Stale pidfile — clean it up so the user can start fresh.
        _delete_pidfile(proj_dir)
        return {"running": False, "stale_cleaned": True, "last": info}
    return {"running": True, **info}


def start(project_id: str, slot: str = "preview") -> dict[str, Any]:
    proj_dir = _proj_dir(project_id)
    if not proj_dir:
        return {"started": False, "error": "project not found"}
    if slot not in ("run", "preview"):
        return {"started": False, "error": f"slot must be 'run' or 'preview', got {slot!r}"}
    existing = status(project_id)
    if existing.get("running"):
        return {"started": False, "error": "already running", "current": existing}

    proj = bp.get_project(project_id)
    cmds = proj["manifest"].get("commands") or {}
    cmd = (cmds.get(slot) or "").strip()
    if not cmd:
        return {"started": False, "error": f"no command configured for slot={slot}"}

    port = _free_port()
    if port is None:
        return {"started": False, "error": "no free port in range"}

    log_path = _logfile_path(proj_dir)
    # Truncate log on each start for simpler reads
    with open(log_path, "w") as lf:
        lf.write(f"# Baza Project preview log — {project_id} — slot={slot}\n# command: {cmd}\n# port: {port}\n\n")

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["BAZA_PROJECT_ID"] = project_id
    env["BAZA_PROJECT_DIR"] = proj_dir

    log_fd = open(log_path, "a")
    try:
        proc = subprocess.Popen(
            cmd, shell=True, cwd=proj_dir, env=env,
            stdout=log_fd, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as e:
        log_fd.close()
        return {"started": False, "error": f"spawn failed: {e}"}

    info = {
        "pid": proc.pid,
        "pgid": os.getpgid(proc.pid) if hasattr(os, "getpgid") else None,
        "port": port,
        "slot": slot,
        "command": cmd,
        "started_at": _now_iso(),
        "log_path": log_path,
        "url": f"http://localhost:{port}",
    }
    _write_pidfile(proj_dir, info)
    return {"started": True, **info}


def stop(project_id: str, *, hard: bool = False) -> dict[str, Any]:
    proj_dir = _proj_dir(project_id)
    if not proj_dir:
        return {"stopped": False, "error": "project not found"}
    info = _read_pidfile(proj_dir)
    if not info:
        return {"stopped": False, "error": "not running"}
    pid = info.get("pid", 0)
    pgid = info.get("pgid") or pid
    sig = signal.SIGKILL if hard else signal.SIGTERM
    try:
        # Kill the whole process group so node/python child threads die too
        os.killpg(pgid, sig)
    except ProcessLookupError:
        # already dead
        _delete_pidfile(proj_dir)
        return {"stopped": True, "note": "already dead"}
    except Exception as e:
        # fall back to single-pid kill
        try:
            os.kill(pid, sig)
        except Exception as e2:
            return {"stopped": False, "error": f"{e}; fallback: {e2}"}
    # Brief wait; if still alive on graceful, escalate
    if not hard:
        for _ in range(20):
            if not _is_pid_alive(pid):
                break
            time.sleep(0.25)
        if _is_pid_alive(pid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                pass
    _delete_pidfile(proj_dir)
    return {"stopped": True}


def tail_logs(project_id: str, lines: int = 200) -> str:
    proj_dir = _proj_dir(project_id)
    if not proj_dir:
        return ""
    log_path = _logfile_path(proj_dir)
    if not os.path.isfile(log_path):
        return ""
    lines = max(1, min(int(lines), 2000))
    try:
        with open(log_path, "r", errors="replace") as f:
            buf = f.readlines()
        return "".join(buf[-lines:])
    except Exception as e:
        return f"[log read error: {e}]"


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
