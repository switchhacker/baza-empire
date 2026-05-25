#!/usr/bin/env python3
"""Claw Batto — inotify watcher: review files immediately after they are saved.

Watches the active framework + vision trees. On IN_CLOSE_WRITE for source
files, debounces 8s per-path (rapid saves coalesce), then sends the file's
content to Claw's LLM for a focused per-file review. Findings flow into
the same claw_reviews.db with cadence='fs_event'.

Global rate limit: max 1 LLM call per LLM_MIN_GAP_S. Saves while waiting
go onto an in-memory queue keyed by path; only the latest state of each
path is reviewed.
"""
from __future__ import annotations

import os
import re
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from inotify_simple import INotify, flags                  # noqa: E402

from core import claw_review_db as db                       # noqa: E402
from scripts.claw_continuous_review import (                # noqa: E402
    REVIEW_SYSTEM, llm_review, parse_findings, log, run,
)

WATCH_ROOTS = [
    ROOT,                              # agent-framework-v3
    ROOT.parent / "agent-framework-v3-vision",
]

WATCHED_EXT = {
    ".py", ".pyi", ".sh", ".bash", ".yaml", ".yml", ".toml", ".ini",
    ".sql", ".html", ".js", ".jsx", ".ts", ".tsx", ".css",
    ".service", ".timer", ".target",
    ".json", ".md",
}

SKIP_DIRS = {
    ".git", "venv", "__pycache__", "node_modules", "artifacts",
    "logs", "backups", ".private-inbound", ".pytest_cache",
}
SKIP_FILES_RX = re.compile(
    r"(\.db|\.log|\.pyc|\.pyo|\.sqlite|\.sqlite-journal|\.swp|"
    r"_snapshot\.json|claw_reviews\.db)$"
)

DEBOUNCE_S      = 8.0
LLM_MIN_GAP_S   = 12.0          # this process's own floor (parallel daemon has its own)
MAX_FILE_LINES  = 800
MAX_FILE_BYTES  = 60_000

_pending: dict[str, float] = {}     # path → earliest review time (monotonic)
_pending_lock = threading.Lock()
_last_llm_at  = 0.0
_stop = False


def _should_watch_dir(path: Path) -> bool:
    parts = set(path.parts)
    return not (SKIP_DIRS & parts)


def _should_watch_file(path: Path) -> bool:
    if path.suffix.lower() not in WATCHED_EXT:
        return False
    if SKIP_FILES_RX.search(path.name):
        return False
    if SKIP_DIRS & set(path.parts):
        return False
    return True


def _enumerate_dirs(root: Path) -> list[Path]:
    out = [root]
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for d in dirnames:
            out.append(Path(dirpath) / d)
    return out


def _review_file(path: Path) -> None:
    global _last_llm_at
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return
    lines = text.splitlines()
    truncated = ""
    if len(lines) > MAX_FILE_LINES or len(text) > MAX_FILE_BYTES:
        text = "\n".join(lines[:MAX_FILE_LINES])[:MAX_FILE_BYTES]
        truncated = f"\n\n[…truncated; original {len(lines)} lines]"

    rel = path
    for r in WATCH_ROOTS:
        try:
            rel = path.relative_to(r.parent)
            break
        except ValueError:
            continue

    gap = time.time() - _last_llm_at
    if gap < LLM_MIN_GAP_S:
        time.sleep(LLM_MIN_GAP_S - gap)

    user = (
        f"A developer just saved this file. Review it for: security issues "
        f"(secrets in code, unsafe shell/SQL/exec), regression risk, bug "
        f"patterns (race conditions, missing error handling, comment-out "
        f"debris like `---`/`pass # TODO`), excess complexity, outstanding "
        f"TODOs/FIXMEs, and dead code.\n\n"
        f"Path: {rel}\n\n```\n{text}\n```{truncated}"
    )
    try:
        raw = llm_review(REVIEW_SYSTEM, user, max_chars=20_000)
    except Exception as e:
        log(f"fs llm error on {rel}: {e}")
        return
    _last_llm_at = time.time()

    findings = parse_findings(raw)
    inserted = 0
    for f in findings:
        rid = db.add_review(
            target_kind="file", target=str(rel),
            severity=f["severity"], title=f["title"], body=f["body"],
            labels=f["labels"] + ["fs-event"],
            cadence="fs_event",
        )
        if rid:
            inserted += 1
    if inserted:
        log(f"fs_event: {rel} → {inserted} finding(s)")


def _drain_loop() -> None:
    """Pop debounced paths and review them serially."""
    while not _stop:
        now = time.monotonic()
        ready: list[str] = []
        with _pending_lock:
            for p, due in list(_pending.items()):
                if due <= now:
                    ready.append(p)
                    del _pending[p]
        for p in ready:
            try:
                _review_file(Path(p))
            except Exception:
                log(f"_review_file crashed:\n{traceback.format_exc()}")
        time.sleep(1.0)


def main() -> int:
    db.init_db()
    log("claw fs watcher starting")

    ino = INotify()
    wd_to_dir: dict[int, Path] = {}
    mask = flags.CLOSE_WRITE | flags.MOVED_TO | flags.CREATE | flags.MOVED_FROM | flags.DELETE_SELF

    for root in WATCH_ROOTS:
        if not root.exists():
            continue
        for d in _enumerate_dirs(root):
            if not _should_watch_dir(d):
                continue
            try:
                wd = ino.add_watch(str(d), mask)
                wd_to_dir[wd] = d
            except OSError as e:
                log(f"could not watch {d}: {e}")
    log(f"watching {len(wd_to_dir)} directories")

    drain = threading.Thread(target=_drain_loop, daemon=True)
    drain.start()

    def _quit(*_):
        global _stop
        _stop = True
        log("shutdown")

    signal.signal(signal.SIGINT,  _quit)
    signal.signal(signal.SIGTERM, _quit)

    while not _stop:
        events = ino.read(timeout=2000)
        for ev in events:
            base = wd_to_dir.get(ev.wd)
            if base is None:
                continue
            name = ev.name
            if not name:
                continue
            full = base / name
            # newly-created subdir → start watching it
            if ev.mask & flags.ISDIR:
                if (ev.mask & (flags.CREATE | flags.MOVED_TO)) and \
                   _should_watch_dir(full):
                    try:
                        wd = ino.add_watch(str(full), mask)
                        wd_to_dir[wd] = full
                        log(f"now watching new dir: {full}")
                    except OSError:
                        pass
                continue
            if not (ev.mask & (flags.CLOSE_WRITE | flags.MOVED_TO)):
                continue
            if not _should_watch_file(full):
                continue
            with _pending_lock:
                _pending[str(full)] = time.monotonic() + DEBOUNCE_S

    return 0


if __name__ == "__main__":
    sys.exit(main())
