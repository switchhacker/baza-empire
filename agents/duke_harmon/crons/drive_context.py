#!/usr/bin/env python3
"""
Duke Harmon — weekday-morning jobsite drive-time briefing.

Weekday mornings (06:15 local, cron "15 6 * * 1-5"): OSRM drive ETA from
Serge's home (BAZA_HOME_ADDRESS) to every active AHBCO jobsite (ahb_projects
status='In Progress' with latitude/longitude set), sent as one combined FYI
report that lands right before Duke's 07:00 deadline_enforcer lookahead.

  - BAZA_HOME_ADDRESS unset -> one deduped setup nag via send_alert
    (key="drivectx:setup", renotify 168h/weekly), then exit cleanly. No
    active-sites lookup is attempted in that case.
  - Home address is geocoded once (core.geocode.geocode) and cached at
    HOME_COORDS_FILE (default configs/.home_coords.json, mode 0600),
    keyed by the address string itself -- an address CHANGE misses the
    cache and triggers a fresh geocode + cache rewrite.
  - Per site: OSRM `driving` route (note the URL's lon,lat order!) via the
    single `_fetch_osrm` seam (10s timeout, required User-Agent). When
    OSRM is unreachable or its response isn't code=="Ok" with a route,
    falls back to haversine-distance x 2.1 min/mi, and the line is marked
    "(rough estimate)" instead of "(no-traffic baseline)".
  - No active geocoded sites -> silent, no message sent at all.
  - One combined `send_report(priority="fyi")` -- 06:15 is outside the
    default quiet-hours window (21:00-06:30) so it sends immediately.

Standalone-executable (`venv/bin/python agents/duke_harmon/crons/drive_context.py`).
`main(now=None)` is the testable entry point and has no import-time side
effects.
"""
import datetime
import json
import logging
import math
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *  # noqa: F401,F403 -- get_db, cron_run, send_alert,
# send_report, log, now, today, TELEGRAM_TOKEN, FRAMEWORK_DIR (house style for every cron)

from core.geocode import geocode

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DUKE-DRIVE] %(message)s")

CRON_NAME = "drive_context"
AGENT_TOKEN = os.getenv("TELEGRAM_DUKE_HARMON", TELEGRAM_TOKEN)

SETUP_ALERT_KEY = "drivectx:setup"
SETUP_RENOTIFY_HOURS = 168  # weekly nag, per the controller decision

# Overridable for tests (env read at import time, mirrors core/cron_health_db.py's
# DB_PATH pattern). Tests may also monkeypatch this module attribute directly
# after import -- functions below always resolve it as a module global at call
# time, so either approach works.
HOME_COORDS_FILE = os.environ.get("BAZA_HOME_COORDS_FILE") or os.path.join(
    FRAMEWORK_DIR, "configs", ".home_coords.json"
)

OSRM_URL = (
    "https://router.project-osrm.org/route/v1/driving/"
    "{lon1},{lat1};{lon2},{lat2}?overview=false"
)
USER_AGENT = "baza-empire/1.0 (contactahbco@gmail.com)"
OSRM_TIMEOUT = 10

METERS_PER_MILE = 1609.344
HAVERSINE_MIN_PER_MILE = 2.1  # brief's fallback rate when OSRM is unavailable
EARTH_RADIUS_MILES = 3958.8

ACTIVE_STATUS = "In Progress"


# ── home-coords cache (configs/.home_coords.json, keyed by the address) ────

