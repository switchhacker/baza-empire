"""SQLite state for the Network tab: audit log, provider tokens, manual
router facts, migration-wizard state. Separate file (network.db) so the
business DB stays clean; chmod 0600 because tokens live here."""
import json
import os
import sqlite3

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "network.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT (datetime('now','localtime')),
  action TEXT NOT NULL, params TEXT, rc INTEGER, out TEXT, err TEXT);
CREATE TABLE IF NOT EXISTS provider_tokens(
  provider TEXT PRIMARY KEY, token TEXT NOT NULL,
  updated TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS manual_facts(
  key TEXT PRIMARY KEY, value TEXT NOT NULL, note TEXT DEFAULT '',
  updated TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS wizard_state(
  phase TEXT PRIMARY KEY, state TEXT NOT NULL, note TEXT DEFAULT '',
  updated TEXT DEFAULT (datetime('now','localtime')));
"""


def _con(db_path=None):
    p = db_path or DEFAULT_DB
    con = sqlite3.connect(p, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def ensure_tables(db_path=None):
    p = db_path or DEFAULT_DB
    if not os.path.exists(p):
        fd = os.open(p, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    con = _con(p)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    con.close()
    os.chmod(p, 0o600)
    return p


def audit(action, params, rc, out, err, db_path=None):
    con = _con(db_path)
    cur = con.execute(
        "INSERT INTO audit_log(action,params,rc,out,err) VALUES(?,?,?,?,?)",
        (action, json.dumps(params or {}), rc, (out or "")[-2000:], (err or "")[-2000:]))
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def recent_audit(limit=200, db_path=None):
    con = _con(db_path)
    rows = con.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d.get("params") or "{}")
        out.append(d)
    return out


def get_token(provider, db_path=None):
    con = _con(db_path)
    row = con.execute("SELECT token FROM provider_tokens WHERE provider=?",
                      (provider,)).fetchone()
    con.close()
    return row["token"] if row else None


def set_token(provider, token, db_path=None):
    con = _con(db_path)
    con.execute("INSERT INTO provider_tokens(provider,token) VALUES(?,?) "
                "ON CONFLICT(provider) DO UPDATE SET token=excluded.token, "
                "updated=datetime('now','localtime')", (provider, token))
    con.commit(); con.close()


def facts_list(db_path=None):
    con = _con(db_path)
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM manual_facts ORDER BY key").fetchall()]
    con.close()
    return rows


def fact_set(key, value, note="", db_path=None):
    con = _con(db_path)
    con.execute("INSERT INTO manual_facts(key,value,note) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "note=excluded.note, updated=datetime('now','localtime')",
                (key, value, note))
    con.commit(); con.close()


def fact_delete(key, db_path=None):
    con = _con(db_path)
    con.execute("DELETE FROM manual_facts WHERE key=?", (key,))
    con.commit(); con.close()


def wizard_get(db_path=None):
    con = _con(db_path)
    rows = con.execute("SELECT * FROM wizard_state").fetchall()
    con.close()
    return {r["phase"]: {"state": r["state"], "note": r["note"], "updated": r["updated"]}
            for r in rows}


def wizard_set(phase, state, note="", db_path=None):
    con = _con(db_path)
    con.execute("INSERT INTO wizard_state(phase,state,note) VALUES(?,?,?) "
                "ON CONFLICT(phase) DO UPDATE SET state=excluded.state, "
                "note=excluded.note, updated=datetime('now','localtime')",
                (phase, state, note))
    con.commit(); con.close()
