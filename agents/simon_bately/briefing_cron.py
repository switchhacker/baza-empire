#!/usr/bin/env python3
"""
Simon Bately — Dynamic Team Commander Briefing
Runs every 2 hours via cron. Pulls LIVE data on entire team state,
project progress, blockers, weather — and tells Serge
exactly where the empire stands and what Simon is commanding the team to do.
"""
import os, re, sys, json, glob, logging, sqlite3, subprocess, datetime, urllib.request
from pathlib import Path

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FRAMEWORK_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(FRAMEWORK_DIR, "configs", "secrets.env"))

from core.skills_engine import SkillsEngine
from core.weather_sources import get_forecast
from core.weather_rules import WIND_SUSTAINED_MPH
from agents.cron_helpers import cron_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SIMON] %(message)s")
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_SIMON_BATELY", "8259565938:AAFCNLSrw096JALxvgmiBCkgByn0uDyGGMo")
SERGE_CHAT_ID  = os.getenv("SERGE_CHAT_ID", "8551331144")
OLLAMA_URLS    = [
    os.getenv("OLLAMA_HOST", "http://localhost:11434"),
    "http://localhost:11437",  # ollama-amd (AMD RX 6700 XT, Vulkan)
    "http://localhost:11436",  # ollama-cpu (CPU fallback)
]
OLLAMA_TIMEOUT = int(os.getenv("SIMON_LLM_TIMEOUT", "120"))  # was 300 — fall over to sibling GPU faster
MODEL          = "qwen2.5:14b"  # matches phil_hass — stays warm in 11434

AGENTS = [
    ("simon_bately",  "Simon",  "Co-CEO / BizOps"),
    ("claw_batto",    "Claw",   "Dev / DevOps"),
    ("phil_hass",     "Phil",   "Legal / Finance"),
    ("sam_axe",       "Sam",    "Design / Marketing"),
    ("duke_harmon",   "Duke",   "Project Manager"),
    ("rex_valor",     "Rex",    "Voicemail / Intake"),
    ("scout_reeves",  "Scout",  "Research"),
    ("nova_sterling", "Nova",   "Client Chat"),
]

ARTIFACTS_DIR = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")
IN_PROGRESS_STATUS = "In Progress"
DUKE_TASK_ARTIFACT_GLOB = "proj-baza-empire/task_manager_*.md"
TASK_EXCERPT_MAX_CHARS = 2000
FYI_CAP = 10

# ── Data collection ────────────────────────────────────────────────────────────

