#!/usr/bin/env python3
"""Duke Harmon — Daily deadline enforcement. What's due today, this week, overdue."""
import os, sys, logging, datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *
from core.weather_sources import get_forecast

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DUKE-DEADLINES] %(message)s")

MODEL = "qwen2.5:14b"
AGENT_TOKEN = os.getenv("TELEGRAM_DUKE_HARMON", TELEGRAM_TOKEN)

# ── 7-day weather lookahead (item 6 of the cron-improvements plan) ─────────
# Appended to the morning deadline message. Consumes core.weather_sources.get_forecast
# (Task 2) against each active site's lat/lon. Pure/testable helpers below;
# build_weather_lookahead(conn) is the single entry point main() calls.

RAIN_PRECIP_PROB_PCT = 50   # "rain day" (forecast): precip_prob_max>=50 OR precip_in>=0.1 --
RAIN_PRECIP_IN = 0.1        # NWS forecasts leave precip_in at 0.0, so precip_prob is primary.
WIND_SUSTAINED_MPH = 20     # "high-wind day": wind_max>=20 or gust_max>=35
WIND_GUST_MPH = 35
HOT_HIGH_F = 90.0           # "≥90° day"
COLLISION_PRECIP_PROB_PCT = 50  # start/end date collision threshold

ACTIVE_SITE_LOOKAHEAD_DAYS = 7
MAX_LOOKAHEAD_SITES = 8     # cap serial get_forecast calls; mirrors weather_watch.py's fetch_cache dedup


def _is_forecast_rain_day(day: dict) -> bool:
    prob = day.get("precip_prob_max") or 0
    precip = day.get("precip_in") or 0
    return prob >= RAIN_PRECIP_PROB_PCT or precip >= RAIN_PRECIP_IN


def _is_forecast_wind_day(day: dict) -> bool:
    wind = day.get("wind_mph") or 0
    gust = day.get("gust_mph") or 0
    return wind >= WIND_SUSTAINED_MPH or gust >= WIND_GUST_MPH


def _is_forecast_hot_day(day: dict) -> bool:
    high = day.get("high_f")
    return high is not None and high >= HOT_HIGH_F


def _day_abbr(date_str) -> str:
    try:
        return datetime.date.fromisoformat(str(date_str)[:10]).strftime("%a")
    except (ValueError, TypeError):
        return str(date_str) if date_str else "?"


def _is_weekday(date_str) -> bool:
    try:
        return datetime.date.fromisoformat(str(date_str)[:10]).weekday() < 5
    except (ValueError, TypeError):
        return False


def _day_icon(day: dict) -> str:
    """Rain > wind > heat > clear, in that priority order."""
    if _is_forecast_rain_day(day):
        prob = day.get("precip_prob_max")
        return f"🌧{int(prob)}%" if prob else "🌧"
    if _is_forecast_wind_day(day):
        return "💨"
    if _is_forecast_hot_day(day):
        return "🔥"
    return "☀️"


def _week_line(daily: list) -> str:
    return " ".join(f"{_day_abbr(d.get('date'))}{_day_icon(d)}" for d in daily)


def _best_exterior_days(daily: list) -> list:
    """The 2 weekdays (Mon-Fri) in `daily` with lowest (precip_prob_max, then
    wind_mph), returned as weekday abbreviations in chronological order."""
    candidates = [d for d in daily if d.get("date") and _is_weekday(d["date"])]
    ranked = sorted(
        candidates,
        key=lambda d: ((d.get("precip_prob_max") or 0), (d.get("wind_mph") or 0)),
    )
    best = ranked[:2]
    best.sort(key=lambda d: d.get("date") or "")
    return [_day_abbr(d["date"]) for d in best]


def _collision_flags(daily: list, start_date, end_date) -> list:
    """Flag when `start_date`/`end_date` falls on a forecast day with
    precip_prob_max >= 50."""
    by_date = {d.get("date"): d for d in daily if d.get("date")}
    flags = []
    for label, date in (("Start", start_date), ("End", end_date)):
        if not date:
            continue
        day = by_date.get(str(date)[:10])
        if not day:
            continue
        prob = day.get("precip_prob_max") or 0
        if prob >= COLLISION_PRECIP_PROB_PCT:
            flags.append(f"⚠️ {label} date {str(date)[:10]} collides with {int(prob)}% rain chance")
    return flags


def _active_sites(conn, ref_date=None):
    """In-Progress sites, OR sites whose start_date falls within the next
    ACTIVE_SITE_LOOKAHEAD_DAYS days. Degrades to [] on any schema drift
    (missing/renamed table or columns) rather than crashing the cron."""
    ref = ref_date or datetime.date.today()
    horizon = ref + datetime.timedelta(days=ACTIVE_SITE_LOOKAHEAD_DAYS)
    try:
        rows = conn.execute(
            "SELECT id, title, address, latitude, longitude, start_date, end_date, status "
            "FROM ahb_projects"
        ).fetchall()
    except Exception as e:
        log.warning(f"weather lookahead: could not read ahb_projects: {e}")
        return []

    out = []
    for r in rows:
        status = r["status"] or ""
        start_date = r["start_date"] or ""
        active = status == "In Progress"
        if not active and start_date:
            try:
                sd = datetime.date.fromisoformat(start_date[:10])
                active = ref <= sd <= horizon
            except ValueError:
                active = False
        if active:
            out.append(r)
    return out


