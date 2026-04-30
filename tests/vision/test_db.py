"""Schema bootstrap is idempotent and the expected tables exist."""
import sqlite3

from dashboard.vision.db import init_db


EXPECTED_TABLES = {
    "assets", "attributes", "captions", "crops",
    "seed_demand", "gpu_lease", "ingest_log",
    "assets_fts",
}


def _table_set(con):
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','virtual') "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def test_init_creates_all_tables(tmp_vision_db):
    con = init_db(tmp_vision_db)
    have = _table_set(con)
    missing = EXPECTED_TABLES - have
    assert not missing, f"missing tables: {missing}"


def test_init_is_idempotent(tmp_vision_db):
    init_db(tmp_vision_db).close()
    init_db(tmp_vision_db).close()
    con = sqlite3.connect(tmp_vision_db)
    assert _table_set(con) >= EXPECTED_TABLES


def test_foreign_keys_on(tmp_vision_db):
    con = init_db(tmp_vision_db)
    [(fk,)] = con.execute("PRAGMA foreign_keys").fetchall()
    assert fk == 1
