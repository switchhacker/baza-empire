#!/usr/bin/env python3
"""
Baza Empire — AHB123 Business Data Query Skill
All agents use this to read/write AHBCO LLC business data.

SKILL_ARGS:
  action: list_clients | get_client | add_client | update_client |
          list_projects | get_project | add_project | update_project |
          list_invoices | add_invoice | list_receipts | add_receipt |
          list_payroll | add_payroll | list_estimates | add_estimate |
          list_employees | add_employee | update_employee |
          list_events | add_event | update_event |
          list_notes | add_note | update_note |
          list_debts | add_debt | update_debt |
          list_files | add_file |
          list_tax | add_tax | update_tax |
          list_phases | add_phase |
          dashboard_stats | search
  filters: {status, client_id, project_id, q, type, category, ...}  (optional)
  data: {...}  (for create/update actions)
"""
import os
import sys
import json
import sqlite3
import uuid
from datetime import datetime

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
action = args.get("action", "")
filters = args.get("filters", {})
data = args.get("data", {})


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql, params=()):
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def execute(sql, params=()):
    conn = get_db()
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def new_id():
    return uuid.uuid4().hex[:24]


# ── Actions ──────────────────────────────────────────────────────────────────

def list_clients():
    sql = "SELECT * FROM ahb_clients"
    params = []
    if filters.get("status"):
        sql += " WHERE status = ?"
        params.append(filters["status"])
    sql += " ORDER BY updated_at DESC"
    return query(sql, params)


def get_client():
    cid = filters.get("id") or data.get("id", "")
    rows = query("SELECT * FROM ahb_clients WHERE id = ?", (cid,))
    return rows[0] if rows else {"error": "Client not found"}


