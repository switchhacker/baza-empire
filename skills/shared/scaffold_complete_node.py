"""Skill: agent reports a node complete (with optional result + artifacts)."""
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main():
    args = json.loads(os.environ.get("SKILL_ARGS") or "{}")
    db_path = args.get("_db_path") or os.environ.get("BAZA_PROJECTS_DB")
    if not db_path:
        print(json.dumps({"error": "no db path"})); sys.exit(1)
    node_id = args["node_id"]
    result = args.get("result", "done")
    artifacts = args.get("artifacts") or []
    decision = args.get("decision")
    reason = args.get("reason", "")

    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db_path)
    node = eng.get_node(node_id)
    if not node:
        print(json.dumps({"error": "node not found"})); sys.exit(1)

    payload = {}
    if node.get("payload_json"):
        try:
            payload = json.loads(node["payload_json"])
        except Exception:
            payload = {}
    payload["result"] = result
    if artifacts:
        payload["artifacts"] = artifacts
    if reason:
        payload["reason"] = reason

    if result == "blocked":
        new_status = "blocked"
    elif decision is not None:
        eng.decide(node_id, chosen_option=decision, reason=reason)
        print(json.dumps({"ok": True, "decided": decision}))
        return
    else:
        new_status = "done"

    eng.update_node(node_id,
                    status=new_status,
                    payload_json=json.dumps(payload, default=str),
                    completed_at=datetime.now(timezone.utc).isoformat())
    eng.emit_event(node["project_id"], node_id=node_id, event_type="completed",
                   actor=node.get("agent_assigned") or "system",
                   payload={"result": result})
    print(json.dumps({"ok": True, "status": new_status}))


if __name__ == "__main__":
    main()
