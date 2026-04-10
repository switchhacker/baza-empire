#!/usr/bin/env python3
"""
Shared Skill: weather
Get current weather and forecast using wttr.in (no API key needed).
"""
import os
import json
import socket
import urllib.request
import urllib.parse

# Force IPv6 — IPv4 unreachable on this host
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET6] or _orig(*a, **k)

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
location = args.get("location", os.environ.get("EMPIRE_LOCATION", "New York"))

encoded = urllib.parse.quote(location)
url = f"https://wttr.in/{encoded}?format=j1"

try:
    req = urllib.request.Request(url, headers={"User-Agent": "BazaEmpire/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    current = data["current_condition"][0]
    today = data["weather"][0]
    tomorrow = data["weather"][1]

    temp_f = current.get("temp_F", "?")
    temp_c = current.get("temp_C", "?")
    feels_f = current.get("FeelsLikeF", "?")
    humidity = current.get("humidity", "?")
    desc = current["weatherDesc"][0]["value"]
    wind_mph = current.get("windspeedMiles", "?")

    max_f = today.get("maxtempF", "?")
    min_f = today.get("mintempF", "?")
    tomorrow_desc = tomorrow["hourly"][4]["weatherDesc"][0]["value"]
    tomorrow_max = tomorrow.get("maxtempF", "?")

    print("=== Weather ===")
    print(f"  Location:    {location}")
    print(f"  Now:         {temp_f}°F ({temp_c}°C) — {desc}")
    print(f"  Feels Like:  {feels_f}°F")
    print(f"  Humidity:    {humidity}%")
    print(f"  Wind:        {wind_mph} mph")
    print(f"  Today:       High {max_f}°F / Low {min_f}°F")
    print(f"  Tomorrow:    {tomorrow_desc}, High {tomorrow_max}°F")

except Exception as e:
    print(f"Error fetching weather: {e}")