def _read_home_cache():
    """Returns the cached {"address","lat","lon",...} dict, or None on any
    failure (missing file, bad JSON, etc)."""
    try:
        with open(HOME_COORDS_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _write_home_cache(address, lat, lon):
    """Best-effort cache write, mode 0600. Write-then-rename so a crash
    mid-write can't leave a half-written cache file. Never raises -- a
    write failure just means next run re-geocodes, which is safe."""
    try:
        os.makedirs(os.path.dirname(HOME_COORDS_FILE), exist_ok=True)
        payload = {
            "address": address,
            "lat": lat,
            "lon": lon,
            "geocoded_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        tmp_path = f"{HOME_COORDS_FILE}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(payload, f)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, HOME_COORDS_FILE)
    except Exception as e:
        log.warning(f"drive_context: home coords cache write failed: {e}")


def _get_home_coords(address):
    """Read-through cache for BAZA_HOME_ADDRESS's (lat, lon), keyed by the
    address string itself so a changed address naturally misses the cache
    and re-geocodes. Returns None if there's no usable cache and geocoding
    fails."""
    cached = _read_home_cache()
    if (
        cached
        and cached.get("address") == address
        and cached.get("lat") is not None
        and cached.get("lon") is not None
    ):
        return (cached["lat"], cached["lon"])

    result = geocode(address)
    if result is None:
        return None
    lat, lon = result
    _write_home_cache(address, lat, lon)
    return (lat, lon)


# ── site selection ───────────────────────────────────────────────────────

def _get_active_sites(conn):
    """In-Progress sites with lat/lon already set. Ordered by a stable
    human label so the message is deterministic run to run."""
    try:
        rows = conn.execute(
            "SELECT * FROM ahb_projects WHERE status = ? "
            "AND latitude IS NOT NULL AND longitude IS NOT NULL",
            (ACTIVE_STATUS,),
        ).fetchall()
    except Exception as e:
        log.error(f"drive_context: _get_active_sites query failed: {e}")
        return []
    return sorted(rows, key=_site_label)


def _site_label(site):
    address = ((site["address"] if "address" in site.keys() else None) or "").strip()
    title = ((site["title"] if "title" in site.keys() else None) or "").strip()
    if address and title:
        return f"{address} ({title})"
    return address or title or f"project {site['id']}"


# ── OSRM + haversine fallback ────────────────────────────────────────────

def _fetch_osrm(lat1, lon1, lat2, lon2, timeout=OSRM_TIMEOUT):
    """Single HTTP seam -- GET the OSRM driving route. URL is lon,lat order
    (OSRM convention), NOT lat,lon. Returns the parsed JSON dict, or None
    on any failure (network error, timeout, bad JSON)."""
    url = OSRM_URL.format(lon1=lon1, lat1=lat1, lon2=lon2, lat2=lat2)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _haversine_miles(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def _drive_eta(home_lat, home_lon, site_lat, site_lon):
    """Returns (minutes, miles, rough). OSRM's driving route (duration/
    distance from routes[0]) when the response parses and code=="Ok";
    otherwise a haversine-miles x 2.1 min/mi "rough estimate" fallback."""
    data = _fetch_osrm(home_lat, home_lon, site_lat, site_lon)
    if data and data.get("code") == "Ok":
        routes = data.get("routes") or []
        if routes:
            route = routes[0]
            duration_s = route.get("duration")
            distance_m = route.get("distance")
            if duration_s is not None and distance_m is not None:
                minutes = round(duration_s / 60)
                miles = distance_m / METERS_PER_MILE
                return minutes, miles, False

    miles = _haversine_miles(home_lat, home_lon, site_lat, site_lon)
    minutes = round(miles * HAVERSINE_MIN_PER_MILE)
    return minutes, miles, True


# ── message formatting ───────────────────────────────────────────────────

def _format_site_line(site, minutes, miles, rough):
    # No html.escape() here -- this goes out via send_report's markdown path
    # (post_html already_html=False), whose md_to_html escapes everything
    # itself; pre-escaping double-escapes "&" (see weather_watch.py's note).
    label = _site_label(site)
    descriptor = "rough estimate" if rough else "no-traffic baseline"
    return f"🚗 {label}: ~{minutes} min ({miles:.0f} mi, {descriptor})"


def _build_message(when, lines):
    header = f"🚗 *Drives this morning* — {when.strftime('%Y-%m-%d')}"
    return header + "\n" + "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────

def main(now=None):
    when = now or datetime.datetime.now()
    with cron_run(CRON_NAME):
        _run(when)


def _run(when):
    home_address = (os.getenv("BAZA_HOME_ADDRESS") or "").strip()
    if not home_address:
        send_alert(
            CRON_NAME,
            "🚗 *Drive context not configured* — set BAZA_HOME_ADDRESS in "
            "configs/secrets.env to enable weekday morning drive-time "
            "briefings to your jobsites.",
            alert_key=SETUP_ALERT_KEY,
            renotify_hours=SETUP_RENOTIFY_HOURS,
            token=AGENT_TOKEN,
        )
        return

    home_coords = _get_home_coords(home_address)
    if home_coords is None:
        log.warning(
            f"drive_context: could not geocode BAZA_HOME_ADDRESS={home_address!r}, skipping run"
        )
        return
    home_lat, home_lon = home_coords

    conn = get_db()
    try:
        sites = _get_active_sites(conn)
    finally:
        conn.close()

    if not sites:
        log.info("drive_context: no active geocoded sites, nothing to report")
        return

    lines = []
    for site in sites:
        try:
            minutes, miles, rough = _drive_eta(home_lat, home_lon, site["latitude"], site["longitude"])
            lines.append(_format_site_line(site, minutes, miles, rough))
        except Exception as e:
            # One bad site must not cost the whole morning message
            # (mirrors weather_watch.py's per-site isolation).
            log.warning(f"drive_context: site {site['id'] if 'id' in site.keys() else '?'} failed: {e}")

    message = _build_message(when, lines)
    send_report(CRON_NAME, message, priority="fyi", token=AGENT_TOKEN)


if __name__ == "__main__":
    main()
