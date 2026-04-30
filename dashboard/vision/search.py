"""SQL composers for browse + search.

The browse query AND-joins `assets` with one `attributes` row per filter key.
Crops use a special `crops.<col>` namespace (only `crops.part` for v1) which
joins the `crops` table directly.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional


def browse_query(filters: dict, *, page: int = 1, limit: int = 60) -> tuple[str, list]:
    """Return (sql, params) selecting matching assets ordered by id desc."""
    base = ["SELECT a.id, a.abs_path, a.source, a.width, a.height, a.classified_at FROM assets a"]
    where = ["a.status = 'ok'"]
    params: list = []

    attr_n = 0
    for k, v in (filters or {}).items():
        if k == "source":
            where.append("a.source = ?")
            params.append(v)
        elif k == "crops.part":
            base.append("JOIN crops c ON c.asset_id = a.id")
            where.append("c.part = ?")
            params.append(v)
        else:
            attr_n += 1
            alias = f"at{attr_n}"
            base.append(f"JOIN attributes {alias} ON {alias}.asset_id = a.id")
            where.append(f"{alias}.key = ? AND {alias}.value = ?")
            params.extend([k, v])

    sql = " ".join(base) + " WHERE " + " AND ".join(where) + " ORDER BY a.id DESC"
    if limit:
        offset = max(0, (page - 1) * limit)
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return sql, params


def count_for_node(con: sqlite3.Connection, filters: dict) -> int:
    """COUNT(*) of matching ok assets — used by /api/vision/tree."""
    sql, params = browse_query(filters, page=1, limit=0)
    sql = sql.replace(
        "SELECT a.id, a.abs_path, a.source, a.width, a.height, a.classified_at",
        "SELECT COUNT(DISTINCT a.id)",
        1,
    )
    sql = sql.split(" ORDER BY ")[0]
    return con.execute(sql, params).fetchone()[0]


def fts_search(con: sqlite3.Connection, q: str, limit: int = 60) -> list[sqlite3.Row]:
    """FTS5 query on caption/tags/attrs_blob."""
    rows = con.execute(
        """SELECT a.id, a.abs_path, a.source, a.width, a.height, a.classified_at,
                  fts.rank
             FROM assets_fts fts
             JOIN assets a ON a.id = fts.rowid
            WHERE assets_fts MATCH ?
              AND a.status = 'ok'
            ORDER BY fts.rank
            LIMIT ?""",
        (q, int(limit)),
    ).fetchall()
    return list(rows)
