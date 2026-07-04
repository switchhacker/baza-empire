#!/usr/bin/env python3
"""
Shared helpers for agent cron scripts.
Every cron script imports this for common functions:
  - Ollama inference
  - Telegram notification
  - Artifact saving
  - Event publishing
  - DB access
"""
import os, sys, json, subprocess, datetime, urllib.request, sqlite3, logging
from contextlib import contextmanager

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(FRAMEWORK_DIR, "configs", "secrets.env"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_SIMON_BATELY", "")
# Legacy (callback-incapable) bot tokens -- see send_alert()'s button
# gating below. Claw and Phil are the only live bots still started via the
# top-level `python agent.py --agent claw_batto|phil_hass` entrypoint
# (MessageHandler only, no CallbackQueryHandler registered), so a Telegram
# inline-keyboard tap on an alert sent through either of their bots would
# never get answered. Every other live bot -- Simon included -- runs
# core/base_agent.py's BaseAgent, which registers the CallbackQueryHandler
# and IS callback-capable.
TELEGRAM_CLAW_BATTO = os.getenv("TELEGRAM_CLAW_BATTO", "")
TELEGRAM_PHIL_HASS = os.getenv("TELEGRAM_PHIL_HASS", "")
SERGE_CHAT_ID = os.getenv("SERGE_CHAT_ID", "8551331144")
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DB_PATH = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
ARTIFACTS_DIR = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")

log = logging.getLogger("cron")


def run_cmd(cmd: str, timeout: int = 10) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, timeout=timeout).decode().strip()
    except Exception:
        return ""


def ollama_generate(model: str, system_prompt: str, user_prompt: str, max_tokens: int = 800) -> str:
    """Run a non-streaming Ollama inference."""
    # Estimate context needed: ~1.3 tokens per word, plus output budget
    total_chars = len(system_prompt) + len(user_prompt)
    needed_ctx = max(8192, int(total_chars / 3) + max_tokens + 512)
    payload = json.dumps({
        "model": model, "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "options": {
            "num_predict": max_tokens,
            "num_ctx": needed_ctx,
            "temperature": 0.7
        }
    }).encode()
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
            content = data.get("message", {}).get("content", "")
            if not content:
                log.warning(f"Ollama returned empty for model={model}, ctx={needed_ctx}, input_chars={total_chars}")
            return content
    except Exception as e:
        log.error(f"Ollama error: {e}")
        return f"(LLM unavailable: {e})"


