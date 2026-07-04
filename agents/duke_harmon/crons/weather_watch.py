#!/usr/bin/env python3
"""
Duke Harmon — jobsite weather watch.

Every 2h during working hours (+ a 05:00 day-ahead run): pulls forecast +
active NWS alerts for every in-progress or imminent AHBCO jobsite,
evaluates heat/rain/wind/cold hazard thresholds (core/weather_rules.py)
against each site's trade profile (core/weather_profile.py), and tells
Serge what to do about it:

  - severity="alert" hazards (today/tomorrow, or any official NWS
    watch/warning) -> one deduped Telegram alert per hazard-key
    (core/cron_health_db.py's cron_alert_state, renotify 24h -- hazard
    keys are date-scoped so they naturally roll off day to day).
  - severity="fyi" hazards (days 2-6, or Minor-severity NWS advisories)
    -> collected and sent as one combined FYI report per run.
  - sites with no address/coords, even after core.geocode.ensure_project_coords
    -> a deduped "add an address" nag (renotify 72h).
  - a previously-alerted NWS watch/warning that's no longer active for a
    site -> one "all clear" line per site per day (see
    `_check_all_clear` docstring for the exact -- deliberately simple --
    approximation used).
  - today's actuals (high/low/precip/wind/gust/conditions) upserted into
    dashboard/baza_projects.db's weather_observations ledger, one row
    per (project_id, obs_date) -- the rain-day record referenced by
    later weather-delay disputes.

Standalone-executable (`venv/bin/python agents/duke_harmon/crons/weather_watch.py`).
`main(now=None)` is the testable entry point and has no import-time side
effects.
"""
import datetime
import logging
import os
import sys
from contextlib import closing

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *  # noqa: F401,F403 -- get_db, cron_run, send_alert,
# send_report, log, now, today, TELEGRAM_TOKEN (house style for every cron in this repo)

from core.weather_sources import get_forecast, get_active_alerts
from core.weather_rules import evaluate, default_profile
from core.geocode import ensure_project_coords
from core.weather_profile import get_weather_profile, ensure_weather_profile_column
from core import cron_health_db as chdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DUKE-WEATHER] %(message)s")

CRON_NAME = "weather_watch"
AGENT_TOKEN = os.getenv("TELEGRAM_DUKE_HARMON", TELEGRAM_TOKEN)

IN_PROGRESS_STATUS = "In Progress"
LOOKAHEAD_DAYS = 7          # brief step 1: In-Progress OR start_date within this many days
NOADDR_RENOTIFY_HOURS = 72  # controller decision
HAZARD_RENOTIFY_HOURS = 24  # controller decision (hazard keys are date-scoped anyway)
ALLCLEAR_RENOTIFY_HOURS = 24
ALL_CLEAR_STALE_HOURS = 6   # only clear an alert once we haven't re-seen it in >6h

WEATHER_OBSERVATIONS_DDL = """
CREATE TABLE IF NOT EXISTS weather_observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL,
    obs_date      TEXT NOT NULL,
    lat           REAL,
    lon           REAL,
    temp_high_f   REAL,
    temp_low_f    REAL,
    precip_in     REAL,
    wind_max_mph  REAL,
    gust_max_mph  REAL,
    conditions    TEXT,
    source        TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_weather_obs_project_date
    ON weather_observations(project_id, obs_date);
"""

HAZARD_EMOJI = {
    "heat": "🔥",
    "rain": "🌧️",
    "wind": "💨",
    "cold_concrete": "🥶",
    "cold_paint": "🥶",
}

HAZARD_TITLE = {
    "heat": "Heat",
    "rain": "Rain",
    "wind": "Wind",
    "cold_concrete": "Cold (concrete/masonry)",
    "cold_paint": "Cold (paint)",
}


# ── schema ────────────────────────────────────────────────────────────

def ensure_weather_observations_table(conn):
    """Create-if-missing DDL for the rain-day ledger. Idempotent, never raises."""
    try:
        conn.executescript(WEATHER_OBSERVATIONS_DDL)
        conn.commit()
    except Exception as e:
        log.warning(f"ensure_weather_observations_table failed: {e}")


# ── site selection ───────────────────────────────────────────────────

def _get_sites(conn, when):
    """In-Progress sites, OR sites whose start_date falls within the next
    LOOKAHEAD_DAYS (i.e. about to break ground -- get weather coverage
    lined up before day one, not after)."""
    today_str = when.date().isoformat()
    horizon_str = (when.date() + datetime.timedelta(days=LOOKAHEAD_DAYS)).isoformat()
    try:
        return conn.execute(
            "SELECT * FROM ahb_projects WHERE status = ? "
            "OR (start_date IS NOT NULL AND start_date != '' "
            "AND start_date >= ? AND start_date <= ?) "
            "ORDER BY id",
            (IN_PROGRESS_STATUS, today_str, horizon_str),
        ).fetchall()
    except Exception as e:
        log.error(f"_get_sites query failed: {e}")
        return []


