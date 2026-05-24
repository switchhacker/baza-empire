"""Skill: agent adds a part to the project BOM."""
import json
import os
import sys
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main():
    args = json.loads(os.environ.get("SKILL_ARGS") or "{}")
    db_path = args.get("_db_path") or os.environ.get("BAZA_PROJECTS_DB")
    if not db_path:
        print(json.dumps({"error": "no db path"})); sys.exit(1)
    project_id = args["project_id"]
    name = (args.get("name") or "").strip()
    if not name:
        print(json.dumps({"error": "name required"})); sys.exit(1)

    con = sqlite3.connect(db_path)
    try:
        cur = con.execute("""
            INSERT INTO project_bom
              (project_id, node_id, name, part_number, vendor, url, qty,
               unit_price, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (project_id, args.get("node_id"), name,
              args.get("part_number"), args.get("vendor"), args.get("url"),
              int(args.get("qty") or 1), args.get("unit_price"),
              args.get("status", "researched"), args.get("notes")))
        bid = cur.lastrowid
        con.commit()
    finally:
        con.close()

    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db_path)
    eng.emit_event(project_id, node_id=args.get("node_id"),
                   event_type="bom_added",
                   actor=args.get("actor", "system"),
                   payload={"bom_id": bid, "name": name})
    print(json.dumps({"bom_id": bid}))


if __name__ == "__main__":
    main()