def add_client():
    cid = new_id()
    execute(
        """INSERT INTO ahb_clients (id, name, phone, email, address, city, source, status, notes, assigned_agent)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cid, data.get("name", ""), data.get("phone", ""), data.get("email", ""),
         data.get("address", ""), data.get("city", "Philadelphia"), data.get("source", ""),
         data.get("status", "lead"), data.get("notes", ""), data.get("assigned_agent", ""))
    )
    return {"success": True, "id": cid}


def update_client():
    cid = data.get("id") or filters.get("id", "")
    fields = []
    vals = []
    for key in ["name", "phone", "email", "address", "city", "source", "status", "notes", "assigned_agent"]:
        if key in data:
            fields.append(f"{key} = ?")
            vals.append(data[key])
    if not fields:
        return {"error": "No fields to update"}
    fields.append("updated_at = ?")
    vals.append(datetime.now().isoformat())
    vals.append(cid)
    execute(f"UPDATE ahb_clients SET {', '.join(fields)} WHERE id = ?", vals)
    return {"success": True}


def list_projects():
    sql = "SELECT * FROM ahb_projects"
    conditions = []
    params = []
    if filters.get("status"):
        conditions.append("status = ?")
        params.append(filters["status"])
    if filters.get("client_id"):
        conditions.append("client_id = ?")
        params.append(filters["client_id"])
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY updated_at DESC"
    return query(sql, params)


def get_project():
    pid = filters.get("id") or data.get("id", "")
    rows = query("SELECT * FROM ahb_projects WHERE id = ?", (pid,))
    return rows[0] if rows else {"error": "Project not found"}


def add_project():
    pid = new_id()
    execute(
        """INSERT INTO ahb_projects (id, client_id, title, address, scope, description,
           budget_low, budget_high, status, start_date, end_date, assigned_agents, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (pid, data.get("client_id", ""), data.get("title", ""), data.get("address", ""),
         data.get("scope", ""), data.get("description", ""),
         data.get("budget_low", 0), data.get("budget_high", 0),
         data.get("status", "estimate"), data.get("start_date", ""), data.get("end_date", ""),
         data.get("assigned_agents", ""), data.get("notes", ""))
    )
    return {"success": True, "id": pid}


def update_project():
    pid = data.get("id") or filters.get("id", "")
    fields = []
    vals = []
    for key in ["client_id", "title", "address", "scope", "description", "budget_low",
                 "budget_high", "status", "start_date", "end_date", "assigned_agents", "notes"]:
        if key in data:
            fields.append(f"{key} = ?")
            vals.append(data[key])
    if not fields:
        return {"error": "No fields to update"}
    fields.append("updated_at = ?")
    vals.append(datetime.now().isoformat())
    vals.append(pid)
    execute(f"UPDATE ahb_projects SET {', '.join(fields)} WHERE id = ?", vals)
    return {"success": True}


def list_invoices():
    sql = "SELECT * FROM ahb_invoices"
    conditions = []
    params = []
    if filters.get("status"):
        conditions.append("status = ?")
        params.append(filters["status"])
    if filters.get("client_id"):
        conditions.append("client_id = ?")
        params.append(filters["client_id"])
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY created_at DESC"
    return query(sql, params)


def add_invoice():
    iid = new_id()
    inv_num = f"AHB-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:3].upper()}"
    line_items = json.dumps(data.get("line_items", []))
    execute(
        """INSERT INTO ahb_invoices (id, client_id, project_id, invoice_number, line_items,
           subtotal, tax, total, status, due_date, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (iid, data.get("client_id", ""), data.get("project_id", ""), inv_num, line_items,
         data.get("subtotal", 0), data.get("tax", 0), data.get("total", 0),
         data.get("status", "draft"), data.get("due_date", ""), data.get("notes", ""))
    )
    return {"success": True, "id": iid, "invoice_number": inv_num}


def list_receipts():
    sql = "SELECT * FROM ahb_receipts"
    conditions = []
    params = []
    if filters.get("project_id"):
        conditions.append("project_id = ?")
        params.append(filters["project_id"])
    if filters.get("category"):
        conditions.append("category = ?")
        params.append(filters["category"])
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY created_at DESC"
    return query(sql, params)


def add_receipt():
    rid = new_id()
    execute(
        """INSERT INTO ahb_receipts (id, project_id, vendor, amount, category, description,
           receipt_date, file_path, ocr_text, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rid, data.get("project_id", ""), data.get("vendor", ""), data.get("amount", 0),
         data.get("category", ""), data.get("description", ""), data.get("receipt_date", ""),
         data.get("file_path", ""), data.get("ocr_text", ""),
         os.environ.get("AGENT_ID", "manual"))
    )
    return {"success": True, "id": rid}


def list_payroll():
    sql = "SELECT * FROM ahb_payroll"
    conditions = []
    params = []
    if filters.get("status"):
        conditions.append("status = ?")
        params.append(filters["status"])
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY created_at DESC"
    return query(sql, params)


def add_payroll():
    pid = new_id()
    hours = float(data.get("hours", 0))
    rate = float(data.get("rate", 0))
    total = hours * rate
    execute(
        """INSERT INTO ahb_payroll (id, worker_name, role, hours, rate, total,
           period_start, period_end, status, project_id, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (pid, data.get("worker_name", ""), data.get("role", ""),
         hours, rate, total,
         data.get("period_start", ""), data.get("period_end", ""),
         data.get("status", "pending"), data.get("project_id", ""), data.get("notes", ""))
    )
    return {"success": True, "id": pid, "total": total}


def list_estimates():
    return query("SELECT * FROM ahb_estimates ORDER BY created_at DESC")


def add_estimate():
    eid = new_id()
    line_items = json.dumps(data.get("line_items", []))
    execute(
        """INSERT INTO ahb_estimates (id, client_id, project_id, title, description, scope,
           line_items, subtotal, markup_pct, total, status, generated_by, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (eid, data.get("client_id", ""), data.get("project_id", ""), data.get("title", ""),
         data.get("description", ""), data.get("scope", ""), line_items,
         data.get("subtotal", 0), data.get("markup_pct", 15), data.get("total", 0),
         data.get("status", "draft"), os.environ.get("AGENT_ID", ""),
         data.get("notes", ""))
    )
    return {"success": True, "id": eid}


def list_employees():
    sql = "SELECT * FROM ahb_employees"
    params = []
    if filters.get("active") is not None:
        sql += " WHERE active = ?"
        params.append(int(filters["active"]))
    return query(sql + " ORDER BY name", params)


def add_employee():
    eid = new_id()
    execute(
        """INSERT INTO ahb_employees (id, name, position, hourly_rate, pay_type, pay_method, phone, email, active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (eid, data.get("name", ""), data.get("position", ""), data.get("hourly_rate", 0),
         data.get("pay_type", "hourly"), data.get("pay_method", ""), data.get("phone", ""),
         data.get("email", ""), 1 if data.get("active", True) else 0))
    return {"success": True, "id": eid}


def update_employee():
    eid = data.get("id") or filters.get("id", "")
    fields, vals = [], []
    for key in ["name", "position", "hourly_rate", "pay_type", "pay_method", "phone", "email"]:
        if key in data:
            fields.append(f"{key} = ?"); vals.append(data[key])
    if "active" in data:
        fields.append("active = ?"); vals.append(1 if data["active"] else 0)
    if not fields:
        return {"error": "No fields to update"}
    fields.append("updated_at = ?"); vals.append(datetime.now().isoformat()); vals.append(eid)
    execute(f"UPDATE ahb_employees SET {', '.join(fields)} WHERE id = ?", vals)
    return {"success": True}


def list_events():
    sql = "SELECT * FROM ahb_events WHERE 1=1"
    params = []
    if filters.get("category"):
        sql += " AND category = ?"; params.append(filters["category"])
    if filters.get("date"):
        sql += " AND date = ?"; params.append(filters["date"])
    if filters.get("date_from"):
        sql += " AND date >= ?"; params.append(filters["date_from"])
    if filters.get("date_to"):
        sql += " AND date <= ?"; params.append(filters["date_to"])
    return query(sql + " ORDER BY date, time", params)


def add_event():
    eid = new_id()
    execute(
        """INSERT INTO ahb_events (id, title, details, date, time, end_time, category, all_day, project_id, employee_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (eid, data.get("title", ""), data.get("details", ""), data.get("date", ""),
         data.get("time", ""), data.get("end_time", ""), data.get("category", ""),
         1 if data.get("all_day") else 0, data.get("project_id", ""), data.get("employee_id", "")))
    return {"success": True, "id": eid}


def update_event():
    eid = data.get("id") or filters.get("id", "")
    fields, vals = [], []
    for key in ["title", "details", "date", "time", "end_time", "category", "project_id", "employee_id"]:
        if key in data:
            fields.append(f"{key} = ?"); vals.append(data[key])
    if "all_day" in data:
        fields.append("all_day = ?"); vals.append(1 if data["all_day"] else 0)
    if not fields:
        return {"error": "No fields to update"}
    vals.append(eid)
    execute(f"UPDATE ahb_events SET {', '.join(fields)} WHERE id = ?", vals)
    return {"success": True}


def list_notes():
    sql = "SELECT * FROM ahb_notes WHERE 1=1"
    params = []
    if filters.get("is_task"):
        sql += " AND is_task = 1"
    if filters.get("project_id"):
        sql += " AND project_id = ?"; params.append(filters["project_id"])
    return query(sql + " ORDER BY pinned DESC, created_at DESC", params)


def add_note():
    nid = new_id()
    checklist = json.dumps(data.get("checklist_items", [])) if isinstance(data.get("checklist_items"), list) else data.get("checklist_items", "[]")
    execute(
        """INSERT INTO ahb_notes (id, title, content, is_list, is_task, tags, pinned, project_id, due_date, checklist_items, author_employee_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (nid, data.get("title", ""), data.get("content", ""),
         1 if data.get("is_list") else 0, 1 if data.get("is_task") else 0,
         data.get("tags", ""), 1 if data.get("pinned") else 0,
         data.get("project_id", ""), data.get("due_date", ""), checklist, data.get("author_employee_id", "")))
    return {"success": True, "id": nid}


def update_note():
    nid = data.get("id") or filters.get("id", "")
    fields, vals = [], []
    for key in ["title", "content", "tags", "project_id", "due_date", "author_employee_id"]:
        if key in data:
            fields.append(f"{key} = ?"); vals.append(data[key])
    for key in ["is_list", "is_task", "pinned"]:
        if key in data:
            fields.append(f"{key} = ?"); vals.append(1 if data[key] else 0)
    if "checklist_items" in data:
        fields.append("checklist_items = ?")
        vals.append(json.dumps(data["checklist_items"]) if isinstance(data["checklist_items"], list) else data["checklist_items"])
    if not fields:
        return {"error": "No fields to update"}
    fields.append("updated_at = ?"); vals.append(datetime.now().isoformat()); vals.append(nid)
    execute(f"UPDATE ahb_notes SET {', '.join(fields)} WHERE id = ?", vals)
    return {"success": True}


def list_debts():
    sql = "SELECT * FROM ahb_debts WHERE 1=1"
    params = []
    if filters.get("type"):
        sql += " AND type = ?"; params.append(filters["type"])
    return query(sql + " ORDER BY due_date", params)


def add_debt():
    did = new_id()
    execute(
        """INSERT INTO ahb_debts (id, name, type, frequency, payment_amount, due_date, payoff_date, balance)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (did, data.get("name", ""), data.get("type", "Bill"), data.get("frequency", "Monthly"),
         data.get("payment_amount", 0), data.get("due_date", ""), data.get("payoff_date", ""),
         data.get("balance", 0)))
    return {"success": True, "id": did}


def update_debt():
    did = data.get("id") or filters.get("id", "")
    fields, vals = [], []
    for key in ["name", "type", "frequency", "payment_amount", "due_date", "payoff_date", "balance"]:
        if key in data:
            fields.append(f"{key} = ?"); vals.append(data[key])
    if not fields:
        return {"error": "No fields to update"}
    fields.append("updated_at = ?"); vals.append(datetime.now().isoformat()); vals.append(did)
    execute(f"UPDATE ahb_debts SET {', '.join(fields)} WHERE id = ?", vals)
    return {"success": True}


def list_files():
    sql = "SELECT * FROM ahb_files WHERE 1=1"
    params = []
    if filters.get("category"):
        sql += " AND category = ?"; params.append(filters["category"])
    if filters.get("project_id"):
        sql += " AND project_id = ?"; params.append(filters["project_id"])
    if filters.get("year"):
        sql += " AND year = ?"; params.append(filters["year"])
    return query(sql + " ORDER BY created_at DESC", params)


def add_file():
    fid = new_id()
    execute(
        """INSERT INTO ahb_files (id, name, file_type, file_path, tags, category, year, project_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (fid, data.get("name", ""), data.get("file_type", ""), data.get("file_path", ""),
         data.get("tags", ""), data.get("category", ""), data.get("year", ""),
         data.get("project_id", "")))
    return {"success": True, "id": fid}


def list_tax():
    sql = "SELECT * FROM ahb_tax_requirements WHERE 1=1"
    params = []
    if filters.get("category"):
        sql += " AND category = ?"; params.append(filters["category"])
    if filters.get("completed") is not None:
        sql += " AND completed = ?"; params.append(int(filters["completed"]))
    return query(sql + " ORDER BY due_date", params)


def add_tax():
    tid = new_id()
    execute(
        """INSERT INTO ahb_tax_requirements (id, title, details, due_date, completed, category)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (tid, data.get("title", ""), data.get("details", ""), data.get("due_date", ""),
         1 if data.get("completed") else 0, data.get("category", "tax")))
    return {"success": True, "id": tid}


def update_tax():
    tid = data.get("id") or filters.get("id", "")
    fields, vals = [], []
    for key in ["title", "details", "due_date", "category"]:
        if key in data:
            fields.append(f"{key} = ?"); vals.append(data[key])
    if "completed" in data:
        fields.append("completed = ?"); vals.append(1 if data["completed"] else 0)
    if not fields:
        return {"error": "No fields to update"}
    vals.append(tid)
    execute(f"UPDATE ahb_tax_requirements SET {', '.join(fields)} WHERE id = ?", vals)
    return {"success": True}


def list_phases():
    sql = "SELECT * FROM ahb_project_phases WHERE 1=1"
    params = []
    if filters.get("project_id"):
        sql += " AND project_id = ?"; params.append(filters["project_id"])
    return query(sql + " ORDER BY phase_number", params)


def add_phase():
    pid = new_id()
    execute(
        """INSERT INTO ahb_project_phases (id, project_id, phase_number, name, value, start_date, end_date, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (pid, data.get("project_id", ""), data.get("phase_number", 1), data.get("name", ""),
         data.get("value", 0), data.get("start_date", ""), data.get("end_date", ""),
         data.get("status", "pending")))
    return {"success": True, "id": pid}


def dashboard_stats():
    stats = {}
    for table, label in [("ahb_clients", "clients"), ("ahb_projects", "projects"),
                          ("ahb_invoices", "invoices"), ("ahb_receipts", "receipts"),
                          ("ahb_payroll", "payroll"), ("ahb_employees", "employees"),
                          ("ahb_events", "events"), ("ahb_notes", "notes"),
                          ("ahb_debts", "debts"), ("ahb_files", "files"),
                          ("ahb_project_phases", "phases"), ("ahb_tax_requirements", "tax")]:
        try:
            rows = query(f"SELECT COUNT(*) as cnt FROM {table}")
            stats[f"{label}_total"] = rows[0]["cnt"] if rows else 0
        except Exception:
            stats[f"{label}_total"] = 0

    # Detailed breakdowns
    try:
        for row in query("SELECT status, COUNT(*) as cnt FROM ahb_clients GROUP BY status"):
            stats[f"clients_{row['status']}"] = row["cnt"]
    except Exception:
        pass
    try:
        for row in query("SELECT status, COUNT(*) as cnt FROM ahb_projects GROUP BY status"):
            stats[f"projects_{row['status']}"] = row["cnt"]
    except Exception:
        pass
    try:
        for row in query("SELECT status, COALESCE(SUM(total),0) as total FROM ahb_invoices GROUP BY status"):
            stats[f"invoices_{row['status']}_total"] = row["total"]
            stats[f"invoices_{row['status']}_count"] = query(
                "SELECT COUNT(*) as cnt FROM ahb_invoices WHERE status=?", (row["status"],)
            )[0]["cnt"]
    except Exception:
        pass
    try:
        rows = query("SELECT COALESCE(SUM(amount),0) as total FROM ahb_receipts")
        stats["receipts_total_amount"] = rows[0]["total"] if rows else 0
    except Exception:
        pass
    try:
        rows = query("SELECT COALESCE(SUM(total),0) as total FROM ahb_payroll")
        stats["payroll_total_cost"] = rows[0]["total"] if rows else 0
    except Exception:
        pass

    try:
        rows = query("SELECT COALESCE(SUM(balance),0) as total FROM ahb_debts")
        stats["debts_total_balance"] = rows[0]["total"] if rows else 0
        rows = query("SELECT COALESCE(SUM(payment_amount),0) as total FROM ahb_debts")
        stats["debts_monthly_payments"] = rows[0]["total"] if rows else 0
    except Exception:
        pass

    return stats


def search_all():
    q = filters.get("q", "").strip()
    if not q:
        return {"error": "Search query 'q' required"}
    like = f"%{q}%"
    results = {}
    results["clients"] = query(
        "SELECT * FROM ahb_clients WHERE name LIKE ? OR phone LIKE ? OR email LIKE ? OR notes LIKE ? LIMIT 10",
        (like, like, like, like)
    )
    results["projects"] = query(
        "SELECT * FROM ahb_projects WHERE title LIKE ? OR description LIKE ? OR address LIKE ? LIMIT 10",
        (like, like, like)
    )
    results["invoices"] = query(
        "SELECT * FROM ahb_invoices WHERE invoice_number LIKE ? OR notes LIKE ? LIMIT 10",
        (like, like)
    )
    results["receipts"] = query(
        "SELECT * FROM ahb_receipts WHERE vendor LIKE ? OR description LIKE ? OR store_name LIKE ? LIMIT 10",
        (like, like, like)
    )
    results["employees"] = query(
        "SELECT * FROM ahb_employees WHERE name LIKE ? OR position LIKE ? LIMIT 10",
        (like, like)
    )
    results["debts"] = query(
        "SELECT * FROM ahb_debts WHERE name LIKE ? LIMIT 10",
        (like,)
    )
    results["events"] = query(
        "SELECT * FROM ahb_events WHERE title LIKE ? OR details LIKE ? LIMIT 10",
        (like, like)
    )
    results["notes"] = query(
        "SELECT * FROM ahb_notes WHERE title LIKE ? OR content LIKE ? LIMIT 10",
        (like, like)
    )
    return results


# ── Dispatch ──────────────────────────────────────────────────────────────────

ACTIONS = {
    "list_clients": list_clients,
    "get_client": get_client,
    "add_client": add_client,
    "update_client": update_client,
    "list_projects": list_projects,
    "get_project": get_project,
    "add_project": add_project,
    "update_project": update_project,
    "list_invoices": list_invoices,
    "add_invoice": add_invoice,
    "list_receipts": list_receipts,
    "add_receipt": add_receipt,
    "list_payroll": list_payroll,
    "add_payroll": add_payroll,
    "list_estimates": list_estimates,
    "add_estimate": add_estimate,
    "list_employees": list_employees,
    "add_employee": add_employee,
    "update_employee": update_employee,
    "list_events": list_events,
    "add_event": add_event,
    "update_event": update_event,
    "list_notes": list_notes,
    "add_note": add_note,
    "update_note": update_note,
    "list_debts": list_debts,
    "add_debt": add_debt,
    "update_debt": update_debt,
    "list_files": list_files,
    "add_file": add_file,
    "list_tax": list_tax,
    "add_tax": add_tax,
    "update_tax": update_tax,
    "list_phases": list_phases,
    "add_phase": add_phase,
    "dashboard_stats": dashboard_stats,
    "search": search_all,
}

if action in ACTIONS:
    try:
        result = ACTIONS[action]()
        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
else:
    print(json.dumps({
        "error": f"Unknown action: {action}",
        "available_actions": list(ACTIONS.keys())
    }))