def _site_label(site):
    address = ((site["address"] if "address" in site.keys() else None) or "").strip()
    title = ((site["title"] if "title" in site.keys() else None) or "").strip()
    if address and title:
        return f"{address} ({title})"
    return address or title or f"project {site['id']}"


# ── message formatting ───────────────────────────────────────────────

def _day_label(date_str):
    if not date_str:
        return ""
    try:
        return datetime.date.fromisoformat(date_str).strftime("%a %m/%d")
    except ValueError:
        return date_str


def _format_hazard_line(hz, site_label):
    # NOTE: no html.escape() here -- this text goes out via send_alert/
    # send_report -> post_html(already_html=False) -> md_to_html, which
    # HTML-escapes the whole message itself. Escaping here too double-
    # escapes interpolated text (e.g. "Smith & Sons" -> "Smith &amp;amp;
    # Sons" rendered literally). House rule: only html.escape() when
    # hand-building HTML and calling post_html(..., already_html=True).
    hazard = hz.get("hazard", "")
    if hazard.startswith("nws:"):
        emoji, title = "🚨", hazard.split(":", 1)[1]
    else:
        emoji = HAZARD_EMOJI.get(hazard, "⚠️")
        title = HAZARD_TITLE.get(hazard, hazard.replace("_", " ").title())
    detail = str(hz.get("detail", ""))
    label = site_label
    day = _day_label(hz.get("date"))
    line = f"{emoji} *{title}* — {label}: {detail}"
    return f"{line} ({day})" if day else line


# ── ledger (weather_observations) ────────────────────────────────────

def _upsert_observation(conn, project_id, lat, lon, forecast, when):
    """UPSERT one weather_observations row per (project_id, obs_date) using
    today's forecast daily entry as the day's actuals-so-far. NWS's daily
    precip_in is always 0.0 (see core/weather_sources.py) -- we store
    whatever the active source gives rather than guess, per the brief."""
    if not forecast:
        return
    daily = forecast.get("daily") or []
    today_str = when.date().isoformat()
    day = next((d for d in daily if d.get("date") == today_str), None)
    if day is None and daily:
        day = daily[0]
    if day is None:
        return
    try:
        conn.execute(
            """INSERT INTO weather_observations
                   (project_id, obs_date, lat, lon, temp_high_f, temp_low_f, precip_in,
                    wind_max_mph, gust_max_mph, conditions, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(project_id, obs_date) DO UPDATE SET
                   lat=excluded.lat, lon=excluded.lon,
                   temp_high_f=excluded.temp_high_f, temp_low_f=excluded.temp_low_f,
                   precip_in=excluded.precip_in, wind_max_mph=excluded.wind_max_mph,
                   gust_max_mph=excluded.gust_max_mph, conditions=excluded.conditions,
                   source=excluded.source""",
            (project_id, today_str, lat, lon, day.get("high_f"), day.get("low_f"),
             day.get("precip_in"), day.get("wind_mph"), day.get("gust_mph"),
             day.get("conditions"), forecast.get("source")),
        )
        conn.commit()
    except Exception as e:
        log.warning(f"weather_observations upsert failed for project {project_id!r}: {e}")


# ── NWS "all clear" (brief step 6) ───────────────────────────────────

def _check_all_clear(project_id, site_label, active_events, when, token):
    """Simplified all-clear approximation (documented per the controller's
    sign-off): every `alert`-severity NWS hazard we've ever sent for this
    site is recorded as its own cron_alert_state row, keyed
    f"weather:{project_id}:nws:{event}:{date}" (send_alert's own alert_key
    for that hazard). For each such row that's still unacknowledged and
    hasn't been re-confirmed active in the last ALL_CLEAR_STALE_HOURS
    (last_seen bumps every run the hazard is still present -- see
    core.cron_health_db.should_alert), if its event name is no longer
    among the site's currently active NWS alerts, treat it as cleared.
    One combined all-clear line per site per day, itself deduped via
    send_alert's own renotify window (24h) rather than a second bespoke
    table -- no separate "active alert id set" column is introduced.

    Once the all-clear actually sends, every cleared row is acked via
    chdb.alert_ack(row_id) -- otherwise these rows stay unacked+stale
    forever (an expired NWS alert's event never comes back to re-bump
    last_seen), so this same lookup would keep finding them on every future
    run. Since the all-clear alert_key is date-scoped
    (f"...allclear:{date_str}"), send_alert's own dedup never catches that
    repeat -- it would fire a fresh "All clear" every single day forever.
    Acking here is what makes a cleared alert clear exactly once.
    """
    prefix = f"weather:{project_id}:nws:"
    cutoff = (when - datetime.timedelta(hours=ALL_CLEAR_STALE_HOURS)).isoformat(timespec="seconds")
    try:
        # connect()'s own docstring warns `with connect() as conn:` only
        # commits/rolls back (sqlite3.Connection's context-manager
        # protocol) -- it does NOT close the connection. Use
        # contextlib.closing() so this connection is actually released.
        with closing(chdb.connect()) as conn:
            rows = conn.execute(
                "SELECT id, key FROM cron_alert_state WHERE key LIKE ? "
                "AND acked_at IS NULL AND last_seen < ?",
                (f"{prefix}%", cutoff),
            ).fetchall()
    except Exception as e:
        log.warning(f"all-clear lookup failed for project {project_id!r}: {e}")
        return

    cleared = set()
    cleared_row_ids = []
    for row in rows:
        rest = row["key"][len(prefix):]
        event, sep, _date = rest.rpartition(":")
        if sep and event and event not in active_events:
            cleared.add(event)
            cleared_row_ids.append(row["id"])

    if not cleared:
        return

    date_str = when.date().isoformat()
    # No html.escape() here -- see _format_hazard_line's note: this text
    # goes out via send_alert -> post_html(already_html=False) ->
    # md_to_html, which escapes the whole message itself already.
    lines = "\n".join(f"  - {e}" for e in sorted(cleared))
    message = (
        f"✅ *All clear* — {site_label}\n"
        f"Previously active NWS alert(s) have ended:\n{lines}"
    )
    sent = send_alert(
        CRON_NAME, message,
        alert_key=f"weather:{project_id}:allclear:{date_str}",
        renotify_hours=ALLCLEAR_RENOTIFY_HOURS,
        token=token,
    )
    if sent:
        for row_id in cleared_row_ids:
            try:
                chdb.alert_ack(row_id)
            except Exception as e:
                log.warning(f"all-clear ack failed for cron_alert_state row {row_id}: {e}")


