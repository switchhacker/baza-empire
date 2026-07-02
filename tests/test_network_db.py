import os, sys, stat, sqlite3

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))

import network_db


def test_roundtrip(tmp_path):
    db = str(tmp_path / "network.db")
    network_db.ensure_tables(db)
    # 0600 perms
    assert stat.S_IMODE(os.stat(db).st_mode) == 0o600

    # WAL mode is active
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con.close()

    rid = network_db.audit("svc_restart", {"unit": "caddy"}, 0, "ok", "", db_path=db)
    assert rid == 1
    rows = network_db.recent_audit(db_path=db)
    assert rows[0]["action"] == "svc_restart" and rows[0]["params"]["unit"] == "caddy"

    assert network_db.get_token("desec", db_path=db) is None
    network_db.set_token("desec", "tok123", db_path=db)
    assert network_db.get_token("desec", db_path=db) == "tok123"
    network_db.set_token("desec", "tok456", db_path=db)  # upsert
    assert network_db.get_token("desec", db_path=db) == "tok456"

    network_db.fact_set("router.reservation", "enp6s0 -> 192.168.1.68", note="G3100 DHCP", db_path=db)
    assert network_db.facts_list(db_path=db)[0]["key"] == "router.reservation"
    network_db.fact_delete("router.reservation", db_path=db)
    assert network_db.facts_list(db_path=db) == []

    network_db.wizard_set("phase1", "done", note="zone added", db_path=db)
    assert network_db.wizard_get(db_path=db)["phase1"]["state"] == "done"
