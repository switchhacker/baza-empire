"""
Baza Empire — Task Updater
Standalone local SQLite task management. Zero external dependencies.
Any agent imports this and calls update_task() / complete_task() / add_task().
"""
import os
import sqlite3
import uuid
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH       = os.path.join(FRAMEWORK_DIR, 'dashboard', 'baza_projects.db')


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Read ───────────────────────────────────────────────────────────────────────

def get_my_tasks(agent_id: str, status: str = None) -> list:
    """Return tasks assigned to this agent, optionally filtered by status."""
    try:
        conn = _conn()
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE assigned_to=? AND status=? ORDER BY priority DESC, due_date",
                (agent_id, status)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE assigned_to=? ORDER BY priority DESC, due_date",
                (agent_id,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[task_updater] get_my_tasks error: {e}")
        return []


def get_task_by_id(task_id: str) -> dict:
    try:
        conn = _conn()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception as e:
        logger.warning(f"[task_updater] get_task_by_id error: {e}")
        return {}


def get_all_tasks(project_id: str = None) -> list:
    try:
        conn = _conn()
        if project_id:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE project_id=? ORDER BY assigned_to, priority DESC",
                (project_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY assigned_to, priority DESC"
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[task_updater] get_all_tasks error: {e}")
        return []


def get_project_stats(project_id: str = None) -> dict:
    tasks = get_all_tasks(project_id)
    total     = len(tasks)
    done      = sum(1 for t in tasks if t['status'] == 'completed')
    in_prog   = sum(1 for t in tasks if t['status'] == 'in_progress')
    blocked   = sum(1 for t in tasks if t['status'] == 'blocked')
    pending   = sum(1 for t in tasks if t['status'] == 'pending')
    progress  = round((done / total) * 100) if total else 0
    return {
        "total": total,
        "completed": done,
        "in_progress": in_prog,
        "blocked": blocked,
        "pending": pending,
        "progress_pct": progress,
    }


# ── Write ──────────────────────────────────────────────────────────────────────

def update_task(task_id: str, fields: dict) -> bool:
    """
    Update any fields on a task.
    fields: dict with any of: status, notes, priority, assigned_to, due_date, title, description
    """
    allowed = {'status', 'notes', 'priority', 'assigned_to', 'due_date', 'title', 'description'}
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered:
        return False
    filtered['updated_at'] = datetime.now().isoformat()
    try:
        conn = _conn()
        set_clause = ', '.join(f"{k}=?" for k in filtered)
        values     = list(filtered.values()) + [task_id]
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id=?", values)
        conn.commit()
        conn.close()
        logger.info(f"[task_updater] Updated task {task_id[:8]}: {filtered}")
        return True
    except Exception as e:
        logger.warning(f"[task_updater] update_task error: {e}")
        return False


def complete_task(task_id: str, notes: str = None) -> bool:
    """Mark a task as completed, optionally add notes."""
    fields = {"status": "completed"}
    if notes:
        fields["notes"] = notes
    return update_task(task_id, fields)


def start_task(task_id: str, notes: str = None) -> bool:
    """Mark a task as in_progress."""
    fields = {"status": "in_progress"}
    if notes:
        fields["notes"] = notes
    return update_task(task_id, fields)


def block_task(task_id: str, reason: str) -> bool:
    """Mark a task as blocked with a reason."""
    return update_task(task_id, {"status": "blocked", "notes": f"BLOCKED: {reason}"})


def add_task(project_id: str, title: str, assigned_to: str,
             description: str = "", priority: str = "medium",
             due_date: str = "", notes: str = "") -> str:
    """Create a new task. Returns the new task ID."""
    task_id = str(uuid.uuid4())
    try:
        conn = _conn()
        conn.execute('''
            INSERT INTO tasks (id, project_id, title, description, assigned_to, status, priority, due_date, notes)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        ''', (task_id, project_id, title, description, assigned_to, priority, due_date, notes))
        conn.commit()
        conn.close()
        logger.info(f"[task_updater] Created task {task_id[:8]}: {title} → {assigned_to}")
        return task_id
    except Exception as e:
        logger.warning(f"[task_updater] add_task error: {e}")
        return ""


# ── Agent convenience wrapper ──────────────────────────────────────────────────

class AgentTaskManager:
    """
    Convenience wrapper for agents.
    Usage:
        from core.task_updater import AgentTaskManager
        tasks = AgentTaskManager("claw_batto")
        tasks.my_pending()          # list my pending tasks
        tasks.complete("task-id")   # mark done
        tasks.start("task-id")      # mark in progress
        tasks.block("task-id", "waiting on Phil")
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id

    def my_tasks(self, status=None):
        return get_my_tasks(self.agent_id, status)

    def my_pending(self):
        return get_my_tasks(self.agent_id, "pending")

    def my_in_progress(self):
        return get_my_tasks(self.agent_id, "in_progress")

    def complete(self, task_id: str, notes: str = None) -> bool:
        return complete_task(task_id, notes)

    def start(self, task_id: str, notes: str = None) -> bool:
        return start_task(task_id, notes)

    def block(self, task_id: str, reason: str) -> bool:
        return block_task(task_id, reason)

    def update(self, task_id: str, **kwargs) -> bool:
        return update_task(task_id, kwargs)

    def add(self, project_id: str, title: str, **kwargs) -> str:
        return add_task(project_id, title, self.agent_id, **kwargs)

    def stats(self, project_id=None) -> dict:
        return get_project_stats(project_id)

    def summary_text(self, project_id=None) -> str:
        """Plain text summary for agent context injection."""
        tasks  = get_my_tasks(self.agent_id)
        stats  = get_project_stats(project_id)
        lines  = [
            f"MY TASKS ({self.agent_id}):",
            f"  Total: {len(tasks)} | Pending: {sum(1 for t in tasks if t['status']=='pending')} | "
            f"In Progress: {sum(1 for t in tasks if t['status']=='in_progress')} | "
            f"Completed: {sum(1 for t in tasks if t['status']=='completed')}",
            "",
        ]
        for t in tasks:
            icon = {"completed": "✅", "in_progress": "🟡", "blocked": "🔴", "pending": "⚪"}.get(t['status'], "⚪")
            lines.append(f"  {icon} [{t['id'][:8]}] {t['title']} (due: {t.get('due_date','?')})")
            if t.get('notes'):
                lines.append(f"       notes: {t['notes'][:80]}")
        lines.append("")
        lines.append(f"PROJECT OVERALL: {stats['progress_pct']}% complete ({stats['completed']}/{stats['total']} tasks done)")
        return "\n".join(lines)
