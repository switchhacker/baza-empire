#!/usr/bin/env python3
"""Claw Batto — continuous monitor + labelled-review producer.

Long-running daemon with four cadences:

  fast    (60s)  — systemd + processes, no LLM
  medium  (5m)   — new git commits → LLM diff review
  slow    (15m)  — journal errors + stale-TODO scan
  hourly  (60m)  — digest line to ~/Desktop/baza-session-log.md;
                   infra-map snapshot diff (logged, not auto-edited)

Writes labelled findings to dashboard/claw_reviews.db.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import claw_review_db as db                # noqa: E402
from core.ollama_client import chat_stream           # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

MODEL          = "deepseek-coder-v2:16b"     # reviewer stays on fast MoE coder; Claw BOT runs gemma4:26b-a4b-it-qat (agents.yaml)
FRAMEWORK      = ROOT
VISION_DIR     = ROOT.parent / "agent-framework-v3-vision"
HOME           = Path.home()
SESSION_LOG    = HOME / "Desktop" / "baza-session-log.md"
INFRA_SNAPSHOT = ROOT / "dashboard" / ".claw_infra_snapshot.json"
INFRA_DELTAS   = ROOT / "dashboard" / ".claw_infra_deltas.json"
BAZA_MAP       = HOME / ".claude" / "projects" / "-home-switchhacker" / "memory" / "baza-map.md"
BAZA_MAP_BACKUPS = BAZA_MAP.parent / ".backups"
SENTINEL_START = "<!-- claw-auto:infra-deltas-start -->"
SENTINEL_END   = "<!-- claw-auto:infra-deltas-end -->"
DELTAS_MAX     = 50
BACKUP_KEEP    = 7

WATCHED_SERVICES = [
    "baza-dashboard.service", "baza-tool-server.service", "baza-litellm.service",
    "baza-sd-webui.service",  "baza-scaffold-runner.service",
    "baza-task-runner.service", "baza-terminal-bot.service",
    "baza-image-indexer.service", "baza-route-watchdog.service",
    "ollama.service", "ollama-amd.service", "ollama-cpu.service",
] + [f"baza-agent-{n}.service" for n in (
    "claw-batto simon-bately phil-hass sam-axe rex-valor "
    "duke-harmon scout-reeves nova-sterling"
).split()]

SKIP_PATH_RX = re.compile(
    r"/(venv|__pycache__|\.git|node_modules|artifacts|logs|"
    r"\.private-inbound|backups)(/|$)|\.(db|log|pyc|pyo|sqlite|sqlite-journal)$"
)

# Global rate limit for LLM calls (other ticks share this with the fs watcher's
# own process — they each enforce their own; this is per-process only).
_last_llm_at = 0.0
LLM_MIN_GAP_S = 8.0

# ── Helpers ───────────────────────────────────────────────────────────────────

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or now_utc()).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{iso()}] {msg}", flush=True)


def run(cmd: list[str] | str, timeout: int = 20) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd, shell=isinstance(cmd, str), capture_output=True,
            text=True, timeout=timeout,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as e:
        return 1, "", str(e)


def llm_review(system: str, user: str, max_chars: int = 8000) -> str:
    global _last_llm_at
    gap = time.time() - _last_llm_at
    if gap < LLM_MIN_GAP_S:
        time.sleep(LLM_MIN_GAP_S - gap)
    _last_llm_at = time.time()
    chunks = []
    for tok in chat_stream(MODEL, [{"role": "user", "content": user[:max_chars]}],
                           system_prompt=system):
        chunks.append(tok)
        if sum(len(c) for c in chunks) > 12000:
            break
    return "".join(chunks)


def parse_findings(text: str) -> list[dict]:
    """Try to recover a JSON envelope; else fall back to one unstructured note."""
    m = re.search(r"\{[\s\S]*\"findings\"\s*:\s*\[[\s\S]*\]\s*\}", text)
    if m:
        try:
            obj = json.loads(m.group(0))
            out = []
            for f in obj.get("findings", []):
                sev = (f.get("severity") or "info").lower()
                if sev not in db.VALID_SEVERITY:
                    sev = "info"
                title = (f.get("title") or "").strip()[:200]
                body  = (f.get("body")  or "").strip()
                labs  = [str(x).strip().lower() for x in (f.get("labels") or [])]
                if title and body:
                    out.append({"severity": sev, "title": title,
                                "body": body, "labels": labs})
            if out:
                return out
        except Exception:
            pass
    text = text.strip()
    if not text:
        return []
    return [{"severity": "info", "title": "Claw review (unstructured)",
             "body": text[:4000], "labels": ["unstructured"]}]


# ── Tick: fast (services + processes) ─────────────────────────────────────────

REVIEW_SYSTEM = (
    "You are Claw Batto, senior Linux/devops engineer reviewing the Baza Empire codebase. "
    "Output a JSON object only — no prose around it. Schema: "
    '{"findings":[{"severity":"info|warn|bug|regression|security",'
    '"title":"<=160 chars","body":"markdown-friendly explanation",'
    '"labels":["short","tags"]}]}. '
    "Be terse, signal over noise. If nothing is wrong, return "
    '{"findings":[]}. Never invent file paths — only label what you can see. '
    "Common labels: security, secret-leak, regression-risk, complexity-high, "
    "bug-pattern, todo-followup, untested, dead-code, perf, race, "
    "service-flap, process-orphan, schema-drift."
)


def tick_fast() -> None:
    # systemd
    rc, out, _ = run(["systemctl", "list-units", "--failed", "--no-legend", "--plain"])
    if rc == 0 and out.strip():
        for line in out.strip().splitlines():
            unit = line.split()[0]
            db.add_review(
                target_kind="service", target=unit,
                severity="warn",
                title=f"systemd unit failed: {unit}",
                body=line,
                labels=["service-flap", "systemd"],
                cadence="fast",
            )

    # explicit baza services — only flag failed/activating; many are oneshots
    # driven by .timer units so "inactive" is the steady state between firings.
    for svc in WATCHED_SERVICES:
        rc, out, _ = run(["systemctl", "is-active", svc])
        state = out.strip()
        if state in ("failed", "activating"):
            db.add_review(
                target_kind="service", target=svc,
                severity="bug" if state == "failed" else "warn",
                title=f"{svc} is {state}",
                body=f"`systemctl is-active {svc}` reported `{state}`.",
                labels=["service-down", state],
                cadence="fast",
            )

    # runaway processes (>150% CPU)
    rc, out, _ = run(["ps", "-eo", "pid,pcpu,pmem,etimes,cmd", "--sort=-pcpu"])
    if rc == 0:
        for line in out.splitlines()[1:21]:
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            try:
                pcpu = float(parts[1]); etimes = int(parts[3])
            except ValueError:
                continue
            cmd = parts[4]
            # ignore browser/desktop
            if any(skip in cmd for skip in ("/firefox", "/chrome", "/code", "gnome-shell")):
                continue
            if pcpu >= 150 and etimes >= 300:
                db.add_review(
                    target_kind="process", target=parts[0],
                    severity="warn",
                    title=f"pid {parts[0]} pegged at {pcpu:.0f}% CPU for {etimes//60}m",
                    body=f"`{cmd}`",
                    labels=["process-runaway", "perf"],
                    cadence="fast",
                )

    # orphan receipt OCR children (Apr 2026 incident)
    rc, out, _ = run(["pgrep", "-fa", "receipt_ocr.py"])
    if rc == 0:
        lines = [l for l in out.splitlines() if l.strip()]
        if len(lines) > 3:
            db.add_review(
                target_kind="process", target="receipt_ocr.py",
                severity="bug",
                title=f"{len(lines)} receipt_ocr.py procs alive (orphan storm?)",
                body="```\n" + "\n".join(lines[:20]) + "\n```",
                labels=["process-orphan", "regression-risk"],
                cadence="fast",
            )


# ── Tick: medium (git commits) ────────────────────────────────────────────────

def _new_commits(repo: Path, cursor_name: str) -> list[tuple[str, str, str]]:
    """Return [(sha, subject, author), ...] since last seen, newest last."""
    if not (repo / ".git").exists():
        return []
    last = db.get_cursor(cursor_name, "")
    rng  = f"{last}..HEAD" if last else "HEAD~10..HEAD"
    rc, out, _ = run(["git", "-C", str(repo), "log", rng,
                      "--pretty=format:%H%x09%s%x09%an"])
    if rc != 0 or not out.strip():
        return []
    rows = []
    for line in out.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return list(reversed(rows))


def tick_medium() -> None:
    for repo, cur in [(FRAMEWORK, "git_framework"), (VISION_DIR, "git_vision")]:
        if not repo.exists():
            continue
        commits = _new_commits(repo, cur)
        for sha, subj, author in commits:
            rc, diff, _ = run(["git", "-C", str(repo), "show", "--stat",
                               "--no-color", sha], timeout=30)
            if rc != 0:
                continue
            user = (
                f"Review this commit for security issues, regression risk, "
                f"bug patterns, complexity hot-spots, and outstanding TODOs.\n\n"
                f"Repo: {repo.name}\nAuthor: {author}\nSubject: {subj}\nSHA: {sha}\n\n"
                f"```diff\n{diff[:6000]}\n```"
            )
            try:
                raw = llm_review(REVIEW_SYSTEM, user)
            except Exception as e:
                log(f"llm error on {sha}: {e}")
                continue
            for f in parse_findings(raw):
                db.add_review(
                    target_kind="commit", target=f"{repo.name}@{sha[:12]}",
                    severity=f["severity"], title=f["title"], body=f["body"],
                    labels=f["labels"] + ["commit-review"],
                    cadence="medium",
                    meta={"sha": sha, "author": author, "subject": subj},
                )
            db.set_cursor(cur, sha)
            log(f"medium: reviewed {repo.name}@{sha[:12]} → {len(parse_findings(raw))} findings")


# ── Tick: slow (journal errors + TODO scan) ───────────────────────────────────

ERR_RX = re.compile(r"(ERROR|Exception|Traceback|CRITICAL)", re.I)


def tick_slow() -> None:
    rc, out, _ = run([
        "journalctl", "--since", "15 min ago", "--no-pager",
        "-u", "baza-*", "-p", "warning",
    ], timeout=20)
    if rc == 0 and out.strip():
        hits = [l for l in out.splitlines() if ERR_RX.search(l)]
        for line in hits[:25]:
            m = re.search(r"]\s+([\w\-\.]+):", line)
            unit = m.group(1) if m else "baza-unknown"
            db.add_review(
                target_kind="log", target=unit,
                severity="warn",
                title=f"journal error in {unit}",
                body=line.strip()[:600],
                labels=["log-error", "journal"],
                cadence="slow",
            )

    # stale TODO/FIXME/XXX/HACK in recently modified files
    rc, out, _ = run(
        f"find {FRAMEWORK} -type f \\( -name '*.py' -o -name '*.js' "
        f"-o -name '*.html' -o -name '*.sh' \\) "
        f"-mmin -360 -not -path '*/venv/*' -not -path '*/__pycache__/*' "
        f"-not -path '*/.git/*' -not -path '*/artifacts/*'"
    )
    if rc != 0:
        return
    files = [Path(f) for f in out.splitlines() if f.strip()][:30]
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        marks = [(i + 1, line) for i, line in enumerate(text.splitlines())
                 if re.search(r"\b(FIXME|XXX|HACK)\b", line)]
        if not marks:
            continue
        rel = f.relative_to(FRAMEWORK.parent)
        body = "\n".join(f"L{n}: {l.strip()[:140]}" for n, l in marks[:8])
        db.add_review(
            target_kind="file", target=str(rel),
            severity="info",
            title=f"{len(marks)} FIXME/XXX/HACK markers in {rel.name}",
            body=f"```\n{body}\n```",
            labels=["todo-followup", "tech-debt"],
            cadence="slow",
        )


# ── Tick: hourly (digest + infra snapshot) ────────────────────────────────────

def _infra_snapshot() -> dict:
    snap = {"services": {}, "timers": {}, "ollama_models": [], "top_dirs": []}
    rc, out, _ = run(["systemctl", "list-units", "--type=service",
                      "--state=loaded", "--no-legend", "--plain", "baza-*"])
    if rc == 0:
        for line in out.splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 4:
                snap["services"][parts[0]] = parts[3]  # active/inactive/...
    rc, out, _ = run(["systemctl", "list-timers", "--all", "--no-legend",
                      "--plain"])
    if rc == 0:
        for line in out.splitlines():
            parts = line.split(None, 5)
            if len(parts) >= 5 and "baza" in parts[-2]:
                snap["timers"][parts[-2]] = parts[-1] if len(parts) > 5 else ""
    rc, out, _ = run("curl -s --max-time 4 http://localhost:11434/api/tags")
    if rc == 0:
        try:
            snap["ollama_models"] = sorted(
                m["name"] for m in json.loads(out).get("models", [])
            )
        except Exception:
            pass
    if FRAMEWORK.exists():
        snap["top_dirs"] = sorted(
            p.name for p in FRAMEWORK.iterdir()
            if p.is_dir() and not p.name.startswith(".")
            and p.name not in {"venv", "__pycache__", "node_modules",
                               "logs", "backups", "artifacts"}
        )
    return snap


def _load_deltas() -> list[dict]:
    if not INFRA_DELTAS.exists():
        return []
    try:
        data = json.loads(INFRA_DELTAS.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_deltas(deltas: list[dict]) -> None:
    deltas = deltas[-DELTAS_MAX:]
    tmp = INFRA_DELTAS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(deltas, indent=2))
    tmp.replace(INFRA_DELTAS)


def _render_deltas_md(deltas: list[dict]) -> str:
    if not deltas:
        return ("_no deltas recorded yet — Claw will append here when "
                "services/timers/ollama models/framework dirs change_")
    # newest first for readability
    by_day: dict[str, list[str]] = {}
    for entry in reversed(deltas):
        day = entry["ts"][:10]
        by_day.setdefault(day, []).append(
            f"- `{entry['ts'][11:16]}Z` {entry['change']}"
        )
    out: list[str] = []
    for day in sorted(by_day.keys(), reverse=True):
        out.append(f"\n**{day}**")
        out.extend(by_day[day])
    return "\n".join(out).lstrip("\n")


def _backup_baza_map() -> None:
    BAZA_MAP_BACKUPS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BAZA_MAP_BACKUPS / f"baza-map.md.{ts}.bak"
    try:
        dest.write_text(BAZA_MAP.read_text())
    except Exception as e:
        log(f"baza-map backup failed: {e}")
        return
    # rotate
    backups = sorted(BAZA_MAP_BACKUPS.glob("baza-map.md.*.bak"))
    for old in backups[:-BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def _update_baza_map(deltas: list[dict]) -> tuple[bool, str]:
    """Atomically rewrite only the content between the sentinels.

    Returns (applied, message). Never edits anything outside the sentinels.
    """
    if not BAZA_MAP.exists():
        return False, f"baza-map.md not found at {BAZA_MAP}"
    try:
        text = BAZA_MAP.read_text()
    except Exception as e:
        return False, f"read failed: {e}"
    if SENTINEL_START not in text or SENTINEL_END not in text:
        return False, "sentinels missing — add them to baza-map.md to enable auto-edit"
    pre,  rest = text.split(SENTINEL_START, 1)
    _mid, post = rest.split(SENTINEL_END, 1)
    new_mid = "\n" + _render_deltas_md(deltas) + "\n"
    new_text = pre + SENTINEL_START + new_mid + SENTINEL_END + post
    if new_text == text:
        return False, "no change"
    _backup_baza_map()
    tmp = BAZA_MAP.with_suffix(".md.tmp")
    try:
        tmp.write_text(new_text)
        os.replace(tmp, BAZA_MAP)
    except Exception as e:
        if tmp.exists():
            try: tmp.unlink()
            except OSError: pass
        return False, f"atomic write failed: {e}"
    return True, f"wrote {len(deltas)} delta(s)"


def tick_hourly() -> None:
    one_hour_ago = iso(now_utc() - timedelta(hours=1))
    counts = db.severity_counts(since_ts=one_hour_ago)
    total = sum(counts.values())

    # session log digest
    digest_line = (
        f"### {datetime.now().strftime('%Y-%m-%d %H:%M')} | Claw hourly digest — "
        f"{total} new ({counts['bug']} bug, {counts['regression']} regression, "
        f"{counts['security']} security, {counts['warn']} warn, {counts['info']} info)"
    )
    try:
        with SESSION_LOG.open("a") as f:
            f.write("\n" + digest_line + "\n")
    except Exception as e:
        log(f"could not append to session log: {e}")

    # infra delta detection
    snap = _infra_snapshot()
    first_run = not INFRA_SNAPSHOT.exists()
    prev = {}
    if not first_run:
        try:
            prev = json.loads(INFRA_SNAPSHOT.read_text())
        except Exception:
            prev = {}
    diffs = []
    for key in ("services", "timers"):
        a, b = set(prev.get(key, {})), set(snap.get(key, {}))
        for added in sorted(b - a):
            diffs.append(f"+ {key}: {added}")
        for removed in sorted(a - b):
            diffs.append(f"- {key}: {removed}")
    a, b = set(prev.get("ollama_models", [])), set(snap.get("ollama_models", []))
    for added in sorted(b - a):
        diffs.append(f"+ ollama_model: {added}")
    for removed in sorted(a - b):
        diffs.append(f"- ollama_model: {removed}")
    a, b = set(prev.get("top_dirs", [])), set(snap.get("top_dirs", []))
    for added in sorted(b - a):
        diffs.append(f"+ framework_dir: {added}")
    for removed in sorted(a - b):
        diffs.append(f"- framework_dir: {removed}")

    if first_run:
        log(f"hourly: first snapshot (baseline {len(snap.get('services', {}))} services, "
            f"{len(snap.get('ollama_models', []))} models) — silently baselining, no deltas")
        diffs = []

    if diffs:
        # append to rolling list (persisted) before re-rendering baza-map
        rolling = _load_deltas()
        ts = iso()
        for d in diffs:
            rolling.append({"ts": ts, "change": d})
        _save_deltas(rolling)

        applied, msg = _update_baza_map(rolling)
        labels = ["infra-map", "delta"]
        if applied:
            labels.append("auto-applied")
        else:
            labels.append("auto-skipped")

        db.add_review(
            target_kind="infra", target="baza_host",
            severity="info",
            title=f"infra delta ({len(diffs)} change{'s' if len(diffs) != 1 else ''}) — {msg}",
            body="```\n" + "\n".join(diffs[:50]) + "\n```",
            labels=labels,
            cadence="hourly",
            meta={"diffs": diffs, "baza_map_applied": applied, "baza_map_msg": msg},
        )
        log(f"hourly: baza-map auto-edit → {applied} ({msg})")

    try:
        INFRA_SNAPSHOT.write_text(json.dumps(snap, indent=2))
    except Exception as e:
        log(f"could not write infra snapshot: {e}")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> int:
    db.init_db()
    log(f"claw continuous review starting; db={db.DB_PATH}")
    log(f"session log = {SESSION_LOG}")

    cadences = {
        "fast":   {"interval": 60,   "next": 0.0, "fn": tick_fast},
        "medium": {"interval": 300,  "next": 30.0, "fn": tick_medium},
        "slow":   {"interval": 900,  "next": 90.0, "fn": tick_slow},
        "hourly": {"interval": 3600, "next": 180.0, "fn": tick_hourly},
    }
    start = time.monotonic()
    stop = False

    def _quit(*_):
        nonlocal stop
        stop = True
        log("shutdown signal — exiting after current tick")

    signal.signal(signal.SIGINT,  _quit)
    signal.signal(signal.SIGTERM, _quit)

    while not stop:
        now = time.monotonic() - start
        for name, c in cadences.items():
            if now >= c["next"]:
                try:
                    c["fn"]()
                except Exception:
                    log(f"{name} tick crashed:\n{traceback.format_exc()}")
                c["next"] = (time.monotonic() - start) + c["interval"]
        time.sleep(5)

    return 0


if __name__ == "__main__":
    sys.exit(main())