def build_weather_lookahead(conn) -> str:
    """7-day forecast lookahead appended to the morning deadline message.

    Per active site (In-Progress OR start_date within the next 7 days) with
    usable coordinates: a compact week line (`Mon☀️ Tue🌧80% ...`), the 2
    best weekday exterior-work days, and a flag if the site's start_date/
    end_date collides with a >=50% rain-chance day. Returns "" when there
    are no active sites, or none has coords/a forecast available.
    """
    sites = _active_sites(conn)
    if not sites:
        return ""

    # Cap to the first MAX_LOOKAHEAD_SITES sites (by title order) so message
    # length can't grow unbounded as the active-project count grows.
    sites = sorted(sites, key=lambda s: (s["title"] or s["address"] or s["id"] or ""))
    total_sites = len(sites)
    truncated_by = total_sites - MAX_LOOKAHEAD_SITES
    sites = sites[:MAX_LOOKAHEAD_SITES]

    # Dedup get_forecast calls by rounded-coord key -- sites sharing coordinates
    # (e.g. same address, or adjacent lots) shouldn't trigger redundant serial
    # HTTP fetches (each up to 2x10s timeout). Mirrors weather_watch.py's
    # fetch_cache pattern.
    fetch_cache = {}
    lines = []
    for site in sites:
        lat, lon = site["latitude"], site["longitude"]
        if lat is None or lon is None:
            continue
        cache_key = (round(lat, 2), round(lon, 2))
        if cache_key not in fetch_cache:
            fetch_cache[cache_key] = get_forecast(lat, lon)
        forecast = fetch_cache[cache_key]
        daily = (forecast or {}).get("daily") or []
        if not daily:
            continue

        label = site["title"] or site["address"] or site["id"]
        lines.append(f"*{label}*: {_week_line(daily)}")

        best = _best_exterior_days(daily)
        if best:
            lines.append(f"  Best exterior days: {'/'.join(best)}")

        for flag in _collision_flags(daily, site["start_date"], site["end_date"]):
            lines.append(f"  {flag}")

    if not lines:
        return ""

    result = "🗓️ *7-Day Weather Lookahead*\n" + "\n".join(lines)
    if truncated_by > 0:
        result += f"\n… and {truncated_by} more sites"
    return result

def collect_data():
    conn = get_db()
    td = today()
    week_end = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()

    overdue = conn.execute("SELECT title, assigned_to, due_date FROM tasks WHERE due_date < ? AND status NOT IN ('completed','done') AND due_date != '' ORDER BY due_date", (td,)).fetchall()
    due_today = conn.execute("SELECT title, assigned_to FROM tasks WHERE due_date = ? AND status NOT IN ('completed','done')", (td,)).fetchall()
    due_week = conn.execute("SELECT title, assigned_to, due_date FROM tasks WHERE due_date > ? AND due_date <= ? AND status NOT IN ('completed','done') ORDER BY due_date", (td, week_end)).fetchall()

    # Overdue invoices
    overdue_inv = conn.execute("SELECT project_name, total, client_name FROM ahb_invoices WHERE status='Overdue' LIMIT 5").fetchall()
    conn.close()

    data = f"DEADLINE REPORT — {td}\n\n"
    data += f"OVERDUE ({len(overdue)}):\n" + ("\n".join(f"  [{o[1] or '?'}] {o[0][:60]} — was due {o[2]}" for o in overdue) if overdue else "  None") + "\n\n"
    data += f"DUE TODAY ({len(due_today)}):\n" + ("\n".join(f"  [{d[1] or '?'}] {d[0][:60]}" for d in due_today) if due_today else "  None") + "\n\n"
    data += f"DUE THIS WEEK ({len(due_week)}):\n" + ("\n".join(f"  [{d[1] or '?'}] {d[0][:60]} — {d[2]}" for d in due_week) if due_week else "  None")
    if overdue_inv:
        data += "\n\nOVERDUE INVOICES:\n" + "\n".join(f"  {i[0][:40]} — ${i[1]:,.2f} ({i[2]})" for i in overdue_inv)
    return data

def main():
    with cron_run("deadline_enforcer"):
        log.info("Starting deadline enforcer...")
        data = collect_data()
        system = f"""You are Duke Harmon — Director of Project Management enforcing deadlines.
Daily deadline report for Serge. Plain text, no markdown. Max 20 lines.
Be aggressive about overdue items. Name names. Recommend actions.

{data}"""
        report = ollama_generate(MODEL, system, f"Deadline enforcement for {today()}")
        message = f"⏰ DEADLINE ENFORCER — {today()}\n\n{report}"

        try:
            conn = get_db()
            try:
                lookahead = build_weather_lookahead(conn)
            finally:
                conn.close()
        except Exception as e:
            log.warning(f"weather lookahead failed: {e}")
            lookahead = ""
        if lookahead:
            message += f"\n\n{lookahead}"

        send_report("deadline_enforcer", message, priority="alert", token=AGENT_TOKEN)

if __name__ == "__main__":
    main()
