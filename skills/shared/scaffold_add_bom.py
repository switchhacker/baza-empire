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
        # Enrich an existing row with the same name (case-insensitive) rather
        # than creating a duplicate — lets an agent backfill vendor/url/price
        # onto a pre-seeded BOM row. Only fields the agent provides overwrite.
        existing = con.execute(
            "SELECT id FROM project_bom WHERE project_id=? AND lower(name)=lower(?) "
            "ORDER BY id LIMIT 1", (project_id, name)).fetchone()
        if existing:
            bid = existing[0]
            sets, vals = [], []
            for col in ("part_number", "vendor", "url", "unit_price", "notes",
                        "node_id", "qty", "status"):
                if args.get(col) is not None:
                    v = int(args["qty"] or 1) if col == "qty" else args.get(col)
                    sets.append(f"{col}=?"); vals.append(v)
            if sets:
                sets.append("updated_at=CURRENT_TIMESTAMP"); vals.append(bid)
                con.execute(f"UPDATE project_bom SET {', '.join(sets)} WHERE id=?", vals)
        else:
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