def send_telegram(message: str, token: str = None, chat_id: str = None) -> bool:
    """Send a Telegram message to Serge (markdown → rich HTML, auto-chunked).

    Returns post_html's own bool (True iff the message was actually
    delivered) instead of swallowing it -- False when the token/chat_id
    aren't configured, or when post_html itself raises. Callers that route
    through this (e.g. send_report()) rely on this real outcome to gate
    downstream side effects like marking queued FYIs consumed only after a
    successful send.
    """
    tok = token or TELEGRAM_TOKEN
    cid = chat_id or SERGE_CHAT_ID
    if not tok or not cid:
        log.warning("No Telegram token/chat_id configured")
        return False
    try:
        from core.telegram_fmt import post_html
        return bool(post_html(tok, cid, message))
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def save_artifact(project_id: str, filename: str, content: str) -> str:
    """Save report as artifact file."""
    art_dir = os.path.join(ARTIFACTS_DIR, project_id)
    os.makedirs(art_dir, exist_ok=True)
    path = os.path.join(art_dir, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def publish_event(agent_id: str, event_type: str, data: dict):
    """Publish event to Redis event bus."""
    try:
        from core.event_bus import publish_sync
        publish_sync(agent_id, event_type, data)
    except Exception as e:
        log.warning(f"Event publish failed: {e}")


def get_db():
    """Get SQLite connection to baza_projects.db."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_pg():
    """Get PostgreSQL connection to baza_agents."""
    import psycopg2
    return psycopg2.connect(host="localhost", port=5432, dbname="baza_agents",
                            user="switchhacker",
                            password=os.environ.get("DB_PASSWORD", "baza2026"))


def log_activity(agent_id: str, summary: str, task_type: str = "cron_task",
                 requested_by: str = "cron", status: str = "completed", **kwargs):
    """Log an entry to the activity feed (task_journal in PostgreSQL)."""
    try:
        from core.context_db import journal_log
        journal_log(agent_id=agent_id, task_type=task_type,
                    task_description=summary, action_summary=summary,
                    requested_by=requested_by, status=status, **kwargs)
    except Exception as e:
        log.warning(f"log_activity failed: {e}")


def today():
    return datetime.date.today().isoformat()


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────────────────────────────────────────────────────────
# Routing layer (Task 4 of the cron-improvements plan): heartbeats, quiet
# hours, delta-suppressed reports, deduped alerts. Built on core/cron_health_db.py
# (Task 1). All cron_health_db access goes through _chdb() so registry
# failures (DB locked, disk full, whatever) never break a cron that's
# otherwise doing real work -- they're logged and swallowed at each call
# site, matching this file's existing send_telegram()/publish_event() style.
# ─────────────────────────────────────────────────────────────────────────

def _chdb():
    """Deferred import of core.cron_health_db (house style in this file: core.*
    imports are deferred per-call, see send_telegram/publish_event/log_activity
    above). Re-resolved from sys.modules on every call so tests that reimport
    core.cron_health_db against a fresh tmp DB are picked up without also
    needing to reimport this module."""
    from core import cron_health_db
    return cron_health_db


@contextmanager
def cron_run(name: str):
    """Heartbeat context manager for a cron script's body.

    Records a cron_runs row (record_run_start) before the body runs and
    closes it out (record_run_end) after -- status='ok' on a clean exit,
    status='error' with the exception message (truncated to 500 chars) if
    the body raises. The body's exception is always re-raised afterward;
    this context manager never masks a cron's exit code/failure. A
    SystemExit with code None/0 is treated as a normal ('ok') exit rather
    than an error, since that's just how a cron script chooses to stop.

    cron_health_db itself failing (record_run_start/record_run_end raising)
    is logged as a warning and swallowed -- a registry hiccup must never
    prevent the wrapped cron body from running or from propagating its own
    exception.

    Usage:
        with cron_run("infra_health"):
            ... cron body ...
    """
    run_id = None
    try:
        run_id = _chdb().record_run_start(name)
    except Exception as e:
        log.warning(f"cron_health_db.record_run_start failed for {name!r}: {e}")
        run_id = None

    try:
        yield
    except BaseException as exc:
        if isinstance(exc, SystemExit):
            code = exc.code
            is_ok = code is None or code == 0
            status = "ok" if is_ok else "error"
            err = None if is_ok else f"SystemExit({code!r})"
        else:
            status = "error"
            err = str(exc)[:500]
        if run_id is not None:
            try:
                _chdb().record_run_end(run_id, status, error=err)
            except Exception as e2:
                log.warning(f"cron_health_db.record_run_end failed for {name!r}: {e2}")
        raise
    else:
        if run_id is not None:
            try:
                _chdb().record_run_end(run_id, "ok")
            except Exception as e:
                log.warning(f"cron_health_db.record_run_end failed for {name!r}: {e}")


def in_quiet_hours(now: datetime.datetime | None = None) -> bool:
    """True if `now` (default: current local time) falls inside the quiet-hours
    window from env BAZA_QUIET_HOURS ("HH:MM-HH:MM", default "21:00-06:30").
    Handles windows that wrap past midnight (start > end, e.g. the default).
    Start is inclusive, end is exclusive. Malformed env values degrade to
    "never quiet" (logged) rather than raising.
    """
    now = now or datetime.datetime.now()
    spec = os.getenv("BAZA_QUIET_HOURS", "21:00-06:30")
    try:
        start_s, end_s = spec.split("-")
        start_h, start_m = (int(x) for x in start_s.split(":"))
        end_h, end_m = (int(x) for x in end_s.split(":"))
        start_t = datetime.time(start_h, start_m)
        end_t = datetime.time(end_h, end_m)
    except Exception:
        log.warning(f"Bad BAZA_QUIET_HOURS={spec!r}, treating as never-quiet")
        return False

    t = now.time()
    if start_t <= end_t:
        return start_t <= t < end_t
    return t >= start_t or t < end_t


def _next_quiet_hours_end(now: datetime.datetime | None = None) -> datetime.datetime:
    """The next datetime the quiet-hours window (BAZA_QUIET_HOURS) ends --
    today's end time if it hasn't passed yet, else tomorrow's. Used as the
    release_after for FYIs queued while in_quiet_hours() is True."""
    now = now or datetime.datetime.now()
    spec = os.getenv("BAZA_QUIET_HOURS", "21:00-06:30")
    try:
        _, end_s = spec.split("-")
        end_h, end_m = (int(x) for x in end_s.split(":"))
    except Exception:
        end_h, end_m = 6, 30
    end_today = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if now < end_today:
        return end_today
    return end_today + datetime.timedelta(days=1)


def send_report(cron_name: str, message: str, priority: str = "fyi",
                delta_key: str | None = None, token=None, chat_id=None) -> bool:
    """Route a routine cron report.

    - delta_key set and the message body is unchanged since the last send
      (per cron_health_db.delta_changed) -> suppress, return False.
    - priority == "alert" -> send immediately via send_telegram, return
      send_telegram's real outcome (True iff actually delivered).
    - priority == "fyi" (default) during quiet hours -> queue via
      cron_health_db.enqueue_fyi (release_after = next quiet-hours end),
      return False.
    - priority == "fyi" outside quiet hours -> send immediately, return
      send_telegram's real outcome (True iff actually delivered).

    Never raises (mirrors send_telegram's swallow-and-log style). Returns
    whether the message was actually delivered right now
    (queueing/suppression -> False; a send attempt that fails -> False, not
    a hollow True) -- consumers like briefing_cron's FYI-consumption gate
    depend on this being a real outcome, not an unconditional True.
    """
    try:
        if delta_key:
            try:
                changed = _chdb().delta_changed(delta_key, message)
            except Exception as e:
                log.warning(f"cron_health_db.delta_changed failed for {delta_key!r}: {e}")
                changed = True  # fail open: a DB hiccup shouldn't silently eat a report
            if not changed:
                log.info(f"send_report: {cron_name!r} unchanged (delta_key={delta_key!r}), suppressing")
                return False

        if priority == "alert":
            return send_telegram(message, token=token, chat_id=chat_id)

        # priority == "fyi" (or any other value defaults to fyi routing)
        if in_quiet_hours():
            release_after = _next_quiet_hours_end().isoformat(timespec="seconds")
            try:
                _chdb().enqueue_fyi(cron_name, message, release_after)
            except Exception as e:
                log.warning(f"cron_health_db.enqueue_fyi failed for {cron_name!r}: {e}")
            return False

        return send_telegram(message, token=token, chat_id=chat_id)
    except Exception as e:
        log.error(f"send_report failed for {cron_name!r}: {e}")
        return False


def send_alert(cron_name: str, message: str, alert_key: str,
              renotify_hours: float | None = None, buttons: bool = True,
              token=None, chat_id=None) -> bool:
    """Send a deduped alert, optionally with inline Ack / Snooze / Task buttons.

    Dedup/renotify/ack/snooze state lives in cron_health_db's
    cron_alert_state, keyed by alert_key (via should_alert). The message's
    first line is stashed in that row's meta as {"title": ...} -- used both
    for dedup and as the default title if the Task button later creates a
    task from this alert. Sends via core.telegram_fmt.post_html (not the
    send_telegram wrapper) so the actual-delivery boolean can be returned to
    the caller.

    The resolved token is simply the explicit `token` arg if one was passed,
    else TELEGRAM_TOKEN (Simon's default) -- send_alert never reroutes an
    alert to a different bot based on `buttons`.

    Buttons are gated on that resolved token instead. When `buttons` is True
    (the default) and should_alert returned a usable row_id, the alert ships
    with an inline keyboard -- "✓ Ack" / "😴 24h" / "➕ Task" -- each
    callback_data encoding "cron|<action>|<row_id>" for
    core.base_agent.BaseAgent._on_cron_callback to handle -- UNLESS the
    resolved token is one of the legacy (callback-incapable) bot tokens:
    TELEGRAM_CLAW_BATTO / TELEGRAM_PHIL_HASS. Claw and Phil are the only
    live bots still started via the top-level
    `python agent.py --agent claw_batto|phil_hass` entrypoint (MessageHandler
    only, no CallbackQueryHandler registered), so a Telegram inline-keyboard
    tap on an alert sent through either of their bots would never get
    answered. Every other live bot -- Simon included -- runs
    core/base_agent.py's BaseAgent, which registers the CallbackQueryHandler
    and IS callback-capable, so it's safe to attach buttons there. A legacy
    token still gets the alert -- same chat, just without the keyboard.
    Only non-empty legacy-token env values count (an unset/blank
    TELEGRAM_CLAW_BATTO or TELEGRAM_PHIL_HASS must never accidentally match
    an equally-blank resolved token).

    Never raises. Returns True iff a message was actually sent just now.
    """
    try:
        title = message.splitlines()[0] if message else ""
        row_id = None
        try:
            send_now, row_id = _chdb().should_alert(alert_key, renotify_hours, {"title": title})
        except Exception as e:
            log.warning(f"cron_health_db.should_alert failed for {alert_key!r}: {e}")
            send_now = True  # fail open: prefer a possible dup over a dropped alert

        if not send_now:
            log.info(f"send_alert: {cron_name!r}/{alert_key!r} deduped, not sending")
            return False

        tok = token or TELEGRAM_TOKEN
        cid = chat_id or SERGE_CHAT_ID

        legacy_tokens = {t for t in (TELEGRAM_CLAW_BATTO, TELEGRAM_PHIL_HASS) if t}
        reply_markup = None
        if buttons and row_id is not None and tok not in legacy_tokens:
            reply_markup = {
                "inline_keyboard": [[
                    {"text": "✓ Ack", "callback_data": f"cron|ack|{row_id}"},
                    {"text": "😴 24h", "callback_data": f"cron|snooze|{row_id}"},
                    {"text": "➕ Task", "callback_data": f"cron|task|{row_id}"},
                ]]
            }

        try:
            from core.telegram_fmt import post_html
            return bool(post_html(tok, cid, message, reply_markup=reply_markup))
        except Exception as e:
            log.error(f"send_alert: telegram send failed for {cron_name!r}: {e}")
            return False
    except Exception as e:
        log.error(f"send_alert failed for {cron_name!r}: {e}")
        return False
