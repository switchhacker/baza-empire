#!/usr/bin/env python3
"""Standalone hardware baseline / verify — works even when the dashboard is down.

This is the resilient half of the Hardware & Upgrades feature: after a hardware
upgrade you can prove the box came back healthy from a plain terminal (or over
`ssh phantom`) without depending on the very dashboard that might have failed to
start.

Usage:
    hw_verify.py --snapshot [--label LABEL]   # capture a known-good baseline
    hw_verify.py                              # verify current state vs latest baseline
    hw_verify.py --json                       # machine-readable verify output

Reads/writes the same hw_baselines / hw_verify_runs tables in baza_projects.db
that the dashboard uses, so a CLI snapshot is visible in the UI and vice-versa.
"""
import argparse
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD = os.path.join(os.path.dirname(_HERE), "dashboard")
if _DASHBOARD not in sys.path:
    sys.path.insert(0, _DASHBOARD)

import hardware_probe as hp  # noqa: E402

DB = os.environ.get("BAZA_PROJECTS_DB", os.path.join(_DASHBOARD, "baza_projects.db"))

# ANSI colors (suppressed if not a tty)
_TTY = sys.stdout.isatty()
def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s
GREEN = lambda s: _c("32", s)
RED = lambda s: _c("31;1", s)
YELLOW = lambda s: _c("33", s)
DIM = lambda s: _c("2", s)
BOLD = lambda s: _c("1", s)


def _con():
    con = sqlite3.connect(DB, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _ensure_tables(con):
    """Create tables if the dashboard hasn't yet (CLI may run pre-dashboard)."""
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS hw_baselines (
            id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER, label TEXT,
            captured_at TEXT DEFAULT (datetime('now')), snapshot_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS hw_verify_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, baseline_id INTEGER,
            ran_at TEXT DEFAULT (datetime('now')), passed INTEGER,
            regressions INTEGER, result_json TEXT NOT NULL);
        """
    )
    con.commit()


def do_snapshot(label):
    snap = hp.probe_system()
    con = _con()
    try:
        _ensure_tables(con)
        cur = con.execute(
            "INSERT INTO hw_baselines (label, captured_at, snapshot_json) VALUES (?,?,?)",
            (label, snap["captured_at"], json.dumps(snap)))
        con.commit()
        bid = cur.lastrowid
    finally:
        con.close()
    s = hp.summarize(snap)
    print(BOLD(f"Baseline #{bid} captured at {snap['captured_at']} (label: {label})"))
    for dom, c in s.items():
        print(f"  {dom:12} ok={c['ok']:2} fail={c['fail']:2} idle={c['idle']:2} warn={c['warn']:2}")
    print(DIM("Stored in baza_projects.db → hw_baselines. Verify after reboot with: hw_verify.py"))
    return 0


def do_verify(as_json):
    con = _con()
    try:
        _ensure_tables(con)
        row = con.execute("SELECT * FROM hw_baselines ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            msg = "No baseline captured yet. Run: hw_verify.py --snapshot"
            print(json.dumps({"error": msg}) if as_json else RED(msg))
            return 2
        baseline = json.loads(row["snapshot_json"])
        current = hp.probe_system()
        diff = hp.diff_snapshots(baseline, current)
        con.execute(
            "INSERT INTO hw_verify_runs (baseline_id, passed, regressions, result_json) "
            "VALUES (?,?,?,?)",
            (row["id"], 1 if diff["pass"] else 0, len(diff["regressions"]),
             json.dumps({"diff": diff})))
        con.commit()
    finally:
        con.close()

    if as_json:
        print(json.dumps(diff, indent=2))
        return 0 if diff["pass"] else 1

    print(BOLD(f"Verify vs baseline #{row['id']} ({row['label']}, {row['captured_at']})"))
    if diff["pass"]:
        print(GREEN("  ✔ PASS — no regressions. Everything that was healthy is back."))
    else:
        print(RED(f"  ✘ FAIL — {len(diff['regressions'])} regression(s):"))
        for r in diff["regressions"]:
            print(RED(f"      ✗ [{r['domain']}] {r['name']}: was {r['was']} → now {r['now']} "
                      f"{DIM(r.get('detail',''))}"))
    if diff["recovered"]:
        for r in diff["recovered"]:
            print(GREEN(f"      ↑ recovered [{r['domain']}] {r['name']}"))
    if diff["changes"]:
        print(BOLD("  Firmware/info changes (expected on an upgrade):"))
        for c in diff["changes"]:
            print(YELLOW(f"      Δ {c['name']}: {c['was_detail']} → {c['now_detail']}"))
    print(DIM("  Domain health now:"))
    for dom, c in diff["summary"].items():
        line = f"      {dom:12} ok={c['ok']:2} fail={c['fail']:2} idle={c['idle']:2}"
        print(GREEN(line) if c["fail"] == 0 else YELLOW(line))
    return 0 if diff["pass"] else 1


def main():
    ap = argparse.ArgumentParser(description="Hardware baseline / verify (dashboard-independent)")
    ap.add_argument("--snapshot", action="store_true", help="capture a baseline instead of verifying")
    ap.add_argument("--label", default="cli", help="label for the snapshot")
    ap.add_argument("--json", action="store_true", help="machine-readable verify output")
    args = ap.parse_args()
    if args.snapshot:
        return do_snapshot(args.label)
    return do_verify(args.json)


if __name__ == "__main__":
    sys.exit(main())