# ── main ──────────────────────────────────────────────────────────────

def main(now=None):
    when = now or datetime.datetime.now()
    with cron_run(CRON_NAME):
        _run(when)


def _run(when):
    conn = get_db()
    try:
        ensure_weather_profile_column(conn)
        ensure_weather_observations_table(conn)

        sites = _get_sites(conn, when)
        if not sites:
            log.info("weather_watch: no in-progress/imminent sites, nothing to check")
            return

        fetch_cache = {}
        fyi_lines = []

        for site in sites:
            pid = site["id"]
            site_label = _site_label(site)

            coords = None
            try:
                coords = ensure_project_coords(conn, pid)
            except Exception as e:
                log.warning(f"ensure_project_coords failed for {pid!r}: {e}")

            if coords is None:
                # No html.escape() here -- see _format_hazard_line's note.
                send_alert(
                    CRON_NAME,
                    f"📍 *No address on file* — {site_label}: "
                    f"weather watch can't cover this site until it has an address. "
                    f"Add one in the dashboard.",
                    alert_key=f"weather:noaddr:{pid}",
                    renotify_hours=NOADDR_RENOTIFY_HOURS,
                    token=AGENT_TOKEN,
                )
                continue

            lat, lon = coords
            cache_key = (round(lat, 2), round(lon, 2))
            if cache_key not in fetch_cache:
                forecast = get_forecast(lat, lon)
                alerts = get_active_alerts(lat, lon)
                fetch_cache[cache_key] = (forecast, alerts)
            forecast, alerts = fetch_cache[cache_key]

            active_events = {a.get("event") for a in (alerts or []) if a.get("event")}

            if forecast is not None:
                _upsert_observation(conn, pid, lat, lon, forecast, when)
            else:
                log.warning(f"no forecast available for {pid!r} ({site_label}), skipping hazard eval")

            if forecast is not None:
                try:
                    profile = get_weather_profile(conn, site)
                except Exception as e:
                    log.warning(f"get_weather_profile failed for {pid!r}: {e}")
                    profile = default_profile()

                # Same per-site isolation as ensure_project_coords/
                # get_weather_profile above -- one site's unexpected
                # evaluate()/formatting/send failure must not abort the
                # remaining sites or the combined FYI batch send.
                try:
                    hazards = evaluate(forecast, alerts, profile)
                    for hz in hazards:
                        line = _format_hazard_line(hz, site_label)
                        if hz.get("severity") == "alert":
                            send_alert(
                                CRON_NAME, line,
                                alert_key=f"weather:{pid}:{hz['key_suffix']}",
                                renotify_hours=HAZARD_RENOTIFY_HOURS,
                                token=AGENT_TOKEN,
                            )
                        else:
                            fyi_lines.append(line)
                except Exception as e:
                    log.warning(f"hazard evaluation failed for {pid!r} ({site_label}): {e}")

            _check_all_clear(pid, site_label, active_events, when, AGENT_TOKEN)

        if fyi_lines:
            message = (
                "🌦️ *Weather FYI* — " + when.strftime("%Y-%m-%d")
                + "\n" + "\n".join(fyi_lines)
            )
            send_report(
                CRON_NAME, message, priority="fyi",
                delta_key="weather_watch_fyi", token=AGENT_TOKEN,
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
