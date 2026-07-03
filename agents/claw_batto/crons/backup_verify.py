#!/usr/bin/env python3
"""Claw Batto — weekly backup verification (test-restore + integrity).

Checks the newest backup under /mnt/empirepool/backups/baza-empire (see
scripts/backup.sh for the real layout: DEST/daily/<STAMP>/ per run, promoted
to DEST/weekly/<STAMP>/ on Sundays, files like baza_projects.db.gz,
baza_agents.dump, configs.tar.gz, artifacts.tar.gz, MANIFEST.txt) for:

  (a) freshness    -- newest dated dir's mtime is <26h old
  (b) size sanity  -- newest backup's total size is >50% of the previous one's
  (c) sqlite integrity -- gunzip the newest baza_projects* file to a tmpdir
      and run `PRAGMA integrity_check` (a raw sqlite binary dump, which is
      what backup.sh's `sqlite3 ... ".backup"` + gzip actually produces); a
      plain-text SQL dump (.sql/.sql.gz) only gets a `gunzip -t`, since
      PRAGMA integrity_check needs a real sqlite file, not SQL text
  (d) postgres dump validity -- baza_agents.dump is pg_dump -Fc (custom
      format, what backup.sh actually writes), verified with `pg_restore -l`;
      a plain-text .sql/.sql.gz dump instead gets a header sniff for the
      standard pg_dump banner

verify(backup_root) is the pure check function -- no Telegram/DB side
effects, just a list of problem strings (empty == clean). All subprocess
access goes through the module-level _sh() so tests monkeypatch it
directly. main() wraps verify() in cron_run()'s heartbeat and routes the
result through send_alert() (problems found) or send_report() (clean).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *  # noqa: F401,F403 (cron_run, send_alert, send_report, log, now, ...)

import glob
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLAW-BACKUP] %(message)s")

AGENT_TOKEN = os.getenv("TELEGRAM_CLAW_BATTO", TELEGRAM_TOKEN)
BACKUP_ROOT = "/mnt/empirepool/backups/baza-empire"
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")
FRESH_MAX_HOURS = 26
SIZE_MIN_RATIO = 0.5


def _sh(cmd: str, timeout: int = 60):
    """Run a shell command, return (returncode, stdout, stderr) as text.
    Never raises -- launch failures/timeouts come back as (1, "", err).
    Module-level so tests monkeypatch it directly (backup_verify._sh)."""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)


def _dated_dirs(backup_root: str):
    """Dated backup dirs under daily/ and weekly/, deduped by stamp name
    (Sunday's weekly promotion is a hardlinked copy of that day's daily dir,
    same stamp -- see scripts/backup.sh step 8), sorted oldest-first as
    (stamp, path). The STAMP format (YYYY-MM-DDTHH-MM-SS) sorts
    lexicographically the same as chronologically."""
    found = {}
    for sub in ("daily", "weekly"):
        base = os.path.join(backup_root, sub)
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            path = os.path.join(base, name)
            if STAMP_RE.match(name) and os.path.isdir(path):
                found.setdefault(name, path)
    return sorted(found.items())


def _dir_size(path: str) -> int:
    """Total size in bytes of all files under path (recursive)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _check_sqlite_backup(path: str) -> str | None:
    """Verify a baza_projects* backup file. Returns None if clean, else a
    problem string. See module docstring for raw-binary vs text-dump
    handling."""
    base = os.path.basename(path)
    is_text_dump = base.endswith(".sql") or base.endswith(".sql.gz")

    if is_text_dump:
        if base.endswith(".gz"):
            rc, out, err = _sh(f"gunzip -t {shlex.quote(path)}")
            if rc != 0:
                return f"gunzip -t failed for {base}: {(err or out).strip()[:200]}"
        return None

    tmpdir = tempfile.mkdtemp(prefix="claw_backup_verify_")
    try:
        if base.endswith(".gz"):
            dest = os.path.join(tmpdir, base[:-3])
            rc, out, err = _sh(f"gunzip -c {shlex.quote(path)} > {shlex.quote(dest)}")
            if rc != 0:
                return f"gunzip failed for {base}: {(err or out).strip()[:200]}"
        else:
            dest = os.path.join(tmpdir, base)
            shutil.copy2(path, dest)

        rc, out, err = _sh(f"sqlite3 {shlex.quote(dest)} 'PRAGMA integrity_check;'")
        result = out.strip().lower()
        if rc != 0 or result != "ok":
            detail = (out.strip() or err.strip())[:200]
            return f"sqlite integrity_check failed for {base}: {detail!r}"
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _check_pg_dump(path: str) -> str | None:
    """Verify the baza_agents postgres dump. Returns None if clean, else a
    problem string. See module docstring for custom-format vs text-dump
    handling."""
    base = os.path.basename(path)
    is_text_dump = base.endswith(".sql") or base.endswith(".sql.gz")

    if is_text_dump:
        if base.endswith(".gz"):
            rc, out, err = _sh(f"gunzip -c {shlex.quote(path)} | head -c 200")
        else:
            rc, out, err = _sh(f"head -c 200 {shlex.quote(path)}")
        if "PostgreSQL database dump" not in out:
            return f"pg dump header check failed for {base}: missing 'PostgreSQL database dump' banner"
        return None

    rc, out, err = _sh(f"pg_restore -l {shlex.quote(path)}")
    if rc != 0:
        return f"pg_restore -l failed for {base} (rc={rc}): {err.strip()[:200]}"
    return None


