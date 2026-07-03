#!/usr/bin/env python3
"""
Shared Skill: weather_history
Rain-day ledger report -- weather-delay evidence for client disputes.

SKILL_ARGS: {"project_id": "...", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}

Reads the `weather_observations` ledger in dashboard/baza_projects.db
(create-if-missing, same DDL a parallel task also creates it with, so this
never crashes on a fresh DB) and prints a markdown table of observed
weather for the project across [start, end] plus rain/high-wind/heat
summary counts -- Phil's delay-documentation evidence for client disputes.

Follows the house skill pattern (skills/shared/weather.py): read
SKILL_ARGS from the env, print the result to stdout.
"""
import os
import json
import sqlite3

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB_PATH = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")

# Same DDL the weather_watch cron (parallel task) creates the table with --
# duplicated here on purpose so this skill never crashes against a DB that
# hasn't been touched by that cron yet.
WEATHER_OBSERVATIONS_DDL = """
CREATE TABLE IF NOT EXISTS weather_observations (
    id INTEGER PRIMARY KEY,
    project_id TEXT,
    obs_date TEXT,
    lat REAL,
    lon REAL,
    temp_high_f REAL,
    temp_low_f REAL,
    precip_in REAL,
    wind_max_mph REAL,
    gust_max_mph REAL,
    conditions TEXT,
    source TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, obs_date)
)
"""

RAIN_CONDITION_WORDS = ("rain", "storm", "shower")
RAIN_PRECIP_IN = 0.1
WIND_SUSTAINED_MPH = 20
WIND_GUST_MPH = 35
HEAT_HIGH_F = 90.0


def _resolve_db_path(args: dict) -> str:
    """`_db_path` in SKILL_ARGS (test seam) > BAZA_PROJECTS_DB env (house
    convention, see skills/shared/scaffold_emit_nodes.py) > default path."""
    return args.get("_db_path") or os.environ.get("BAZA_PROJECTS_DB") or DEFAULT_DB_PATH


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute(WEATHER_OBSERVATIONS_DDL)
    conn.commit()
    return conn


def is_rain_day(row) -> bool:
    """precip_in >= 0.1 OR conditions text mentions rain/storm/shower."""
    precip = row["precip_in"]
    if precip is not None and precip >= RAIN_PRECIP_IN:
        return True
    conditions = (row["conditions"] or "").lower()
    return any(w in conditions for w in RAIN_CONDITION_WORDS)


def is_high_wind_day(row) -> bool:
    """wind_max_mph >= 20 OR gust_max_mph >= 35."""
    wind = row["wind_max_mph"] or 0
    gust = row["gust_max_mph"] or 0
    return wind >= WIND_SUSTAINED_MPH or gust >= WIND_GUST_MPH


def is_hot_day(row) -> bool:
    """temp_high_f >= 90."""
    high = row["temp_high_f"]
    return high is not None and high >= HEAT_HIGH_F


def _fmt_f(v) -> str:
    return f"{v:.0f}°F" if v is not None else "-"


def _fmt_in(v) -> str:
    return f"{v:.2f}in" if v is not None else "-"


def _fmt_mph(v) -> str:
    return f"{v:.0f}mph" if v is not None else "-"


def build_history_report(conn, project_id: str, start: str, end: str) -> str:
    """Markdown table of weather_observations rows for `project_id` in
    [start, end] (inclusive, ISO date strings) plus rain/wind/heat counts.
    Returns a plain "no observations" message when the range is empty."""
    rows = conn.execute(
        "SELECT * FROM weather_observations "
        "WHERE project_id = ? AND obs_date >= ? AND obs_date <= ? "
        "ORDER BY obs_date",
        (project_id, start, end),
    ).fetchall()

    if not rows:
        return (
            f"No weather observations recorded for project `{project_id}` "
            f"between {start} and {end}."
        )

    lines = [
        f"### Weather Ledger — {project_id} ({start} to {end})",
        "",
        "| Date | High | Low | Precip | Wind | Gust | Conditions |",
        "|------|------|-----|--------|------|------|------------|",
    ]

    rain_days = wind_days = hot_days = 0
    for r in rows:
        if is_rain_day(r):
            rain_days += 1
        if is_high_wind_day(r):
            wind_days += 1
        if is_hot_day(r):
            hot_days += 1
        lines.append(
            "| {date} | {high} | {low} | {precip} | {wind} | {gust} | {cond} |".format(
                date=r["obs_date"],
                high=_fmt_f(r["temp_high_f"]),
                low=_fmt_f(r["temp_low_f"]),
                precip=_fmt_in(r["precip_in"]),
                wind=_fmt_mph(r["wind_max_mph"]),
                gust=_fmt_mph(r["gust_max_mph"]),
                cond=r["conditions"] or "",
            )
        )

    lines.append("")
    lines.append(
        f"**Summary:** {len(rows)} day(s) observed — {rain_days} rain day(s), "
        f"{wind_days} high-wind day(s), {hot_days} day(s) ≥90°F."
    )
    return "\n".join(lines)


def main():
    args = json.loads(os.environ.get("SKILL_ARGS") or "{}")
    project_id = args.get("project_id", "")
    start = args.get("start", "")
    end = args.get("end", "")

    if not project_id or not start or not end:
        print("Error: project_id, start, and end are required.")
        return

    db_path = _resolve_db_path(args)
    try:
        conn = get_conn(db_path)
        try:
            report = build_history_report(conn, project_id, start, end)
        finally:
            conn.close()
        print(report)
    except Exception as e:
        print(f"Error building weather history: {e}")


if __name__ == "__main__":
    main()
