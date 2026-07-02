"""Tests for core/weather_sources.py — NWS primary / Open-Meteo fallback weather clients.

All HTTP is monkeypatched via the single `_fetch_json` seam. No live network calls.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import weather_sources as ws


# ── canned NWS fixtures ──────────────────────────────────────────────────

NWS_POINTS = {
    "properties": {
        "forecastHourly": "https://api.weather.gov/gridpoints/PHI/50,75/forecast/hourly",
        "forecast": "https://api.weather.gov/gridpoints/PHI/50,75/forecast",
    }
}

NWS_HOURLY = {
    "properties": {
        "periods": [
            {
                "startTime": "2026-07-02T09:00:00-04:00",
                "temperature": 78,
                "relativeHumidity": {"value": 55},
                "probabilityOfPrecipitation": {"value": 20},
                "windSpeed": "5 to 10 mph",
                "windGust": None,
            },
            {
                "startTime": "2026-07-02T10:00:00-04:00",
                "temperature": 82,
                "relativeHumidity": {"value": 58},
                "probabilityOfPrecipitation": {"value": 30},
                "windSpeed": "10 mph",
                "windGust": "20 mph",
            },
        ]
    }
}

NWS_DAILY = {
    "properties": {
        "periods": [
            {
                "startTime": "2026-07-02T06:00:00-04:00",
                "isDaytime": True,
                "temperature": 88,
                "probabilityOfPrecipitation": {"value": 30},
                "windSpeed": "10 to 15 mph",
                "windGust": "25 mph",
                "shortForecast": "Sunny",
            },
            {
                "startTime": "2026-07-02T18:00:00-04:00",
                "isDaytime": False,
                "temperature": 65,
                "probabilityOfPrecipitation": {"value": 10},
                "windSpeed": "5 mph",
                "windGust": None,
                "shortForecast": "Clear",
            },
            {
                "startTime": "2026-07-03T06:00:00-04:00",
                "isDaytime": True,
                "temperature": 90,
                "probabilityOfPrecipitation": {"value": 40},
                "windSpeed": "15 mph",
                "windGust": None,
                "shortForecast": "Partly Cloudy",
            },
            {
                "startTime": "2026-07-03T18:00:00-04:00",
                "isDaytime": False,
                "temperature": 68,
                "probabilityOfPrecipitation": {"value": 10},
                "windSpeed": "5 mph",
                "windGust": None,
                "shortForecast": "Clear",
            },
        ]
    }
}

NWS_ALERTS = {
    "features": [
        {
            "id": "urn:oid:1",
            "properties": {
                "event": "Heat Advisory",
                "severity": "Moderate",
                "headline": "Heat Advisory in effect",
                "onset": "2026-07-02T12:00:00-04:00",
                "ends": "2026-07-02T20:00:00-04:00",
            },
        },
        {
            "id": "urn:oid:2",
            "properties": {
                "event": "Small Craft Advisory",
                "severity": "Minor",
                "headline": "Minor advisory",
                "onset": "2026-07-02T12:00:00-04:00",
                "ends": "2026-07-02T20:00:00-04:00",
            },
        },
    ]
}

OPEN_METEO = {
    "hourly": {
        "time": ["2026-07-02T09:00", "2026-07-02T10:00"],
        "temperature_2m": [79, 83],
        "relative_humidity_2m": [50, 52],
        "precipitation_probability": [10, 15],
        "wind_speed_10m": [8, 12],
        "wind_gusts_10m": [15, 22],
    },
    "daily": {
        "time": ["2026-07-02", "2026-07-03"],
        "temperature_2m_max": [89, 91],
        "temperature_2m_min": [66, 69],
        "precipitation_sum": [0.05, 0.2],
        "precipitation_probability_max": [25, 45],
        "wind_speed_10m_max": [14, 18],
        "wind_gusts_10m_max": [21, 30],
    },
}


def make_fetch(mapping):
    """Return a fake `_fetch_json(url, timeout=10)` that dispatches on URL substring."""
    def _fake(url, timeout=10):
        for needle, payload in mapping.items():
            if needle in url:
                return payload
        return None
    return _fake


# ── _fetch_json seam sanity ──────────────────────────────────────────────

def test_fetch_json_sets_user_agent(monkeypatch):
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=10):
        captured["headers"] = req.headers
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(ws.urllib.request, "urlopen", fake_urlopen)
    result = ws._fetch_json("https://example.com/x")
    assert result == {"ok": True}
    # urllib.request.Request lower-cases header keys internally except first letter
    assert any(k.lower() == "user-agent" for k in captured["headers"])
    ua_key = [k for k in captured["headers"] if k.lower() == "user-agent"][0]
    assert "baza-empire" in captured["headers"][ua_key]


def test_fetch_json_returns_none_on_exception(monkeypatch):
    def fake_urlopen(req, timeout=10):
        raise TimeoutError("boom")
    monkeypatch.setattr(ws.urllib.request, "urlopen", fake_urlopen)
    assert ws._fetch_json("https://example.com/x") is None


# ── NWS normalization ─────────────────────────────────────────────────────

def test_nws_normalization(monkeypatch):
    monkeypatch.setattr(ws, "_fetch_json", make_fetch({
        "points/40.1,-74.95": NWS_POINTS,
        "gridpoints/PHI/50,75/forecast/hourly": NWS_HOURLY,
        "gridpoints/PHI/50,75/forecast": NWS_DAILY,
    }))
    result = ws.get_forecast(40.1, -74.95)
    assert result is not None
    assert result["source"] == "nws"

    hourly = result["hourly"]
    assert len(hourly) == 2
    h0 = hourly[0]
    assert h0["ts"] == "2026-07-02T09:00:00-04:00"
    assert h0["temp_f"] == 78
    assert h0["rh"] == 55
    assert h0["precip_prob"] == 20
    assert h0["wind_mph"] == 10  # "5 to 10 mph" -> max int
    assert h0["gust_mph"] is None
    h1 = hourly[1]
    assert h1["wind_mph"] == 10
    assert h1["gust_mph"] == 20

    daily = result["daily"]
    assert len(daily) == 2
    d0 = daily[0]
    assert d0["date"] == "2026-07-02"
    assert d0["high_f"] == 88
    assert d0["low_f"] == 65
    assert d0["precip_prob_max"] == 30
    assert d0["wind_mph"] == 15  # max of day(10-15) and night(5) periods
    assert d0["gust_mph"] == 25
    assert d0["conditions"] == "Sunny"
    d1 = daily[1]
    assert d1["date"] == "2026-07-03"
    assert d1["high_f"] == 90
    assert d1["low_f"] == 68


def test_open_meteo_fallback_when_nws_none(monkeypatch):
    """When the NWS points lookup fails, get_forecast falls back to Open-Meteo."""
    monkeypatch.setattr(ws, "_fetch_json", make_fetch({
        "api.open-meteo.com": OPEN_METEO,
    }))
    result = ws.get_forecast(40.1, -74.95)
    assert result is not None
    assert result["source"] == "open_meteo"
    assert len(result["hourly"]) == 2
    assert result["hourly"][0]["temp_f"] == 79
    assert result["hourly"][0]["wind_mph"] == 8
    assert result["hourly"][0]["gust_mph"] == 15
    assert len(result["daily"]) == 2
    assert result["daily"][0]["date"] == "2026-07-02"
    assert result["daily"][0]["high_f"] == 89
    assert result["daily"][0]["low_f"] == 66
    assert result["daily"][0]["precip_in"] == 0.05
    assert result["daily"][0]["precip_prob_max"] == 25
    assert result["daily"][1]["gust_mph"] == 30


def test_open_meteo_fallback_when_nws_hourly_missing(monkeypatch):
    """NWS points resolves but the hourly/daily fetch fails -> still falls back."""
    monkeypatch.setattr(ws, "_fetch_json", make_fetch({
        "points/40.1,-74.95": NWS_POINTS,
        # forecastHourly / forecast URLs deliberately absent -> return None
        "api.open-meteo.com": OPEN_METEO,
    }))
    result = ws.get_forecast(40.1, -74.95)
    assert result["source"] == "open_meteo"


def test_get_forecast_none_when_both_sources_fail(monkeypatch):
    monkeypatch.setattr(ws, "_fetch_json", lambda url, timeout=10: None)
    assert ws.get_forecast(40.1, -74.95) is None


# ── alerts ─────────────────────────────────────────────────────────────

def test_alerts_parse(monkeypatch):
    monkeypatch.setattr(ws, "_fetch_json", make_fetch({
        "alerts/active": NWS_ALERTS,
    }))
    alerts = ws.get_active_alerts(40.1, -74.95)
    assert len(alerts) == 2
    a0 = alerts[0]
    assert a0["id"] == "urn:oid:1"
    assert a0["event"] == "Heat Advisory"
    assert a0["severity"] == "Moderate"
    assert a0["headline"] == "Heat Advisory in effect"
    assert a0["onset"] == "2026-07-02T12:00:00-04:00"
    assert a0["ends"] == "2026-07-02T20:00:00-04:00"


def test_alerts_empty_on_fetch_failure(monkeypatch):
    monkeypatch.setattr(ws, "_fetch_json", lambda url, timeout=10: None)
    assert ws.get_active_alerts(40.1, -74.95) == []
