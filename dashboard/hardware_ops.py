"""Flask blueprint for the Hardware & Upgrades feature (Settings tab).

Full upgrade lifecycle, all surfaced under /settings/hardware:

  Research/Plan → Baseline snapshot → Shutdown runbook → (reboot) → Verify diff

The heavy lifting (probing the live system, diffing snapshots) lives in
`hardware_probe`; this module is the HTTP + persistence layer over it. Tables
live in baza_projects.db alongside the rest of the dashboard data.
"""
import json
import os
import sqlite3
import subprocess
import urllib.request

from flask import Blueprint, jsonify, render_template, request

try:
    from dashboard import hardware_probe as hp
except ImportError:
    import hardware_probe as hp

hardware_bp = Blueprint("hardware_ops", __name__)

RESEARCH_MODEL = os.environ.get("BAZA_HW_RESEARCH_MODEL", "gemma4:12b-it-qat")
RESEARCH_OLLAMA = os.environ.get("BAZA_HW_RESEARCH_OLLAMA", "http://127.0.0.1:11434")
BIOS_STAGING = os.path.expanduser("~/bios-staging")


def _db_path():
    return os.environ.get(
        "BAZA_PROJECTS_DB",
        os.path.join(os.path.dirname(__file__), "baza_projects.db"),
    )


