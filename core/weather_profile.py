#!/usr/bin/env python3
"""
Jobsite weather-profile classifier.

`get_weather_profile(conn, project_row)` resolves a project's weather
sensitivity profile (`{"exterior": bool, "trades": [...]}`) consumed by
`core/weather_rules.evaluate()`. `ahb_projects.weather_profile` is a
read-through JSON cache: if already set, parse and return it (no LLM
call). Otherwise classify the project's scope/description with a single
local Ollama call (qwen2.5:14b, via `agents.cron_helpers.ollama_generate`
-- deferred-imported to avoid a hard import-time dependency from core/ on
agents/), parse the response defensively, cache a successful
classification back to the column, and fall back to
`core.weather_rules.default_profile()` on ANY failure (missing project
fields, Ollama unavailable, garbage/malformed output, DB write failure).
`default_profile()` is `exterior=True` -- the conservative "assume it
needs weather coverage" fallback, so a classifier hiccup never silently
under-alerts a jobsite.

A failed classification is deliberately NOT cached (the column is left
as-is) so the next cron run gets another chance at a real LLM
classification instead of being stuck on a fallback forever.

`ensure_weather_profile_column(conn)` is the idempotent migration
companion (`ALTER TABLE ahb_projects ADD COLUMN weather_profile TEXT` if
missing), mirroring the `ahb_projects.latitude/longitude` pattern already
backfilled by `core/geocode.py`.
"""
from __future__ import annotations

import json
import logging
import re

from core.weather_rules import default_profile

log = logging.getLogger("weather_profile")

MODEL = "qwen2.5:14b"

CLASSIFY_SYSTEM_PROMPT = (
    "You are a construction jobsite classifier for a general contractor's "
    "weather-alerting system. Given a project's scope and description, "
    "respond with ONLY compact JSON (no prose, no markdown fences) in "
    "exactly this shape: "
    '{"exterior": true|false, "trades": ["concrete", "masonry", "paint"]}. '
    "exterior=true if any work happens outside (roofing, siding, decks, "
    "driveways, concrete pours, exterior paint, landscaping, additions "
    "with new exterior walls, etc). exterior=false only for purely "
    "interior work (interior remodel, plumbing/electrical indoors, "
    "interior paint only, etc). trades is a lowercase list of any "
    "relevant trades touching weather-sensitive materials -- include "
    "'concrete' or 'masonry' if the job pours/sets concrete or masonry, "
    "'paint' if it involves paint or coatings, and any other trades that "
    "clearly apply (roofing, framing, electrical, plumbing, hvac, "
    "drywall, flooring, landscaping). Use an empty list if none are "
    "clearly relevant. Respond with the JSON object and nothing else."
)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def ensure_weather_profile_column(conn) -> None:
    """Idempotent migration: add `ahb_projects.weather_profile` (TEXT) if
    it doesn't already exist. Never raises -- a migration hiccup is
    logged and swallowed, matching this codebase's cron-safety posture."""
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(ahb_projects)").fetchall()}
        if "weather_profile" not in cols:
            conn.execute("ALTER TABLE ahb_projects ADD COLUMN weather_profile TEXT")
            conn.commit()
    except Exception as e:
        log.warning(f"ensure_weather_profile_column failed: {e}")


def _row_get(row, key):
    """dict- or sqlite3.Row-safe optional field access -> None if absent."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _normalize_profile(parsed) -> dict:
    """Coerce a parsed-JSON classifier response into the canonical
    {"exterior": bool, "trades": [str, ...]} shape. Raises ValueError if
    `parsed` isn't even a JSON object -- callers treat that as a failure."""
    if not isinstance(parsed, dict):
        raise ValueError("classifier output is not a JSON object")
    exterior = bool(parsed.get("exterior", True))
    trades_raw = parsed.get("trades", [])
    if not isinstance(trades_raw, list):
        trades_raw = []
    trades = sorted({
        t.strip().lower() for t in trades_raw
        if isinstance(t, str) and t.strip()
    })
    return {"exterior": exterior, "trades": trades}


def _parse_llm_json(raw: str):
    """Defensive JSON extraction: try a clean parse first, then fall back
    to pulling the first {...} block out of surrounding prose. Returns
    None (never raises) if nothing parseable is found."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        return None


def get_weather_profile(conn, project_row) -> dict:
    """Read-through-cached weather profile for one ahb_projects row.

    `project_row` must support item access (sqlite3.Row or dict) --
    "id", "weather_profile", "scope", "description" are all read
    defensively (missing/None treated as absent/empty). Never raises:
    any failure anywhere in this function (missing fields, Ollama
    unavailable, garbage LLM output, DB write failure) falls back to
    core.weather_rules.default_profile().
    """
    try:
        project_id = _row_get(project_row, "id")

        cached = _row_get(project_row, "weather_profile")
        if cached:
            try:
                return _normalize_profile(json.loads(cached))
            except Exception:
                log.warning(
                    f"weather_profile cache for project {project_id!r} "
                    "is unparsable, reclassifying"
                )

        scope = _row_get(project_row, "scope") or ""
        description = _row_get(project_row, "description") or ""

        try:
            from agents.cron_helpers import ollama_generate
        except Exception as e:
            log.warning(f"ollama_generate unavailable: {e}")
            return default_profile()

        user_prompt = f"Scope: {scope}\nDescription: {description}"
        try:
            raw = ollama_generate(MODEL, CLASSIFY_SYSTEM_PROMPT, user_prompt, max_tokens=200)
        except Exception as e:
            log.warning(f"ollama_generate call failed for project {project_id!r}: {e}")
            return default_profile()

        parsed = _parse_llm_json(raw)
        if parsed is None:
            log.warning(
                f"weather profile classifier returned unparsable output "
                f"for project {project_id!r}: {raw!r}"
            )
            return default_profile()

        try:
            profile = _normalize_profile(parsed)
        except Exception as e:
            log.warning(f"weather profile classifier output malformed for project {project_id!r}: {e}")
            return default_profile()

        if project_id is not None:
            try:
                conn.execute(
                    "UPDATE ahb_projects SET weather_profile = ? WHERE id = ?",
                    (json.dumps(profile), project_id),
                )
                conn.commit()
            except Exception as e:
                log.warning(f"weather_profile cache write failed for project {project_id!r}: {e}")

        return profile
    except Exception as e:
        log.error(f"get_weather_profile failed unexpectedly: {e}")
        return default_profile()
