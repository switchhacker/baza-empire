#!/usr/bin/env python3
"""
Pure weather-data clients: NWS (api.weather.gov) primary, Open-Meteo fallback.

No side effects beyond HTTP GET. All network access funnels through the
single `_fetch_json` seam so tests can monkeypatch it and never touch the
network. Consumed by the (later) jobsite weather-watch cron.

Normalized shapes:
    get_forecast(lat, lon) -> {
        "source": "nws" | "open_meteo",
        "daily":  [{"date","high_f","low_f","precip_prob_max","precip_in",
                    "wind_mph","gust_mph","conditions"}, ...] (<=7),
        "hourly": [{"ts","temp_f","rh","precip_prob","wind_mph","gust_mph"}, ...] (<=48),
    } | None

    get_active_alerts(lat, lon) -> [{"id","event","severity","headline","onset","ends"}]
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

USER_AGENT = "baza-empire/1.0 (contactahbco@gmail.com)"

NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
NWS_ALERTS_URL = "https://api.weather.gov/alerts/active?point={lat},{lon}"

OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&hourly=temperature_2m,relative_humidity_2m,precipitation_probability,wind_speed_10m,wind_gusts_10m"
    "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max"
    "&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch"
    "&timezone=America/New_York&forecast_days=7"
)

_WIND_NUM_RE = re.compile(r"\d+")


# ── HTTP seam ──────────────────────────────────────────────────────────

def _fetch_json(url: str, timeout: int = 10) -> dict | None:
    """Single HTTP seam. GET url, parse JSON, return None on any failure."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/geo+json, application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    except Exception:
        return None


# ── shared helpers ───────────────────────────────────────────────────────

def _parse_wind_mph(text) -> int | None:
    """'10 to 20 mph' -> 20, '10 mph' -> 10, None/'' -> None."""
    if not text or not isinstance(text, str):
        return None
    nums = _WIND_NUM_RE.findall(text)
    if not nums:
        return None
    return max(int(n) for n in nums)


def _at(seq, i):
    return seq[i] if seq is not None and i < len(seq) else None


# ── NWS ────────────────────────────────────────────────────────────────

def _parse_nws_hourly(data: dict) -> list[dict]:
    periods = (data.get("properties") or {}).get("periods") or []
    out = []
    for p in periods[:48]:
        precip = p.get("probabilityOfPrecipitation") or {}
        rh = p.get("relativeHumidity") or {}
        out.append({
            "ts": p.get("startTime"),
            "temp_f": p.get("temperature"),
            "rh": rh.get("value"),
            "precip_prob": precip.get("value"),
            "wind_mph": _parse_wind_mph(p.get("windSpeed")),
            "gust_mph": _parse_wind_mph(p.get("windGust")),
        })
    return out


def _parse_nws_daily(data: dict) -> list[dict]:
    """NWS `forecast` periods alternate day/night ~12h blocks; merge by date."""
    periods = (data.get("properties") or {}).get("periods") or []
    days: dict[str, dict] = {}
    order: list[str] = []
    for p in periods:
        start = p.get("startTime") or ""
        date = start[:10]
        if not date:
            continue
        if date not in days:
            days[date] = {
                "date": date, "high_f": None, "low_f": None,
                "precip_prob_max": None, "precip_in": 0.0,
                "wind_mph": None, "gust_mph": None, "conditions": None,
            }
            order.append(date)
        d = days[date]
        temp = p.get("temperature")
        wind = _parse_wind_mph(p.get("windSpeed"))
        gust = _parse_wind_mph(p.get("windGust"))
        precip = (p.get("probabilityOfPrecipitation") or {}).get("value")

        if p.get("isDaytime"):
            d["high_f"] = temp
        else:
            d["low_f"] = temp
        if d["conditions"] is None:
            d["conditions"] = p.get("shortForecast")
        if precip is not None:
            d["precip_prob_max"] = max(d["precip_prob_max"] or 0, precip)
        if wind is not None:
            d["wind_mph"] = max(d["wind_mph"] or 0, wind)
        if gust is not None:
            d["gust_mph"] = max(d["gust_mph"] or 0, gust)

    return [days[d] for d in order[:7]]


