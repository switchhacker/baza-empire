"""Nominatim geocoding for AHBCO project addresses.

Lean, read-through-cache module used by:
  - the dashboard project create/update routes (best-effort hook, never
    blocks a save on a geocoding failure)
  - scripts/backfill_geocode.py (one-shot backfill for existing rows)
  - later cron tasks that need a jobsite's (lat, lon) for weather alerts
    (`ensure_project_coords` is the stable entry point for that)

Distinct from core/geocoder.py (the older bulk "Geocode All" dashboard
widget with multi-variant address-fallback matching) — this module is
intentionally minimal and matches the exact signatures other tasks in the
cron-improvements plan depend on.

Per global-constraints.md: External HTTP only for data, 10s timeouts,
User-Agent: baza-empire/1.0 (contactahbco@gmail.com).
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

log = logging.getLogger("geocode")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "baza-empire/1.0 (contactahbco@gmail.com)"
TIMEOUT = 10


def geocode(address: str) -> tuple[float, float] | None:
    """Look up (lat, lon) for a free-text address via Nominatim.

    Returns None on any failure (blank address, network error, bad
    response, no results) — callers never need to handle exceptions.
    """
    if not address or not address.strip():
        return None
    try:
        params = urllib.parse.urlencode({
            "q": address,
            "format": "json",
            "limit": 1,
        })
        url = f"{NOMINATIM_URL}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
        if not data:
            return None
        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        return (lat, lon)
    except Exception as e:
        log.warning(f"geocode failed for {address!r}: {e}")
        return None


def ensure_project_coords(conn, project_id: str) -> tuple[float, float] | None:
    """Ensure ahb_projects.latitude/longitude are populated for project_id.

    Read-through cache: if the row already has lat/lon, returns them
    without making a network call. Otherwise geocodes
    COALESCE(address, location), persists the result (latitude, longitude,
    geocoded_at=datetime('now')) via UPDATE + commit, and returns it.

    Returns None if the project doesn't exist, has no usable address, or
    geocoding fails. Never raises — callers (dashboard save hooks) rely on
    that, though wrapping in try/except at the call site is still expected.
    """
    try:
        row = conn.execute(
            "SELECT latitude, longitude, address, location "
            "FROM ahb_projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    except Exception as e:
        log.warning(f"ensure_project_coords lookup failed for {project_id}: {e}")
        return None

    if row is None:
        return None

    lat, lon = row["latitude"], row["longitude"]
    if lat is not None and lon is not None:
        return (lat, lon)

    address = row["address"] or row["location"]
    if not address or not str(address).strip():
        return None

    result = geocode(address)
    if result is None:
        return None

    lat, lon = result
    try:
        conn.execute(
            "UPDATE ahb_projects SET latitude = ?, longitude = ?, "
            "geocoded_at = datetime('now') WHERE id = ?",
            (lat, lon, project_id),
        )
        conn.commit()
    except Exception as e:
        log.warning(f"ensure_project_coords update failed for {project_id}: {e}")
        return None

    return (lat, lon)