def read_recent_artifact(project_dir_glob: str, max_age_h: float = 12.0) -> str | None:
    """Newest file under dashboard/artifacts/ matching project_dir_glob (a
    glob pattern relative to ARTIFACTS_DIR, e.g. "proj-baza-empire/task_manager_*.md"),
    read and returned as text. None if nothing matches, the newest match's
    mtime is older than max_age_h hours, or the file can't be read.

    Pure-ish and testable: ARTIFACTS_DIR is a module global resolved fresh
    on every call (monkeypatchable — same pattern core/claim_verifier.py
    uses for its own ARTIFACTS_DIR); "now" is real wall-clock time, so
    tests control freshness via each fixture file's mtime (os.utime)
    rather than an injected clock. Never raises — any glob/stat/read
    failure degrades to None, logged.
    """
    try:
        pattern = os.path.join(ARTIFACTS_DIR, project_dir_glob)
        matches = [p for p in glob.glob(pattern) if os.path.isfile(p) and not p.endswith(".meta")]
        if not matches:
            return None
        newest = max(matches, key=os.path.getmtime)
        age_h = (datetime.datetime.now().timestamp() - os.path.getmtime(newest)) / 3600.0
        if age_h > max_age_h:
            return None
        with open(newest, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        log.warning(f"read_recent_artifact({project_dir_glob!r}) failed: {e}")
        return None


def _biz_db_path() -> str | None:
    """Resolve dashboard/baza_projects.db (or the framework-root fallback
    copy), or None if neither exists. Shared by get_tasks_summary's
    recompute fallback and main()'s per-site weather wiring."""
    db_candidates = [
        os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db"),
        os.path.join(FRAMEWORK_DIR, "baza_projects.db"),
    ]
    return next((p for p in db_candidates if os.path.exists(p)), None)

def get_service_status(agent_id: str) -> str:
    svc = f"baza-agent-{agent_id.replace('_','-')}"
    try:
        r = subprocess.run(["systemctl","is-active", svc],
                           capture_output=True, text=True, timeout=3)
        return "🟢 online" if r.stdout.strip() == "active" else "🔴 offline"
    except:
        return "❓ unknown"

def get_team_status() -> str:
    lines = ["TEAM STATUS:"]
    for agent_id, name, role in AGENTS:
        status = get_service_status(agent_id)
        lines.append(f"  {name} ({role}): {status}")
    return "\n".join(lines)

def get_tasks_summary() -> str:
    """Fetch live tasks from local baza_projects.db SQLite.

    Prefers a fresh Duke project_tracker artifact (task_manager_*.md, every
    4h — see config/agents.yaml — well within read_recent_artifact's 12h
    default staleness window) over this recompute: project_tracker already
    runs this exact tasks/projects query and narrates it, so redoing it
    here on every briefing cycle is pure duplication when a recent run
    exists. Falls back to the live recompute below whenever Duke's
    artifact is missing or stale, so briefings never silently go dark on
    task status if project_tracker itself is broken or running late.
    """
    fresh = read_recent_artifact(DUKE_TASK_ARTIFACT_GLOB)
    if fresh:
        excerpt = fresh.strip()
        if len(excerpt) > TASK_EXCERPT_MAX_CHARS:
            excerpt = excerpt[:TASK_EXCERPT_MAX_CHARS] + "\n… (truncated)"
        return "TASK BOARD (from Duke's last project_tracker run):\n" + excerpt

    db_path = _biz_db_path()
    if not db_path:
        return "TASKS: local DB not found"

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT t.title, t.status, t.priority, t.assigned_to, t.notes,
                   t.updated_at, p.name as project_name
            FROM tasks t
            LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.is_subtask = 0
            ORDER BY t.updated_at DESC
            LIMIT 60
        """)
        tasks = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        log.warning(f"Local tasks DB failed: {e}")
        return "TASKS: DB read error"

    if not tasks:
        return "TASKS: no tasks found"

    by_status = {}
    for t in tasks:
        s = (t.get("status") or "pending").lower()
        by_status.setdefault(s, []).append(t)

    total   = len(tasks)
    done    = len(by_status.get("completed", []) + by_status.get("done", []))
    blocked = len(by_status.get("blocked", []))
    in_prog = len(by_status.get("in_progress", []))
    pending = len(by_status.get("pending", []))
    pct     = int(done / total * 100) if total else 0

    lines = [f"TASK BOARD: {total} tasks | {done} done ({pct}%) | {in_prog} active | {blocked} BLOCKED | {pending} pending"]

    if by_status.get("blocked"):
        lines.append("\n  🚫 BLOCKED — SERGE ACTION NEEDED:")
        for t in by_status["blocked"][:5]:
            proj = t.get("project_name") or "?"
            lines.append(f"    [{t.get('assigned_to','?')}] {t.get('title','?')[:70]} ({proj})")
            if t.get("notes"):
                lines.append(f"      → {t['notes'][:80]}")

    if by_status.get("in_progress"):
        lines.append("\n  🔄 IN PROGRESS:")
        for t in by_status["in_progress"][:6]:
            proj = t.get("project_name") or "?"
            lines.append(f"    [{t.get('assigned_to','?')}] {t.get('title','?')[:70]} ({proj})")

    high_pending = [t for t in by_status.get("pending", []) if t.get("priority") == "high"]
    if high_pending:
        lines.append("\n  ⏳ HIGH PRIORITY PENDING:")
        for t in high_pending[:4]:
            lines.append(f"    [{t.get('assigned_to','?')}] {t.get('title','?')[:70]}")

    return "\n".join(lines)

def get_recent_activity() -> str:
    """Read recent agent messages from the Baza PostgreSQL context DB."""
    try:
        from core.context_db import _get_pool
        pool = _get_pool()
        conn = pool.getconn()
        cur = conn.cursor()
        cur.execute("""
            SELECT agent_id, content, created_at
            FROM messages
            WHERE role = 'assistant'
            ORDER BY created_at DESC
            LIMIT 30
        """)
        rows = cur.fetchall()
        cur.close()
        pool.putconn(conn)
        if not rows:
            return "RECENT ACTIVITY: no messages found"
        lines = ["RECENT AGENT ACTIVITY:"]
        seen = set()
        for agent_id, content, created_at in rows:
            if agent_id in seen:
                continue
            seen.add(agent_id)
            text = (content or "")[:120].replace("\n", " ")
            ts = str(created_at)[:16] if created_at else ""
            lines.append(f"  {agent_id}: {text} [{ts}]")
            if len(seen) >= 8:
                break
        return "\n".join(lines)
    except Exception as e:
        return f"RECENT ACTIVITY: error reading context DB ({str(e)[:80]})"

def get_artifacts_summary() -> str:
    """Use the canonical briefing_data skill for the Recent Wins source.

    This is the ONLY source Simon should cite as a "win" — it shows real
    files modified in the last 2 hours. If empty, the briefing must say
    so plainly. No invented completions.
    """
    arts_dir = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")
    if not os.path.exists(arts_dir): return "ARTIFACTS_REAL_LAST_2H: none"
    cutoff = datetime.datetime.now().timestamp() - 7200  # last 2 hours (briefing window)
    rows = []
    for proj in os.listdir(arts_dir):
        proj_dir = os.path.join(arts_dir, proj)
        if not os.path.isdir(proj_dir): continue
        for fname in os.listdir(proj_dir):
            if fname.endswith('.meta'): continue
            fpath = os.path.join(proj_dir, fname)
            if not os.path.isfile(fpath): continue
            mt = os.path.getmtime(fpath)
            if mt < cutoff: continue
            # Try to read the .meta sidecar for agent attribution
            agent = ""
            try:
                with open(fpath + ".meta") as mf:
                    agent = (json.load(mf) or {}).get("agent_id", "")
            except Exception:
                head = fname.split("_", 2)
                if len(head) >= 2 and head[0] in ("simon","claw","sam","nova","phil","rex","duke","scout"):
                    agent = "_".join(head[:2])
            rows.append((mt, proj, fname, agent, os.path.getsize(fpath)))
    rows.sort(reverse=True)
    if not rows:
        return ("ARTIFACTS_REAL_LAST_2H: NONE — no files saved in the last 2h.\n"
                "  → Briefing must say 'no completed deliverables this cycle' "
                "and NOT invent wins.")
    lines = [f"ARTIFACTS_REAL_LAST_2H: {len(rows)} file(s) — these are the ONLY items you may cite as Recent Wins:"]
    for mt, proj, fname, agent, size in rows[:15]:
        ts = datetime.datetime.fromtimestamp(mt).strftime("%H:%M")
        lines.append(f"  - {proj}/{fname} ({size//1024} KB, {agent or 'unknown'}, {ts})")
    return "\n".join(lines)

# ── Per-jobsite weather (replaces the old hardcoded-Philadelphia block) ─────

def _weather_site_label(row) -> str:
    """Compact site name for a weather one-liner: project title if set,
    else address, else a bare project-id fallback."""
    title = (row["title"] or "").strip()
    if title:
        return title
    address = (row["address"] or "").strip()
    return address or f"project {row['id']}"


def _philadelphia_fallback_weather() -> str:
    """Literal pre-existing wttr.in-backed 'weather' skill call, kept as-is
    for the case where there are zero geocoded active jobsites — simplest
    correct fallback, unchanged behavior from before this task."""
    try:
        skills = SkillsEngine(FRAMEWORK_DIR)
        r = skills.run("weather", {"location": "Philadelphia, PA"})
        return r.get("output", "WEATHER: unavailable") if r.get("success") else "WEATHER: unavailable"
    except Exception as e:
        log.warning(f"Philadelphia fallback weather skill failed: {e}")
        return "WEATHER: unavailable"


def build_site_weather_section(conn) -> str:
    """One compact line per distinct geocoded active-jobsite coordinate:
    site name, today's hi/lo, precip probability, and a wind flag.

    Active = ahb_projects.status == 'In Progress' with non-null
    latitude/longitude (already-geocoded — this function never geocodes;
    that's core.geocode.ensure_project_coords's job elsewhere). Coordinates
    are deduped by (round(lat,2), round(lon,2)) before calling
    get_forecast — the same dedupe pattern as
    agents/duke_harmon/crons/weather_watch.py's fetch_cache, so sites
    sharing a block only fetch once.

    Falls back to the literal Philadelphia line when there are zero
    geocoded active sites (including on a DB error — we can't tell either
    way, so the safest degrade is the same fallback) — NOT when a forecast
    fetch merely fails for one or some coordinates, which just drops that
    line (logged) and keeps going with the rest.
    """
    try:
        rows = conn.execute(
            "SELECT id, title, address, latitude, longitude FROM ahb_projects "
            "WHERE status = ? AND latitude IS NOT NULL AND longitude IS NOT NULL "
            "ORDER BY id",
            (IN_PROGRESS_STATUS,),
        ).fetchall()
    except Exception as e:
        log.warning(f"build_site_weather_section: site query failed: {e}")
        rows = []

    if not rows:
        return _philadelphia_fallback_weather()

    coords = {}
    order = []
    for row in rows:
        try:
            lat, lon = float(row["latitude"]), float(row["longitude"])
        except (TypeError, ValueError):
            continue
        key = (round(lat, 2), round(lon, 2))
        if key not in coords:
            coords[key] = []
            order.append(key)
        coords[key].append(_weather_site_label(row))

    if not order:
        return _philadelphia_fallback_weather()

    lines = ["JOBSITE WEATHER:"]
    for key in order:
        lat, lon = key
        try:
            forecast = get_forecast(lat, lon)
        except Exception as e:
            log.warning(f"build_site_weather_section: get_forecast failed for {key}: {e}")
            forecast = None

        label = " / ".join(coords[key])
        daily = (forecast or {}).get("daily") or []
        today = daily[0] if daily else None
        if not today:
            lines.append(f"  ⚠️ {label}: forecast unavailable")
            continue

        hi, lo = today.get("high_f"), today.get("low_f")
        precip = today.get("precip_prob_max")
        wind = today.get("wind_mph")
        hi_s = f"{hi:.0f}°" if isinstance(hi, (int, float)) else "?°"
        lo_s = f"{lo:.0f}°" if isinstance(lo, (int, float)) else "?°"
        precip_s = f"{precip:.0f}%" if isinstance(precip, (int, float)) else "?%"
        wind_s = f"{wind:.0f}mph" if isinstance(wind, (int, float)) else "?mph"
        wind_flag = " 💨" if isinstance(wind, (int, float)) and wind >= WIND_SUSTAINED_MPH else ""
        lines.append(f"  🌤️ {label}: {hi_s}/{lo_s} · rain {precip_s} · wind {wind_s}{wind_flag}")

    return "\n".join(lines)


# ── Overnight FYI flush ──────────────────────────────────────────────────

def _chdb():
    """Deferred import of core.cron_health_db — mirrors agents.cron_helpers'
    own _chdb() and this file's existing deferred core.* imports
    (get_recent_activity, send_telegram). Resolved fresh from sys.modules
    on every call so tests that reimport core.cron_health_db against a tmp
    DB (BAZA_CRON_HEALTH_DB) are picked up without also needing to
    reimport this module."""
    from core import cron_health_db
    return cron_health_db


def build_fyi_section() -> tuple[str, list[int]]:
    """Overnight/queued FYI flush: pulls cron_health_db's fyi_queue rows
    that are now due for release (send_report() queues 'fyi'-priority
    reports there during quiet hours instead of sending immediately) and
    renders up to FYI_CAP as bullets ("+N more" beyond that).

    Returns (section_text, pending_ids) and returns ("", []) when nothing's
    pending. Consumption is deliberately NOT done here — this used to mark
    every pending row consumed at read time, before the Telegram send, so a
    failed send silently lost the queued FYIs forever. Now the caller
    (main()) is responsible for calling cron_health_db.mark_fyis_consumed
    (pending_ids) itself, and only once the send has actually succeeded, so
    a failed send leaves these rows pending for the next cycle instead of
    dropping them.

    Exception-safe by design: any cron_health_db read failure (missing
    table, locked DB, whatever) is caught, logged, and degrades to ("", [])
    — a queue hiccup must never break the rest of the briefing.
    """
    try:
        now_iso = datetime.datetime.now().isoformat(timespec="seconds")
        rows = _chdb().pending_fyis(now_iso)
        if not rows:
            return "", []

        shown = rows[:FYI_CAP]
        lines = ["📥 Overnight FYIs"]
        for row in shown:
            msg = (row["message"] or "").strip()
            msg = msg.splitlines()[0][:160] if msg else "(empty)"
            lines.append(f"  • [{row['cron_name'] or '?'}] {msg}")
        remaining = len(rows) - len(shown)
        if remaining > 0:
            lines.append(f"  …+{remaining} more")

        pending_ids = [row["id"] for row in rows]
        return "\n".join(lines), pending_ids
    except Exception as e:
        log.warning(f"build_fyi_section failed (cron_health_db error): {e}")
        return "", []


# ── LLM briefing ─────────────────────────────────────────────────────────────

def build_dynamic_briefing(live_data: str, team_status: str, tasks: str,
                            activity: str, artifacts: str) -> str:
    now = datetime.datetime.now().strftime("%A, %B %d %Y — %I:%M %p")

    system = f"""You are Simon Bately — Co-CEO and Team Commander of the Baza Empire and AHBCO LLC.
You report directly to Serge (the boss). This is your scheduled 2-hour team command briefing.

STRICT FORMAT RULES — NO EXCEPTIONS:
- LANGUAGE: English only. Never Vietnamese, Chinese, Spanish, or any other language — even if the input has non-English proper nouns. This is a hard rule.
- ZERO markdown. No #, ##, **, __, *, [], ()
- Use ━━━━━━━━━━━━━━━━ as section dividers
- Use emoji for labels and bullets only
- Plain text — no bold, no headers with pound signs
- Max 35 lines. Keep it sharp and actionable.
- Serge is the boss. Simon = commander. You command the team TO PLEASE SERGE.

ANTI-HALLUCINATION RULES — VIOLATING THESE MEANS THE BRIEFING IS WRONG:
- "Recent Wins" MUST come from the ARTIFACTS_REAL_LAST_2H block below. If it
  says NONE, you write "No completed deliverables this cycle" — DO NOT
  invent wins, designs, or completions. Cite filenames when you do have wins.
- "Active Tasks" / "Command from Simon" MUST reference real entries from the
  TASK_STATE / RECENT_ACTIVITY blocks. You cannot say "Sam completed X" if
  no artifact for X exists in the artifact list.
- Theatrical phrasing ("I've dispatched Claw to...") is allowed ONLY if you
  also emit a real DISPATCH:<agent_id>:<directive> line at the bottom of the
  briefing for the runner to forward. Otherwise drop the theater.
- If a section has nothing real, write "Nothing to report" rather than filler.

YOU MUST COVER:
1. Empire pulse: who is online, who is offline right now (from team_status block)
2. Active tasks: only what TASK_STATE shows
3. What Simon is dispatching RIGHT NOW — must be real DISPATCH lines
4. Any blockers or issues — only if visible in the data
5. Recent wins: only from ARTIFACTS_REAL_LAST_2H — say NONE if NONE
6. Quick metrics: weather (from live data)
7. Your flag: one urgent action item for Serge

TONE: Sharp, confident commander — but factual. You don't invent wins to
sound like you're winning. If the team did nothing this cycle, that's the
briefing.

LIVE DATA:
{live_data}

{team_status}

{tasks}

{activity}

{artifacts}
"""
    prompt = f"Send Serge his 2-hour command briefing for {now}. Be sharp. Own the room."

    payload = json.dumps({
        "model": MODEL, "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt}
        ]
    }).encode()

    # Try each Ollama instance (AMD then NVIDIA) with generous timeout
    last_err = None
    for url in OLLAMA_URLS:
        try:
            log.info(f"Trying LLM at {url}...")
            req = urllib.request.Request(
                f"{url}/api/chat", data=payload,
                headers={"Content-Type":"application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as r:
                return json.loads(r.read())["message"]["content"].strip()
        except Exception as e:
            log.warning(f"LLM at {url} failed: {e}")
            last_err = e

    log.error(f"All LLM instances failed: {last_err}")
    return (
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 Simon Briefing — {now}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚠️ LLM unavailable: {last_err}\n\n"
        f"{team_status}\n\n"
        f"{tasks}"
    )

def looks_non_english(text: str) -> bool:
    """Returns True if the briefing looks like it drifted to a non-English language.

    Signal: >3% of non-emoji, non-whitespace characters fall outside ASCII/Latin-1
    common-punctuation range. Emojis and the divider char are whitelisted.
    """
    if not text:
        return False
    whitelist = set('━─·•→←↑↓✓✗✔✘–—…"\'')
    suspect = 0; total = 0
    for ch in text:
        if ch.isspace() or ch in whitelist:
            continue
        cp = ord(ch)
        # Skip emoji (surrogate pair high-cp) and common symbols
        if cp >= 0x2600:
            continue
        total += 1
        # ASCII printable is 32-126; Latin-1 extended is up to 255 (covers é, ñ, etc.)
        if cp > 0x024F:  # beyond Latin Extended-B — likely CJK/Vietnamese-diacritic/etc.
            suspect += 1
    if total < 80:
        return False
    return (suspect / total) > 0.03


def _strip_llm_tokens(text: str) -> str:
    """Drop leaked chat-template tokens without touching markdown.

    post_html renders markdown now, so this must NOT strip #, **, _, etc.
    It only removes qwen/chat-template control tokens that occasionally
    leak into model output: <think>...</think> reasoning blocks (whole
    block, including content — a leaked chain-of-thought shouldn't reach
    Serge), <tool_call>/</tool_call> tags, and <|...|> special tokens.
    """
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'</?tool_call>', '', text)
    text = re.sub(r'<\|[^|]*\|>', '', text)
    return text.strip()

def send_telegram(text: str) -> bool:
    """Send the final briefing to Serge, routed through
    cron_helpers.send_report (Task 8 retrofit: every cron's outbound send
    goes through send_report()/send_alert() instead of a bare Telegram
    call). priority="alert" mirrors this function's previous behavior —
    immediate, unconditional delivery regardless of quiet hours — since a
    2-hour command briefing is not a queueable FYI. token/chat_id are
    passed through explicitly so this always uses Simon's bot/Serge's chat
    even though cron_helpers' own module-level defaults differ (Simon's
    TELEGRAM_TOKEN here has a hardcoded fallback; cron_helpers' default is
    empty unless TELEGRAM_SIMON_BATELY is set in its own environment).

    _strip_llm_tokens is applied here, before handing off, so send_report
    (and ultimately post_html) always receives already-cleaned text and
    never sees leaked <think>/<tool_call>/<|...|> chat-template tokens.

    Returns the bool cron_helpers.send_report reports back (True iff the
    message was actually sent just now). main() gates FYI-queue
    consumption on this return value so a failed send doesn't silently
    lose queued FYIs.
    """
    from agents.cron_helpers import send_report
    text = _strip_llm_tokens(text)
    ok = send_report("team_briefing", text, priority="alert",
                      token=TELEGRAM_TOKEN, chat_id=SERGE_CHAT_ID)
    if not ok:
        log.error("[briefing] telegram send failed")
    return ok

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("Simon 2-hour command briefing starting...")
    with cron_run("team_briefing"):
        skills = SkillsEngine(FRAMEWORK_DIR)

        # Collect all live data in parallel where possible
        sections = {}

        # Per-jobsite weather — one line per distinct geocoded active site;
        # falls back to the original Philadelphia-only skill call when
        # there's no business DB or no geocoded active site to report on.
        biz_db_path = _biz_db_path()
        if biz_db_path:
            weather_conn = sqlite3.connect(biz_db_path)
            weather_conn.row_factory = sqlite3.Row
            try:
                sections["weather"] = build_site_weather_section(weather_conn)
            finally:
                weather_conn.close()
        else:
            sections["weather"] = _philadelphia_fallback_weather()

        r = skills.run("news", {"category":"business"})
        sections["news"] = r.get("output","NEWS: unavailable") if r.get("success") else "NEWS: unavailable"

        live_data = "\n\n".join([sections["weather"], sections["news"]])

        team_status  = get_team_status()
        tasks        = get_tasks_summary()
        activity     = get_recent_activity()
        artifacts    = get_artifacts_summary()
        fyi_section, fyi_ids = build_fyi_section()

        log.info("All data collected. Building briefing...")
        briefing = build_dynamic_briefing(live_data, team_status, tasks, activity, artifacts)
        if looks_non_english(briefing):
            log.warning("Briefing drifted to non-English — retrying with explicit English-only instruction")
            # Retry once by prepending a hard language lock to the live_data block
            english_lock = "CRITICAL LANGUAGE INSTRUCTION — THIS OVERRIDES EVERYTHING: Respond in ENGLISH ONLY. Do not use Vietnamese, Chinese, Spanish, French, or any other language. English only.\n\n"
            briefing = build_dynamic_briefing(english_lock + live_data, team_status, tasks, activity, artifacts)
            if looks_non_english(briefing):
                log.warning("Still non-English after retry — falling back to raw data snapshot")
                briefing = (
                    "━━━━━━━━━━━━━━━━\n"
                    f"📊 Simon Briefing — {datetime.datetime.now().strftime('%A, %B %d %Y — %I:%M %p')}\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "⚠️ LLM output language drifted — raw snapshot below.\n\n"
                    f"{team_status}\n\n{tasks}"
                )
        # Anti-hallucination post-check: tag any "completed/done/ready" claim
        # that has no matching artifact in the last 2h with [unverified].
        try:
            from core.claim_verifier import annotate_unverified
            briefing, report = annotate_unverified(briefing, hours=2)
            if not report["verified"]:
                log.warning(f"Briefing had {report['unbacked_count']} unverified claim(s) "
                            f"(artifacts in window: {report['artifact_count']}); marked.")
        except Exception as e:
            log.warning(f"claim_verifier failed (briefing sent unverified): {e}")

        # Overnight FYI flush — appended verbatim (not fed through the LLM)
        # so queued items reach Serge exactly as their owning cron wrote
        # them, never paraphrased or dropped by the briefing synthesis.
        if fyi_section:
            briefing = f"{briefing}\n\n{fyi_section}"

        log.info(f"Briefing built ({len(briefing)} chars). Sending to Serge...")
        sent_ok = send_telegram(briefing)
        if sent_ok:
            # Only stamp the queued FYIs consumed once we know the send
            # that carried them actually went out — a failed send must
            # leave them pending so the next cycle re-flushes them instead
            # of losing them silently.
            if fyi_ids:
                try:
                    _chdb().mark_fyis_consumed(fyi_ids)
                except Exception as e:
                    log.warning(f"mark_fyis_consumed failed after successful send "
                                f"({len(fyi_ids)} id(s) left pending, will retry next cycle): {e}")
            log.info("Done.")
        else:
            log.error(f"Telegram send failed — briefing NOT delivered; "
                       f"{len(fyi_ids)} queued FYI(s) left unconsumed for next cycle.")

if __name__ == "__main__":
    main()
