#!/usr/bin/env python3
"""
Geocode AHB project addresses → lat/lng using OpenStreetMap Nominatim.

Free, no API key. Rate-limited to 1 req/sec per Nominatim's usage policy.
Caches results in the ahb_projects table itself (latitude, longitude, geocoded_at).
"""
from __future__ import annotations
import os, sys, time, json, sqlite3, urllib.parse, urllib.request, logging

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_DB  = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT    = "BazaEmpire/1.0 (admin@allhomebuilding.co)"
RATE_LIMIT    = 1.1  # seconds between requests

log = logging.getLogger("geocoder")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [geocoder] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


import re as _re

# US state abbreviations / common AHB territory tells
_STATE_PATTERN = _re.compile(r"\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b", _re.I)
_UNIT_PATTERN  = _re.compile(r"\b(unit|apt|apartment|suite|ste|#)\s*[\w\d-]+\b", _re.I)
_PHILA_HINTS   = _re.compile(r"\b(phila|philadelphia|philly)\b", _re.I)


def _normalize_variants(address: str) -> list[str]:
    """Generate progressive fallback variants of an address for geocoding."""
    addr = address.strip()
    variants = []
    # 1. As-is
    variants.append(addr)
    # 2. Newlines → commas, collapse whitespace
    cleaned = _re.sub(r"\s*\n+\s*", ", ", addr)
    cleaned = _re.sub(r"\s+", " ", cleaned).strip(" ,")
    if cleaned != addr:
        variants.append(cleaned)
    # 3. Strip "unit/apt/suite X" segments
    no_unit = _UNIT_PATTERN.sub("", cleaned).strip(" ,")
    no_unit = _re.sub(r"\s+", " ", no_unit)
    if no_unit and no_unit != cleaned:
        variants.append(no_unit)
    # 4. Expand "phila" → "Philadelphia, PA"
    if _PHILA_HINTS.search(no_unit) and not _re.search(r"\bphiladelphia\b", no_unit, _re.I):
        expanded = _PHILA_HINTS.sub("Philadelphia, PA", no_unit)
        variants.append(expanded)
    # 5. Append ", PA, USA" if no state present (most AHB work is PA)
    base = no_unit or cleaned
    if base and not _STATE_PATTERN.search(base):
        variants.append(base + ", PA, USA")
    # 6. Append ", USA" only
    if base and ", usa" not in base.lower():
        variants.append(base + ", USA")
    # Dedupe preserving order
    seen = set()
    out  = []
    for v in variants:
        v = v.strip(" ,")
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def _nominatim_lookup(query: str) -> dict | None:
    params = {
        "q":              query,
        "format":         "json",
        "limit":          "1",
        "addressdetails": "0",
        "countrycodes":   "us",
    }
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        if not data:
            return None
        return {
            "lat": float(data[0]["lat"]),
            "lng": float(data[0]["lon"]),
            "display_name": data[0].get("display_name", ""),
            "query": query,
        }
    except Exception as e:
        log.warning(f"nominatim {query!r}: {e}")
        return None


def geocode_address(address: str) -> dict | None:
    """Try multiple progressive variants until Nominatim returns a hit."""
    if not address or not address.strip():
        return None
    variants = _normalize_variants(address)
    for i, v in enumerate(variants):
        result = _nominatim_lookup(v)
        if result:
            if i > 0:
                log.info(f"  matched on variant #{i+1}: {v!r}")
            return result
        if i < len(variants) - 1:
            time.sleep(RATE_LIMIT)  # respect rate limit between variants
    return None


def geocode_all_projects(force: bool = False, progress_cb=None) -> dict:
    """Walk every project with an address. Return summary dict."""
    if not os.path.exists(DASHBOARD_DB):
        return {"ok": False, "error": "dashboard DB not found"}

    conn = sqlite3.connect(DASHBOARD_DB)
    conn.row_factory = sqlite3.Row
    if force:
        rows = conn.execute("""SELECT id, title, address FROM ahb_projects
                               WHERE address IS NOT NULL AND TRIM(address) != ''""").fetchall()
    else:
        rows = conn.execute("""SELECT id, title, address FROM ahb_projects
                               WHERE address IS NOT NULL AND TRIM(address) != ''
                                 AND (latitude IS NULL OR longitude IS NULL)""").fetchall()

    total = len(rows)
    succeeded = 0
    failed    = 0
    results   = []

    for i, row in enumerate(rows, 1):
        addr = row["address"]
        result = geocode_address(addr)
        if result:
            conn.execute("""UPDATE ahb_projects
                            SET latitude=?, longitude=?, geocoded_at=datetime('now')
                            WHERE id=?""",
                         (result["lat"], result["lng"], row["id"]))
            conn.commit()
            succeeded += 1
            results.append({"id": row["id"], "title": row["title"], "address": addr,
                            "lat": result["lat"], "lng": result["lng"], "ok": True})
            log.info(f"[{i}/{total}] ✓ {row['title']}: {addr} → {result['lat']:.5f},{result['lng']:.5f}")
        else:
            failed += 1
            results.append({"id": row["id"], "title": row["title"], "address": addr, "ok": False})
            log.info(f"[{i}/{total}] ✗ {row['title']}: {addr}")
        if progress_cb:
            try: progress_cb(i, total, succeeded, failed)
            except Exception: pass
        if i < total:
            time.sleep(RATE_LIMIT)  # Nominatim ToS

    conn.close()
    return {"ok": True, "total": total, "succeeded": succeeded,
            "failed": failed, "results": results}


def project_geocode_status() -> dict:
    """Quick status counts for the dashboard widget."""
    if not os.path.exists(DASHBOARD_DB):
        return {"with_address": 0, "geocoded": 0, "pending": 0}
    conn = sqlite3.connect(DASHBOARD_DB)
    with_addr = conn.execute("SELECT count(*) FROM ahb_projects WHERE address IS NOT NULL AND TRIM(address)!=''").fetchone()[0]
    geo = conn.execute("SELECT count(*) FROM ahb_projects WHERE latitude IS NOT NULL AND longitude IS NOT NULL").fetchone()[0]
    conn.close()
    return {"with_address": with_addr, "geocoded": geo, "pending": with_addr - geo}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="Re-geocode even projects already done")
    p.add_argument("--status", action="store_true", help="Just show status counts")
    args = p.parse_args()
    if args.status:
        print(json.dumps(project_geocode_status(), indent=2))
    else:
        print(json.dumps(geocode_all_projects(force=args.force), indent=2, default=str))
