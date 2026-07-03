#!/usr/bin/env python3
"""
Scout Reeves -- weekly BLS PPI construction-material price snapshot.

Pulls monthly Producer Price Index values for six construction-material
series from the BLS public API (v1, keyless) and stores them in
dashboard/baza_projects.db's material_price_points ledger, one row per
(series_id, period). Then, per series:

  - |latest month vs previous month| > SPIKE_THRESHOLD_PCT -> one deduped
    Telegram alert (agents.cron_helpers.send_alert), alert_key is
    period-keyed (f"matprice:{series_id}:{period}") with
    renotify_hours=999999 -- a given series/period spike fires exactly
    once, ever (re-running the cron, or BLS re-serving the same month's
    figure, never re-alerts).
  - otherwise -> one row (latest value, 1-month Delta%, 12-month Delta%) in a
    combined trend table sent via send_report(priority="fyi",
    delta_key="material_prices") -- suppressed on unchanged content,
    queued instead of sent during quiet hours (both send_report's job,
    not this module's).

A series BLS doesn't resolve (absent from the response, or present with
an empty `data` list -- e.g. a wrong/retired series id) degrades to an
"unavailable" line in the same report. It is never a run failure: BLS
availability is monitoring input, not a precondition for the cron to
succeed. cron_run() records that clean/error status per run in
cron_health.db.

All BLS HTTP access funnels through the single `_post_json` seam so tests
can monkeypatch it and never touch the network (same house pattern as
core/weather_sources.py's `_fetch_json`).

Standalone-executable (`venv/bin/python agents/scout_reeves/crons/material_prices.py`).
`main(now=None)` is the testable entry point and has no import-time side
effects.
"""
import datetime
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *  # noqa: F401,F403 -- get_db, cron_run, send_alert,
# send_report, log, now, today, TELEGRAM_TOKEN (house style for every cron in this repo)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SCOUT-PRICES] %(message)s")

CRON_NAME = "material_prices"
AGENT_TOKEN = os.getenv("TELEGRAM_SCOUT_REEVES", TELEGRAM_TOKEN)

BLS_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
USER_AGENT = "baza-empire/1.0 (contactahbco@gmail.com)"
BLS_TIMEOUT_S = 15  # controller decision -- BLS is occasionally slow; 15s not the global 10s default

SPIKE_THRESHOLD_PCT = 5.0
SPIKE_RENOTIFY_HOURS = 999999  # period-keyed alert_key => effectively "fire once, ever"

# The six construction-material PPI series from the task brief. Labels are
# for display only; BLS's own seriesID is the only thing that matters for
# the request/response and for the material_price_points key.
SERIES = {
    "WPU0811": "Softwood lumber",
    "WPU137": "Gypsum products",
    "WPU1332": "Ready-mix concrete",
    "WPU1017": "Steel mill products",
    "WPU10250105": "Copper wire & cable",
    "WPU057303": "No. 2 diesel fuel",
}

_MONTH_PERIOD_RE = re.compile(r"^M(0[1-9]|1[0-2])$")  # M01-M12 only; excludes M13 (annual average)

MATERIAL_PRICE_POINTS_DDL = """
CREATE TABLE IF NOT EXISTS material_price_points (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id     TEXT NOT NULL,
    series_label  TEXT,
    period        TEXT NOT NULL,
    period_name   TEXT,
    year          TEXT,
    value         REAL,
    fetched_at    TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_material_price_points_series_period
    ON material_price_points(series_id, period);
"""


# ── HTTP seam ────────────────────────────────────────────────────────────

