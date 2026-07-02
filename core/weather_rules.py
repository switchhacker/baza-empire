#!/usr/bin/env python3
"""
Jobsite weather threshold rules engine.

Pure functions: takes the normalized `weather_sources.get_forecast()` /
`get_active_alerts()` output plus a jobsite profile and produces a flat list
of hazard dicts. No I/O, no HTTP — the (later) weather-watch cron owns
scheduling/dedup/notification.

Threshold rules (exact, per spec):
  heat:  heat-index >=90F fyi / >=103F alert, any work-hour reading that day.
  rain:  work-hour precip_prob >=50% OR daily precip_in >=0.1in — exterior only.
  wind:  daily wind_mph >=20 sustained OR gust_mph >=35 — exterior only.
  cold:  daily low_f <40 when "concrete" or "masonry" in trades (cold_concrete);
         daily low_f <50 when "paint" in trades (cold_paint).
  nws:   every active NWS alert -> hazard severity "alert" (or "fyi" if the
         alert's own severity == "Minor"), independent of day position.

Day-position tiering (non-NWS hazards only): today/tomorrow (index 0,1) may
reach "alert"; days 2-6 are always capped to "fyi" regardless of magnitude.
"""
from __future__ import annotations

from datetime import datetime

HEAT_FYI_F = 90.0
HEAT_ALERT_F = 103.0
RAIN_PRECIP_PROB_PCT = 50
RAIN_PRECIP_IN = 0.1
WIND_SUSTAINED_MPH = 20
WIND_GUST_MPH = 35
COLD_CONCRETE_F = 40
COLD_PAINT_F = 50

ALERT_ELIGIBLE_DAY_INDEXES = 2  # today + tomorrow (indexes 0, 1)


def heat_index_f(temp_f: float, rh: float) -> float:
    """NWS Rothfusz regression heat index, degrees F.

    Uses the simple averaging formula when its average with the raw
    temperature is below 80F (Rothfusz regression is undefined/unstable
    outside its fitted range), otherwise the full regression with NWS's
    low-humidity / high-humidity adjustments.
    """
    t, r = float(temp_f), float(rh)

    simple = 0.5 * (t + 61.0 + ((t - 68.0) * 1.2) + (r * 0.094))
    if (simple + t) / 2.0 < 80.0:
        return simple

    hi = (
        -42.379
        + 2.04901523 * t
        + 10.14333127 * r
        - 0.22475541 * t * r
        - 0.00683783 * t * t
        - 0.05481717 * r * r
        + 0.00122874 * t * t * r
        + 0.00085282 * t * r * r
        - 0.00000199 * t * t * r * r
    )

    if r < 13 and 80 <= t <= 112:
        hi -= ((13 - r) / 4.0) * (((17 - abs(t - 95.0)) / 17.0) ** 0.5)
    elif r > 85 and 80 <= t <= 87:
        hi += ((r - 85) / 10.0) * ((87 - t) / 5.0)

    return hi


def default_profile() -> dict:
    return {"exterior": True, "trades": []}


def _hour_of(ts: str) -> int | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).hour
    except ValueError:
        return None


def _in_work_hours(ts: str, work_hours: tuple[int, int]) -> bool:
    h = _hour_of(ts)
    if h is None:
        return False
    start, end = work_hours
    return start <= h < end


def _day_severity(day_index: int, is_hazard: bool) -> str | None:
    """Single-threshold hazards (rain/wind/cold): alert on day 0-1, fyi after."""
    if not is_hazard:
        return None
    return "alert" if day_index < ALERT_ELIGIBLE_DAY_INDEXES else "fyi"


