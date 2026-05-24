import sqlite3
import tempfile
import os
from pathlib import Path


def _migrate(db_path):
    """Helper that runs the migration against a fresh DB."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dashboard.app import _ensure_scaffold_tables
    con = sqlite3.connect(db_path)
    _ensure_scaffold_tables(con)
    con.commit()
    return con


def test_scaffold_tables_created():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        con = _migrate(path)
        cur = con.cursor()
        names = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "project_scaffold_nodes" in names
        assert "project_scaffold_edges" in names
        assert "project_scaffold_events" in names
        assert "project_bom" in names
        assert "baza_inventory" in names
        assert "baza_equipment" in names

        # Critical indices must exist (hot-path queries depend on them)
        indices = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_scaffold_nodes_pid" in indices
        assert "idx_scaffold_edges_from" in indices
        assert "idx_scaffold_edges_to" in indices
        con.close()
    finally:
        os.unlink(path)


def test_scaffold_migration_idempotent():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        con1 = _migrate(path); con1.close()
        # Second run must not raise
        con2 = _migrate(path); con2.close()
    finally:
        os.unlink(path)


def test_scaffold_paused_column_added_to_projects():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT)")
        con.commit()
        con.close()
        con = _migrate(path)
        cols = {r[1] for r in con.execute("PRAGMA table_info(projects)")}
        assert "scaffold_paused" in cols
        con.close()
    finally:
        os.unlink(path)