def _post_json(url: str, payload: dict, timeout: int = BLS_TIMEOUT_S) -> dict | None:
    """Single HTTP seam. POST JSON, parse JSON response, return None on any
    failure (network, timeout, non-JSON body). Tests monkeypatch this
    function directly -- never urllib -- to stay off the network."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    except Exception:
        return None


# ── schema ───────────────────────────────────────────────────────────────

def ensure_material_price_points_table(conn):
    """Create-if-missing DDL for the PPI ledger. Idempotent, never raises."""
    try:
        conn.executescript(MATERIAL_PRICE_POINTS_DDL)
        conn.commit()
    except Exception as e:
        log.warning(f"ensure_material_price_points_table failed: {e}")


# ── BLS response parsing ────────────────────────────────────────────────

def _normalize_points(series_result: dict | None) -> list[dict]:
    """BLS `data` rows (any order, may include M13 annual-average rows) ->
    ascending-by-period list of {period, period_name, year, value}. Returns
    [] for a missing series, an empty `data` list, or any row that fails to
    parse -- callers treat an empty result as "series unavailable", not an
    error."""
    if not series_result:
        return []
    raw = series_result.get("data") or []
    out = []
    for d in raw:
        period_code = (d.get("period") or "").strip()
        if not _MONTH_PERIOD_RE.match(period_code):
            continue  # skip M13 (annual average) and any non-monthly period code
        year = (d.get("year") or "").strip()
        if not year:
            continue
        try:
            value = float(d.get("value"))
        except (TypeError, ValueError):
            continue
        out.append({
            "period": f"{year}-{period_code}",
            "period_name": d.get("periodName") or "",
            "year": year,
            "value": value,
        })
    out.sort(key=lambda p: p["period"])
    return out


def _store_points(conn, series_id: str, label: str, points: list[dict]):
    """INSERT OR IGNORE each point -- idempotent re-runs never duplicate a
    (series_id, period) row and never overwrite one BLS has already given
    us (BLS PPI figures can be revised; we keep the first value we saw)."""
    try:
        for p in points:
            conn.execute(
                "INSERT OR IGNORE INTO material_price_points "
                "(series_id, series_label, period, period_name, year, value, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                (series_id, label, p["period"], p["period_name"], p["year"], p["value"]),
            )
        conn.commit()
    except Exception as e:
        log.warning(f"material_price_points insert failed for {series_id!r}: {e}")


# ── period arithmetic ───────────────────────────────────────────────────

def _parse_period(period: str) -> tuple[int, int]:
    y, m = period.split("-M")
    return int(y), int(m)


def _prev_period(period: str) -> str:
    y, m = _parse_period(period)
    if m == 1:
        return f"{y - 1}-M12"
    return f"{y}-M{m - 1:02d}"


def _year_ago_period(period: str) -> str:
    y, m = _parse_period(period)
    return f"{y - 1}-M{m:02d}"


def _pct_change(new, old):
    if old in (None, 0):
        return None
    return (new - old) / old * 100.0


# ── message formatting ──────────────────────────────────────────────────

def _fmt_pct(v):
    return f"{v:+.1f}%" if v is not None else "n/a"


def _send_spike_alert(series_id, label, latest_period, latest_value, prev_period, prev_value, delta_1mo):
    arrow = "\U0001F4C8" if delta_1mo > 0 else "\U0001F4C9"  # up/down chart emoji
    message = (
        f"{arrow} *Material price spike* — {label} ({series_id})\n"
        f"{latest_period}: {latest_value:.2f} vs {prev_period}: {prev_value:.2f} "
        f"({delta_1mo:+.1f}%)"
    )
    send_alert(
        CRON_NAME, message,
        alert_key=f"matprice:{series_id}:{latest_period}",
        renotify_hours=SPIKE_RENOTIFY_HOURS,
        token=AGENT_TOKEN,
    )


def _build_report(when, trend_rows, unavailable):
    lines = [f"\U0001F4CA *Material Prices (BLS PPI)* — {when.strftime('%Y-%m-%d')}"]
    if trend_rows:
        lines.append("")
        lines.append("```")
        lines.append(f"{'Series':<22}{'Value':>9}{'1mo':>8}{'12mo':>8}")
        for r in trend_rows:
            lines.append(
                f"{r['label'][:22]:<22}{r['value']:>9.2f}"
                f"{_fmt_pct(r['delta_1mo']):>8}{_fmt_pct(r['delta_12mo']):>8}"
            )
        lines.append("```")
    if unavailable:
        lines.append("")
        lines.append("⚠️ Unavailable series (verify series id):")
        for series_id, label in unavailable:
            lines.append(f"  - {series_id} ({label})")
    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────

def main(now=None):
    when = now or datetime.datetime.now()
    with cron_run(CRON_NAME):
        _run(when)


def _run(when):
    conn = get_db()
    try:
        ensure_material_price_points_table(conn)

        start_year = str(when.year - 1)
        end_year = str(when.year)
        payload = {"seriesid": list(SERIES.keys()), "startyear": start_year, "endyear": end_year}
        data = _post_json(BLS_URL, payload)

        by_id = {}
        if data:
            for r in ((data.get("Results") or {}).get("series") or []):
                sid = r.get("seriesID")
                if sid:
                    by_id[sid] = r
        else:
            log.warning("material_prices: BLS request failed/returned nothing; all series unavailable this run")

        unavailable = []
        trend_rows = []

        for series_id, label in SERIES.items():
            points = _normalize_points(by_id.get(series_id))
            if not points:
                log.warning(f"material_prices: series {series_id!r} ({label}) has no usable monthly data, marking unavailable")
                unavailable.append((series_id, label))
                continue

            _store_points(conn, series_id, label, points)

            rows = conn.execute(
                "SELECT period, value FROM material_price_points WHERE series_id = ? ORDER BY period ASC",
                (series_id,),
            ).fetchall()
            if not rows:
                continue
            period_values = {row["period"]: row["value"] for row in rows}
            latest_period = max(period_values)  # "YYYY-Mxx" sorts lexicographically == chronologically
            latest_value = period_values[latest_period]
            prev_period = _prev_period(latest_period)
            prev_value = period_values.get(prev_period)
            yearago_period = _year_ago_period(latest_period)
            yearago_value = period_values.get(yearago_period)

            delta_1mo = _pct_change(latest_value, prev_value)
            delta_12mo = _pct_change(latest_value, yearago_value)

            if delta_1mo is not None and abs(delta_1mo) > SPIKE_THRESHOLD_PCT:
                _send_spike_alert(series_id, label, latest_period, latest_value,
                                   prev_period, prev_value, delta_1mo)
            else:
                trend_rows.append({
                    "series_id": series_id, "label": label, "period": latest_period,
                    "value": latest_value, "delta_1mo": delta_1mo, "delta_12mo": delta_12mo,
                })

        if trend_rows or unavailable:
            message = _build_report(when, trend_rows, unavailable)
            send_report(CRON_NAME, message, priority="fyi", delta_key="material_prices", token=AGENT_TOKEN)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