def _con():
    con = sqlite3.connect(_db_path(), timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


# ───────────────────────────── schema ──────────────────────────────

def _ensure_hardware_tables(db_path=None):
    con = sqlite3.connect(db_path or _db_path(), timeout=5)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS hw_upgrade_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                status TEXT DEFAULT 'planning',
                goal TEXT,
                bios_req TEXT,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS hw_components (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER,
                category TEXT,
                name TEXT NOT NULL,
                specs TEXT,
                socket TEXT,
                tdp TEXT,
                est_cost REAL,
                vendor TEXT,
                url TEXT,
                compat_notes TEXT,
                source TEXT DEFAULT 'manual',
                status TEXT DEFAULT 'candidate',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS hw_baselines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER,
                label TEXT,
                captured_at TEXT DEFAULT (datetime('now')),
                snapshot_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hw_verify_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                baseline_id INTEGER,
                ran_at TEXT DEFAULT (datetime('now')),
                passed INTEGER,
                regressions INTEGER,
                result_json TEXT NOT NULL
            );
            """
        )
        con.commit()
    finally:
        con.close()


# ───────────────────────────── plans CRUD ──────────────────────────

PLAN_FIELDS = ("title", "status", "goal", "bios_req", "notes")


@hardware_bp.route("/api/baza/hw/plans", methods=["GET", "POST"])
def plans():
    con = _con()
    try:
        if request.method == "POST":
            b = request.get_json(silent=True) or {}
            title = (b.get("title") or "").strip()
            if not title:
                return jsonify({"error": "title required"}), 400
            cur = con.execute(
                "INSERT INTO hw_upgrade_plans (title, status, goal, bios_req, notes) "
                "VALUES (?,?,?,?,?)",
                (title, b.get("status") or "planning", b.get("goal"),
                 b.get("bios_req"), b.get("notes")),
            )
            con.commit()
            return jsonify({"id": cur.lastrowid}), 201
        rows = con.execute(
            "SELECT * FROM hw_upgrade_plans ORDER BY "
            "CASE status WHEN 'in_progress' THEN 0 WHEN 'ready' THEN 1 "
            "WHEN 'planning' THEN 2 ELSE 3 END, updated_at DESC"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        con.close()


@hardware_bp.route("/api/baza/hw/plans/<int:pid>", methods=["PATCH", "DELETE"])
def plan_detail(pid):
    con = _con()
    try:
        if not con.execute("SELECT 1 FROM hw_upgrade_plans WHERE id=?", (pid,)).fetchone():
            return jsonify({"error": "not found"}), 404
        if request.method == "DELETE":
            con.execute("DELETE FROM hw_components WHERE plan_id=?", (pid,))
            con.execute("DELETE FROM hw_upgrade_plans WHERE id=?", (pid,))
            con.commit()
            return jsonify({"ok": True})
        b = request.get_json(silent=True) or {}
        sets, vals = [], []
        for f in PLAN_FIELDS:
            if f in b:
                sets.append(f"{f}=?")
                vals.append(b[f])
        if sets:
            sets.append("updated_at=datetime('now')")
            vals.append(pid)
            con.execute(f"UPDATE hw_upgrade_plans SET {','.join(sets)} WHERE id=?", vals)
            con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


# ─────────────────────────── components CRUD ───────────────────────

COMPONENT_FIELDS = ("category", "name", "specs", "socket", "tdp", "est_cost",
                    "vendor", "url", "compat_notes", "source", "status")


@hardware_bp.route("/api/baza/hw/plans/<int:pid>/components", methods=["GET", "POST"])
def components(pid):
    con = _con()
    try:
        if not con.execute("SELECT 1 FROM hw_upgrade_plans WHERE id=?", (pid,)).fetchone():
            return jsonify({"error": "plan not found"}), 404
        if request.method == "POST":
            b = request.get_json(silent=True) or {}
            name = (b.get("name") or "").strip()
            if not name:
                return jsonify({"error": "name required"}), 400
            cols = ["plan_id"] + [f for f in COMPONENT_FIELDS if f in b]
            vals = [pid] + [b[f] for f in COMPONENT_FIELDS if f in b]
            ph = ",".join(["?"] * len(cols))
            cur = con.execute(
                f"INSERT INTO hw_components ({','.join(cols)}) VALUES ({ph})", vals)
            con.commit()
            return jsonify({"id": cur.lastrowid}), 201
        rows = con.execute(
            "SELECT * FROM hw_components WHERE plan_id=? ORDER BY category, id", (pid,)
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        con.close()


@hardware_bp.route("/api/baza/hw/components/<int:cid>", methods=["PATCH", "DELETE"])
def component_detail(cid):
    con = _con()
    try:
        if not con.execute("SELECT 1 FROM hw_components WHERE id=?", (cid,)).fetchone():
            return jsonify({"error": "not found"}), 404
        if request.method == "DELETE":
            con.execute("DELETE FROM hw_components WHERE id=?", (cid,))
            con.commit()
            return jsonify({"ok": True})
        b = request.get_json(silent=True) or {}
        sets, vals = [], []
        for f in COMPONENT_FIELDS:
            if f in b:
                sets.append(f"{f}=?")
                vals.append(b[f])
        if sets:
            vals.append(cid)
            con.execute(f"UPDATE hw_components SET {','.join(sets)} WHERE id=?", vals)
            con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


# ─────────────────────────── baseline / verify ─────────────────────

@hardware_bp.route("/api/baza/hw/baseline", methods=["POST"])
def capture_baseline():
    b = request.get_json(silent=True) or {}
    snap = hp.probe_system()
    con = _con()
    try:
        cur = con.execute(
            "INSERT INTO hw_baselines (plan_id, label, captured_at, snapshot_json) "
            "VALUES (?,?,?,?)",
            (b.get("plan_id"), (b.get("label") or "manual").strip(),
             snap["captured_at"], json.dumps(snap)),
        )
        con.commit()
        return jsonify({"id": cur.lastrowid, "captured_at": snap["captured_at"],
                        "summary": hp.summarize(snap)}), 201
    finally:
        con.close()


def _latest_baseline(con, plan_id=None):
    if plan_id:
        row = con.execute(
            "SELECT * FROM hw_baselines WHERE plan_id=? ORDER BY id DESC LIMIT 1",
            (plan_id,)).fetchone()
        if row:
            return row
    return con.execute("SELECT * FROM hw_baselines ORDER BY id DESC LIMIT 1").fetchone()


@hardware_bp.route("/api/baza/hw/baseline/latest", methods=["GET"])
def baseline_latest():
    con = _con()
    try:
        row = _latest_baseline(con, request.args.get("plan_id"))
        if not row:
            return jsonify({"baseline": None})
        snap = json.loads(row["snapshot_json"])
        return jsonify({"id": row["id"], "label": row["label"],
                        "captured_at": row["captured_at"],
                        "summary": hp.summarize(snap), "snapshot": snap})
    finally:
        con.close()


@hardware_bp.route("/api/baza/hw/verify", methods=["POST"])
def verify():
    b = request.get_json(silent=True) or {}
    con = _con()
    try:
        base_row = _latest_baseline(con, b.get("plan_id"))
        if not base_row:
            return jsonify({"error": "no baseline captured yet — snapshot first"}), 400
        baseline = json.loads(base_row["snapshot_json"])
        current = hp.probe_system()
        diff = hp.diff_snapshots(baseline, current)
        cur = con.execute(
            "INSERT INTO hw_verify_runs (baseline_id, passed, regressions, result_json) "
            "VALUES (?,?,?,?)",
            (base_row["id"], 1 if diff["pass"] else 0,
             len(diff["regressions"]), json.dumps({"diff": diff, "current": current})),
        )
        con.commit()
        return jsonify({"id": cur.lastrowid, "baseline_id": base_row["id"],
                        "baseline_label": base_row["label"], "diff": diff}), 201
    finally:
        con.close()


@hardware_bp.route("/api/baza/hw/verify/latest", methods=["GET"])
def verify_latest():
    con = _con()
    try:
        row = con.execute(
            "SELECT * FROM hw_verify_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return jsonify({"verify": None})
        payload = json.loads(row["result_json"])
        return jsonify({"id": row["id"], "ran_at": row["ran_at"],
                        "passed": bool(row["passed"]),
                        "baseline_id": row["baseline_id"], "diff": payload.get("diff")})
    finally:
        con.close()


# ─────────────────────────── shutdown runbook ──────────────────────

def _baza_units_by_phase():
    """Group currently-loaded baza units into ordered shutdown phases."""
    rc, out, _ = hp._run(["systemctl", "list-units", "baza-*", "--type=service",
                          "--all", "--plain", "--no-legend", "--no-pager"])
    units = [u["unit"] for u in hp.parse_systemctl_units(out)]
    agents = sorted(u for u in units if u.startswith("baza-agent-"))
    app_svcs = [u for u in ("baza-dashboard.service", "baza-tool-server.service",
                            "baza-litellm.service", "baza-terminal-bot.service")
                if u in units]
    sd = [u for u in ("baza-sd-webui.service",) if u in units]
    ollama = ["ollama.service", "ollama-cuda.service", "ollama-cpu.service",
              "ollama-amd.service", "ollama-dual.service"]
    return agents, app_svcs, sd, ollama


@hardware_bp.route("/api/baza/hw/runbook/<int:pid>", methods=["GET"])
@hardware_bp.route("/api/baza/hw/runbook", methods=["GET"])
def runbook(pid=None):
    agents, app_svcs, sd, ollama = _baza_units_by_phase()
    bios_note = ""
    if os.path.isdir(BIOS_STAGING):
        bios_note = f"BIOS staging found at {BIOS_STAGING} — run its prep before flashing."
    steps = [
        {"n": 1, "title": "Snapshot a fresh baseline",
         "why": "Capture the exact known-good state right before you power off.",
         "cmds": ["# click 'Snapshot now' in this panel, or:",
                  "cd ~/baza-empire/agent-framework-v3/dashboard && "
                  "../venv/bin/python ../scripts/hw_verify.py --snapshot"]},
        {"n": 2, "title": "Pause timers",
         "why": "Stop the 5-min watchdog and other timers from restarting things mid-shutdown.",
         "cmds": ["sudo systemctl stop 'baza-*.timer'",
                  "systemctl --user stop claw-auto-git.timer"]},
        {"n": 3, "title": "Stop agents",
         "why": "Quiesce the 8 local agents before the platform they talk to goes away.",
         "cmds": [f"sudo systemctl stop {' '.join(agents)}" if agents
                  else "# (no agent units loaded)"]},
        {"n": 4, "title": "Stop app services",
         "why": "Dashboard, tool-server, litellm, terminal bot. NOTE: this stops THIS UI — "
                "finish the remaining steps from a terminal.",
         "cmds": [f"sudo systemctl stop {' '.join(app_svcs)}" if app_svcs
                  else "# (none loaded)"]},
        {"n": 5, "title": "Stop SD WebUI + claw review",
         "why": "Release the NVIDIA 3070 and the background reviewer.",
         "cmds": ([f"sudo systemctl stop {' '.join(sd)}"] if sd else [])
                 + ["systemctl --user stop baza-claw-review.service baza-claw-fs-watcher.service"]},
        {"n": 6, "title": "Stop Ollama (×5)",
         "why": "Last app layer holding the GPUs.",
         "cmds": [f"sudo systemctl stop {' '.join(ollama)}"]},
        {"n": 7, "title": "Confirm quiesced, then power off",
         "why": "ZFS is left to systemd (do NOT manually export). Verify nothing baza-* is "
                "still active, then power down for the hardware swap.",
         "cmds": ["systemctl list-units 'baza-*' --state=active --no-pager",
                  "sync",
                  "sudo systemctl poweroff"]},
    ]
    boot = {
        "title": "After the box is back",
        "why": "Enabled units come up on their own. Prove it — run verify and read the diff. "
               "If the dashboard itself didn't come back, run the CLI from a terminal or "
               "over `ssh phantom`.",
        "cmds": ["cd ~/baza-empire/agent-framework-v3/dashboard && "
                 "../venv/bin/python ../scripts/hw_verify.py",
                 "# or: click 'Verify now' in this panel once the dashboard is up"],
    }
    return jsonify({"plan_id": pid, "bios_note": bios_note, "steps": steps, "boot": boot})


# ─────────────────────────── agent research (local) ────────────────

@hardware_bp.route("/api/baza/hw/research", methods=["POST"])
def research():
    """Opt-in, on-demand draft of a component spec from a LOCAL Ollama model.

    Honors the local-first rule: this never hits the open web. It asks a local
    model to structure what it knows into our component fields; the user reviews
    and edits before saving. For live web research, invoke deep-research manually.
    """
    b = request.get_json(silent=True) or {}
    query = (b.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400
    category = (b.get("category") or "").strip()
    prompt = (
        "You are a hardware-upgrade research assistant for a Linux workstation "
        "(currently AMD Ryzen 7 5700G, ASUS X570-E board). Draft a concise component "
        "record as STRICT JSON with keys: name, category, specs, socket, tdp, "
        "est_cost (USD number), vendor, compat_notes. No prose, JSON only.\n\n"
        f"Category hint: {category or 'unknown'}\nRequest: {query}\n"
        "Note any socket/BIOS compatibility risk for an X570 board in compat_notes."
    )
    try:
        req = urllib.request.Request(
            f"{RESEARCH_OLLAMA}/api/generate",
            data=json.dumps({"model": RESEARCH_MODEL, "prompt": prompt,
                             "stream": False, "format": "json"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = json.loads(r.read().decode())
        text = raw.get("response", "").strip()
        draft = json.loads(text) if text else {}
        draft["source"] = "agent (local)"
        return jsonify({"draft": draft, "model": RESEARCH_MODEL})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"local research failed: {e}",
                        "model": RESEARCH_MODEL}), 502


# ─────────────────────────── page ──────────────────────────────────

@hardware_bp.route("/settings/hardware", methods=["GET"])
def hardware_page():
    return render_template("hardware.html", nav_active="settings")
