"""Tests for core/weather_rules.py — heat index + jobsite threshold rules engine."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import weather_rules as wr


# ── helpers ────────────────────────────────────────────────────────────

def make_day(date, high_f=80, low_f=60, precip_prob_max=0, precip_in=0.0,
             wind_mph=5, gust_mph=10, conditions="Clear"):
    return {
        "date": date, "high_f": high_f, "low_f": low_f,
        "precip_prob_max": precip_prob_max, "precip_in": precip_in,
        "wind_mph": wind_mph, "gust_mph": gust_mph, "conditions": conditions,
    }


def make_hour(ts, temp_f=75, rh=50, precip_prob=0, wind_mph=5, gust_mph=10):
    return {
        "ts": ts, "temp_f": temp_f, "rh": rh,
        "precip_prob": precip_prob, "wind_mph": wind_mph, "gust_mph": gust_mph,
    }


def find(hazards, hazard, date):
    return [h for h in hazards if h["hazard"] == hazard and h["date"] == date]


# ── heat_index_f ───────────────────────────────────────────────────────

def test_heat_index_known_value():
    hi = wr.heat_index_f(95, 60)
    assert 112 <= hi <= 116  # 114 +/- 2 per spec


def test_heat_index_low_avg_uses_simple_formula():
    # T=70, RH=50 -> avg well under 80 -> simple formula, no Rothfusz blowup
    hi = wr.heat_index_f(70, 50)
    assert 68 <= hi <= 72


# ── default_profile ──────────────────────────────────────────────────

def test_default_profile_shape():
    p = wr.default_profile()
    assert p == {"exterior": True, "trades": []}


# ── evaluate: heat tiers ────────────────────────────────────────────────

def test_evaluate_heat_tiers():
    daily = [
        make_day("2026-07-02"),  # day0 -> alert-eligible, hot -> alert
        make_day("2026-07-03"),  # day1 -> alert-eligible, moderate -> fyi
    ]
    hourly = [
        make_hour("2026-07-02T14:00:00-04:00", temp_f=95, rh=60),  # HI ~113 -> alert
        make_hour("2026-07-03T14:00:00-04:00", temp_f=90, rh=50),  # HI ~94.6 -> fyi
    ]
    forecast = {"source": "nws", "daily": daily, "hourly": hourly}
    hazards = wr.evaluate(forecast, [], wr.default_profile())

    hot = find(hazards, "heat", "2026-07-02")
    assert len(hot) == 1
    assert hot[0]["severity"] == "alert"
    assert hot[0]["key_suffix"] == "heat:2026-07-02"

    moderate = find(hazards, "heat", "2026-07-03")
    assert len(moderate) == 1
    assert moderate[0]["severity"] == "fyi"


def test_evaluate_heat_below_threshold_no_hazard():
    daily = [make_day("2026-07-02")]
    hourly = [make_hour("2026-07-02T14:00:00-04:00", temp_f=75, rh=40)]
    forecast = {"source": "nws", "daily": daily, "hourly": hourly}
    hazards = wr.evaluate(forecast, [], wr.default_profile())
    assert find(hazards, "heat", "2026-07-02") == []


def test_evaluate_heat_ignores_off_hours():
    # Extreme heat reading exists but outside work hours (7-18) -> no hazard.
    daily = [make_day("2026-07-02")]
    hourly = [make_hour("2026-07-02T22:00:00-04:00", temp_f=100, rh=70)]
    forecast = {"source": "nws", "daily": daily, "hourly": hourly}
    hazards = wr.evaluate(forecast, [], wr.default_profile())
    assert find(hazards, "heat", "2026-07-02") == []


# ── evaluate: rain (exterior only) ─────────────────────────────────────

def test_evaluate_rain_exterior_only():
    daily = [make_day("2026-07-02", precip_in=0.2)]
    forecast = {"source": "nws", "daily": daily, "hourly": []}

    exterior_profile = {"exterior": True, "trades": []}
    hazards = wr.evaluate(forecast, [], exterior_profile)
    assert len(find(hazards, "rain", "2026-07-02")) == 1

    interior_profile = {"exterior": False, "trades": []}
    hazards2 = wr.evaluate(forecast, [], interior_profile)
    assert find(hazards2, "rain", "2026-07-02") == []


def test_evaluate_rain_via_work_hour_precip_prob():
    daily = [make_day("2026-07-02", precip_in=0.0)]
    hourly = [make_hour("2026-07-02T09:00:00-04:00", precip_prob=60)]
    forecast = {"source": "nws", "daily": daily, "hourly": hourly}
    hazards = wr.evaluate(forecast, [], {"exterior": True, "trades": []})
    assert len(find(hazards, "rain", "2026-07-02")) == 1


# ── evaluate: wind ───────────────────────────────────────────────────────

def test_evaluate_wind_gust():
    # sustained wind below 20 but gust >= 35 must still trigger, exterior only
    daily = [make_day("2026-07-02", wind_mph=12, gust_mph=40)]
    forecast = {"source": "nws", "daily": daily, "hourly": []}

    hazards = wr.evaluate(forecast, [], {"exterior": True, "trades": []})
    assert len(find(hazards, "wind", "2026-07-02")) == 1

    hazards_interior = wr.evaluate(forecast, [], {"exterior": False, "trades": []})
    assert find(hazards_interior, "wind", "2026-07-02") == []


def test_evaluate_wind_sustained():
    daily = [make_day("2026-07-02", wind_mph=25, gust_mph=28)]
    forecast = {"source": "nws", "daily": daily, "hourly": []}
    hazards = wr.evaluate(forecast, [], {"exterior": True, "trades": []})
    assert len(find(hazards, "wind", "2026-07-02")) == 1


def test_evaluate_wind_below_threshold_no_hazard():
    daily = [make_day("2026-07-02", wind_mph=10, gust_mph=15)]
    forecast = {"source": "nws", "daily": daily, "hourly": []}
    hazards = wr.evaluate(forecast, [], {"exterior": True, "trades": []})
    assert find(hazards, "wind", "2026-07-02") == []


# ── evaluate: cold (trade-gated) ────────────────────────────────────────

def test_evaluate_cold_requires_trade():
    daily = [make_day("2026-07-02", low_f=35)]
    forecast = {"source": "nws", "daily": daily, "hourly": []}

    # No relevant trade -> no cold hazard at all
    hazards_none = wr.evaluate(forecast, [], {"exterior": True, "trades": []})
    assert find(hazards_none, "cold_concrete", "2026-07-02") == []
    assert find(hazards_none, "cold_paint", "2026-07-02") == []

    # concrete trade -> cold_concrete only (35 < 40)
    hazards_concrete = wr.evaluate(forecast, [], {"exterior": True, "trades": ["concrete"]})
    assert len(find(hazards_concrete, "cold_concrete", "2026-07-02")) == 1
    assert find(hazards_concrete, "cold_paint", "2026-07-02") == []

    # masonry trade also triggers cold_concrete hazard key
    hazards_masonry = wr.evaluate(forecast, [], {"exterior": True, "trades": ["masonry"]})
    assert len(find(hazards_masonry, "cold_concrete", "2026-07-02")) == 1

    # paint trade -> cold_paint only (35 < 50), no cold_concrete
    hazards_paint = wr.evaluate(forecast, [], {"exterior": True, "trades": ["paint"]})
    assert find(hazards_paint, "cold_concrete", "2026-07-02") == []
    assert len(find(hazards_paint, "cold_paint", "2026-07-02")) == 1


def test_evaluate_cold_paint_threshold_not_concrete():
    # low=45: below paint's 50 threshold but not concrete's 40 threshold
    daily = [make_day("2026-07-02", low_f=45)]
    forecast = {"source": "nws", "daily": daily, "hourly": []}
    hazards = wr.evaluate(forecast, [], {"exterior": True, "trades": ["concrete", "paint"]})
    assert find(hazards, "cold_concrete", "2026-07-02") == []
    assert len(find(hazards, "cold_paint", "2026-07-02")) == 1


# ── evaluate: NWS alerts ─────────────────────────────────────────────────

def test_nws_alert_always_alert():
    daily = [make_day("2026-07-02"), make_day("2026-07-03"), make_day("2026-07-04"),
              make_day("2026-07-05"), make_day("2026-07-06"), make_day("2026-07-07"),
              make_day("2026-07-08")]
    forecast = {"source": "nws", "daily": daily, "hourly": []}
    alerts = [
        {"id": "1", "event": "Tornado Warning", "severity": "Severe",
         "headline": "Tornado Warning issued", "onset": "2026-07-08T12:00:00-04:00", "ends": None},
        {"id": "2", "event": "Small Craft Advisory", "severity": "Minor",
         "headline": "Minor advisory", "onset": "2026-07-02T12:00:00-04:00", "ends": None},
    ]
    hazards = wr.evaluate(forecast, alerts, wr.default_profile())

    tornado = [h for h in hazards if h["hazard"] == "nws:Tornado Warning"]
    assert len(tornado) == 1
    assert tornado[0]["severity"] == "alert"
    assert tornado[0]["key_suffix"] == "nws:Tornado Warning:2026-07-08"

    minor = [h for h in hazards if h["hazard"] == "nws:Small Craft Advisory"]
    assert len(minor) == 1
    assert minor[0]["severity"] == "fyi"


# ── evaluate: far-day downgrade ──────────────────────────────────────────

def test_far_day_downgraded_to_fyi():
    daily = [
        make_day("2026-07-02"),  # day0
        make_day("2026-07-03"),  # day1
        make_day("2026-07-04"),  # day2 -> must be capped to fyi even if severe
    ]
    hourly = [
        make_hour("2026-07-04T14:00:00-04:00", temp_f=95, rh=60),  # HI ~113, would be alert on day0/1
    ]
    forecast = {"source": "nws", "daily": daily, "hourly": hourly}
    hazards = wr.evaluate(forecast, [], wr.default_profile())
    far = find(hazards, "heat", "2026-07-04")
    assert len(far) == 1
    assert far[0]["severity"] == "fyi"


def test_near_day_wind_is_alert_far_day_wind_is_fyi():
    daily = [
        make_day("2026-07-02", wind_mph=25),  # day0
        make_day("2026-07-03", wind_mph=5),
        make_day("2026-07-04", wind_mph=25),  # day2 -> same magnitude, must be fyi
    ]
    forecast = {"source": "nws", "daily": daily, "hourly": []}
    hazards = wr.evaluate(forecast, [], {"exterior": True, "trades": []})
    near = find(hazards, "wind", "2026-07-02")
    far = find(hazards, "wind", "2026-07-04")
    assert near[0]["severity"] == "alert"
    assert far[0]["severity"] == "fyi"