def verify(backup_root: str) -> list[str]:
    """Pure check: given the backup root dir, return a list of problem
    strings (empty == clean). No Telegram/DB side effects -- main() handles
    reporting."""
    problems: list[str] = []
    dirs = _dated_dirs(backup_root)
    if not dirs:
        problems.append(f"no dated backup directories found under {backup_root}")
        return problems

    stamp, newest_dir = dirs[-1]
    prev = dirs[-2] if len(dirs) > 1 else None

    # (a) freshness
    try:
        age_h = (time.time() - os.path.getmtime(newest_dir)) / 3600
        if age_h > FRESH_MAX_HOURS:
            problems.append(f"newest backup {stamp} is {age_h:.1f}h old (>{FRESH_MAX_HOURS}h)")
    except OSError as e:
        problems.append(f"could not stat newest backup dir {newest_dir}: {e}")

    # (b) size sanity
    newest_size = _dir_size(newest_dir)
    if newest_size == 0:
        problems.append(f"newest backup {stamp} is empty (0 bytes)")
    if prev:
        prev_stamp, prev_dir = prev
        prev_size = _dir_size(prev_dir)
        if prev_size > 0 and newest_size < prev_size * SIZE_MIN_RATIO:
            problems.append(
                f"newest backup {stamp} size {newest_size}B is <50% of previous "
                f"{prev_stamp} size {prev_size}B"
            )

    # (c) sqlite integrity (test-restore)
    sqlite_candidates = sorted(glob.glob(os.path.join(newest_dir, "baza_projects*")))
    if not sqlite_candidates:
        problems.append(f"no baza_projects* backup file found in {stamp}")
    else:
        problem = _check_sqlite_backup(sqlite_candidates[0])
        if problem:
            problems.append(problem)

    # (d) postgres dump validity
    pg_candidates = sorted(glob.glob(os.path.join(newest_dir, "baza_agents*")))
    if not pg_candidates:
        problems.append(f"no baza_agents dump found in {stamp}")
    else:
        problem = _check_pg_dump(pg_candidates[0])
        if problem:
            problems.append(problem)

    return problems


def main():
    with cron_run("backup_verify"):
        log.info("Starting backup verification...")
        problems = verify(BACKUP_ROOT)
        if problems:
            msg = "BACKUP VERIFY — problems found\n\n" + "\n".join(f"- {p}" for p in problems)
            log.warning(msg)
            send_alert("backup_verify", msg, alert_key="backup_verify:fail",
                        renotify_hours=24, token=AGENT_TOKEN)
        else:
            msg = f"Backup verify OK — newest backup checked clean ({now()})"
            log.info(msg)
            send_report("backup_verify", msg, priority="fyi",
                        delta_key="backup_verify", token=AGENT_TOKEN)
        log.info("Done.")


if __name__ == "__main__":
    main()
