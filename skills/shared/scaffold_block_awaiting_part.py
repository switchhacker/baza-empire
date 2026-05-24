"""Skill: mark a node blocked because a needed part hasn't arrived."""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main():
    args = json.loads(os.environ.get("SKILL_ARGS") or "{}")
    db_path = args.get("_db_path") or os.environ.get("BAZA_PROJECTS_DB")
    if not db_path:
        print(json.dumps({"error": "no db path"})); sys.exit(1)
    node_id = args["node_id"]
    bom_id = args.get("bom_id")

    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db_path)
    node = eng.get_node(node_id)
    if not node:
        print(json.dumps({"error": "node not found"})); sys.exit(1)

    eng.update_node(node_id, status="awaiting_part")
    eng.emit_event(node["project_id"], node_id=node_id,
                   event_type="awaiting_part", actor="system",
                   payload={"bom_id": bom_id})
    print(json.dumps({"ok": True}))


if __name__ == "__main__":
    main()
