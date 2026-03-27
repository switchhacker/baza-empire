"""
Skill: update_task
Agents call this to update task status in the local SQLite DB.
Usage in LLM output:
  ##SKILL:update_task:{"task_id": "a1b2c3d4", "status": "completed", "notes": "Done, deployed."}##
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.task_updater import update_task, get_task_by_id, get_project_stats


def run(params: dict) -> dict:
    task_id = params.get("task_id", "").strip()
    status  = params.get("status", "").strip()
    notes   = params.get("notes", "").strip()

    if not task_id:
        return {"success": False, "error": "task_id is required"}

    # Allow short IDs (first 8 chars) — expand to full
    if len(task_id) == 8:
        # Try to find the full ID
        try:
            import sqlite3
            FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            DB_PATH = os.path.join(FRAMEWORK_DIR, 'dashboard', 'baza_projects.db')
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute("SELECT id FROM tasks WHERE id LIKE ?", (task_id + '%',)).fetchone()
            conn.close()
            if row:
                task_id = row[0]
        except:
            pass

    valid_statuses = {"pending", "in_progress", "completed", "blocked"}
    if status and status not in valid_statuses:
        return {"success": False, "error": f"Invalid status '{status}'. Use: {', '.join(valid_statuses)}"}

    fields = {}
    if status:
        fields["status"] = status
    if notes:
        fields["notes"] = notes

    if not fields:
        return {"success": False, "error": "Nothing to update — provide status and/or notes"}

    ok = update_task(task_id, fields)
    if not ok:
        return {"success": False, "error": f"Task {task_id[:8]} not found or update failed"}

    task = get_task_by_id(task_id)
    stats = get_project_stats()

    return {
        "success": True,
        "output": (
            f"Task updated: {task.get('title', task_id[:8])}\n"
            f"Status: {task.get('status')}\n"
            f"Notes: {task.get('notes','')}\n"
            f"Overall progress: {stats['progress_pct']}% ({stats['completed']}/{stats['total']} done)"
        )
    }


if __name__ == "__main__":
    import json
    params = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    result = run(params)
    print(result)
