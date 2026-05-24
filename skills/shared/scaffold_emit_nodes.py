"""Skill: agents add child nodes under a parent during scaffold decomposition."""
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
    project_id = args["project_id"]
    parent_id = args.get("parent_id")
    nodes = args.get("nodes") or []

    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db_path)
    created = []
    for n in nodes:
        nid = eng.create_node(
            project_id,
            node_type=n["node_type"],
            title=n.get("title", ""),
            description=n.get("description", ""),
            parent_id=n.get("parent_id", parent_id),
            weight=n.get("weight"),
            agent=n.get("agent"),
            payload=n.get("payload"),
        )
        created.append(nid)
        for dep in (n.get("depends_on") or []):
            eng.add_edge(project_id, from_node=dep, to_node=nid, edge_type="depends_on")
    print(json.dumps({"created_ids": created}))


if __name__ == "__main__":
    main()