def _get_forecast_nws(lat: float, lon: float) -> dict | None:
    points = _fetch_json(NWS_POINTS_URL.format(lat=lat, lon=lon))
    if not points:
        return None
    try:
        props = points["properties"]
        hourly_url = props["forecastHourly"]
        daily_url = props["forecast"]
    except (KeyError, TypeError):
        return None

    hourly_data = _fetch_json(hourly_url)
    daily_data = _fetch_json(daily_url)
    if not hourly_data or not daily_data:
        return None

    try:
        hourly = _parse_nws_hourly(hourly_data)
        daily = _parse_nws_daily(daily_data)
    except (KeyError, TypeError, ValueError):
        return None
    if not hourly or not daily:
        return None

    return {"source": "nws", "daily": daily, "hourly": hourly}


# ── Open-Meteo (fallback) ─────────────────────────────────────────────

def _parse_om_hourly(hourly: dict) -> list[dict]:
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    rh = hourly.get("relative_humidity_2m") or []
    precip = hourly.get("precipitation_probability") or []
    wind = hourly.get("wind_speed_10m") or []
    gust = hourly.get("wind_gusts_10m") or []
    out = []
    for i, ts in enumerate(times[:48]):
        out.append({
            "ts": ts,
            "temp_f": _at(temps, i),
            "rh": _at(rh, i),
            "precip_prob": _at(precip, i),
            "wind_mph": _at(wind, i),
            "gust_mph": _at(gust, i),
        })
    return out


def _parse_om_daily(daily: dict) -> list[dict]:
    dates = daily.get("time") or []
    hi = daily.get("temperature_2m_max") or []
    lo = daily.get("temperature_2m_min") or []
    precip_sum = daily.get("precipitation_sum") or []
    precip_prob = daily.get("precipitation_probability_max") or []
    wind = daily.get("wind_speed_10m_max") or []
    gust = daily.get("wind_gusts_10m_max") or []
    out = []
    for i, date in enumerate(dates[:7]):
        out.append({
            "date": date,
            "high_f": _at(hi, i),
            "low_f": _at(lo, i),
            "precip_prob_max": _at(precip_prob, i),
            "precip_in": _at(precip_sum, i),
            "wind_mph": _at(wind, i),
            "gust_mph": _at(gust, i),
            # Open-Meteo weathercode wasn't requested (kept minimal per spec URL);
            # leave conditions blank rather than guess.
            "conditions": "",
        })
    return out


def _get_forecast_open_meteo(lat: float, lon: float) -> dict | None:
    url = OPEN_METEO_URL.format(lat=lat, lon=lon)
    data = _fetch_json(url)
    if not data:
        return None
    try:
        hourly = _parse_om_hourly(data.get("hourly") or {})
        daily = _parse_om_daily(data.get("daily") or {})
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if not hourly or not daily:
        return None
    return {"source": "open_meteo", "daily": daily, "hourly": hourly}


# ── public API ────────────────────────────────────────────────────────

def get_forecast(lat: float, lon: float) -> dict | None:
    """NWS primary, Open-Meteo fallback. None if both sources fail."""
    result = _get_forecast_nws(lat, lon)
    if result is not None:
        return result
    return _get_forecast_open_meteo(lat, lon)


def get_active_alerts(lat: float, lon: float) -> list[dict]:
    """Active NWS alerts covering (lat, lon). Empty list on any failure."""
    data = _fetch_json(NWS_ALERTS_URL.format(lat=lat, lon=lon))
    if not data:
        return []
    out = []
    for feature in data.get("features") or []:
        props = feature.get("properties") or {}
        out.append({
            "id": feature.get("id") or props.get("id"),
            "event": props.get("event"),
            "severity": props.get("severity"),
            "headline": props.get("headline"),
            "onset": props.get("onset"),
            "ends": props.get("ends"),
        })
    return out


if __name__ == "__main__":
    import sys as _sys
    _lat, _lon = 40.1, -74.95
    if len(_sys.argv) >= 3:
        _lat, _lon = float(_sys.argv[1]), float(_sys.argv[2])
    print(json.dumps(get_forecast(_lat, _lon), indent=2, default=str))
    print(json.dumps(get_active_alerts(_lat, _lon), indent=2, default=str))