def evaluate(
    forecast: dict,
    alerts: list[dict],
    profile: dict,
    work_hours: tuple[int, int] = (7, 18),
) -> list[dict]:
    hazards: list[dict] = []

    daily = (forecast or {}).get("daily") or []
    hourly = (forecast or {}).get("hourly") or []
    exterior = bool((profile or {}).get("exterior", True))
    trades = (profile or {}).get("trades") or []

    dates = [d.get("date") for d in daily]

    for i, day in enumerate(daily):
        date = day.get("date")
        alert_eligible = i < ALERT_ELIGIBLE_DAY_INDEXES

        day_hours = [h for h in hourly if (h.get("ts") or "").startswith(date or "\0")]
        work_day_hours = [h for h in day_hours if _in_work_hours(h.get("ts"), work_hours)]

        # ── heat ──
        max_hi = None
        for h in work_day_hours:
            temp_f, rh = h.get("temp_f"), h.get("rh")
            if temp_f is None or rh is None:
                continue
            hi = heat_index_f(temp_f, rh)
            if max_hi is None or hi > max_hi:
                max_hi = hi
        if max_hi is not None:
            if max_hi >= HEAT_ALERT_F:
                sev = "alert" if alert_eligible else "fyi"
                hazards.append({
                    "key_suffix": f"heat:{date}", "hazard": "heat", "severity": sev,
                    "date": date, "detail": f"Heat index {max_hi:.0f}°F during work hours — extreme heat risk",
                })
            elif max_hi >= HEAT_FYI_F:
                hazards.append({
                    "key_suffix": f"heat:{date}", "hazard": "heat", "severity": "fyi",
                    "date": date, "detail": f"Heat index {max_hi:.0f}°F during work hours — heat caution",
                })

        # ── rain (exterior only) ──
        if exterior:
            max_precip_prob = 0
            for h in work_day_hours:
                p = h.get("precip_prob")
                if p is not None and p > max_precip_prob:
                    max_precip_prob = p
            precip_in = day.get("precip_in") or 0.0
            if max_precip_prob >= RAIN_PRECIP_PROB_PCT or precip_in >= RAIN_PRECIP_IN:
                sev = _day_severity(i, True)
                hazards.append({
                    "key_suffix": f"rain:{date}", "hazard": "rain", "severity": sev,
                    "date": date,
                    "detail": f"Rain likely ({max_precip_prob:.0f}% work-hour prob / {precip_in:.2f}in forecast)",
                })

            # ── wind (exterior only) ──
            wind_mph = day.get("wind_mph") or 0
            gust_mph = day.get("gust_mph") or 0
            if wind_mph >= WIND_SUSTAINED_MPH or gust_mph >= WIND_GUST_MPH:
                sev = _day_severity(i, True)
                hazards.append({
                    "key_suffix": f"wind:{date}", "hazard": "wind", "severity": sev,
                    "date": date,
                    "detail": f"Wind {wind_mph:.0f} mph sustained / {gust_mph:.0f} mph gust",
                })

        # ── cold (trade-gated) ──
        low_f = day.get("low_f")
        if low_f is not None:
            if ("concrete" in trades or "masonry" in trades) and low_f < COLD_CONCRETE_F:
                sev = _day_severity(i, True)
                hazards.append({
                    "key_suffix": f"cold_concrete:{date}", "hazard": "cold_concrete", "severity": sev,
                    "date": date, "detail": f"Low {low_f:.0f}°F — concrete/masonry cure risk",
                })
            if "paint" in trades and low_f < COLD_PAINT_F:
                sev = _day_severity(i, True)
                hazards.append({
                    "key_suffix": f"cold_paint:{date}", "hazard": "cold_paint", "severity": sev,
                    "date": date, "detail": f"Low {low_f:.0f}°F — paint application risk",
                })

    # ── NWS alerts: always mapped from the alert's own severity, independent
    #    of day-position tiering. ──
    fallback_date = dates[0] if dates else None
    for a in alerts or []:
        event = a.get("event") or "Alert"
        onset = a.get("onset") or ""
        date = onset[:10] if onset else fallback_date
        sev = "fyi" if a.get("severity") == "Minor" else "alert"
        hazard = f"nws:{event}"
        hazards.append({
            "key_suffix": f"{hazard}:{date}",
            "hazard": hazard,
            "severity": sev,
            "date": date,
            "detail": a.get("headline") or event,
        })

    return hazards
