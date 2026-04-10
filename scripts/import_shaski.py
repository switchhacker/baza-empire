#!/usr/bin/env python3
"""
Import Shaski JSON export into the AHB123 SQLite database.

Usage:
    python scripts/import_shaski.py            # interactive confirmation
    python scripts/import_shaski.py --force     # skip confirmation, clear and import
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime

JSON_PATH = "/media/switchhacker/USB DISK2/Shaski-Export-2026-04-02_1110.json"
DB_PATH = "/home/switchhacker/baza-empire/agent-framework-v3/dashboard/baza_projects.db"

AHB_TABLES = [
    "ahb_clients", "ahb_employees", "ahb_projects", "ahb_project_phases",
    "ahb_invoices", "ahb_payroll", "ahb_receipts", "ahb_events",
    "ahb_notes", "ahb_debts", "ahb_files", "ahb_tax_requirements",
]

# ALTER TABLE statements to add missing columns to existing tables
ALTER_TABLES_SQL = [
    # ahb_clients missing columns
    "ALTER TABLE ahb_clients ADD COLUMN company TEXT DEFAULT ''",
    "ALTER TABLE ahb_clients ADD COLUMN tags TEXT DEFAULT ''",
    # ahb_projects missing columns
    "ALTER TABLE ahb_projects ADD COLUMN acquisition_type TEXT DEFAULT ''",
    "ALTER TABLE ahb_projects ADD COLUMN value REAL DEFAULT 0",
    # ahb_invoices missing columns
    "ALTER TABLE ahb_invoices ADD COLUMN client_name TEXT DEFAULT ''",
    "ALTER TABLE ahb_invoices ADD COLUMN project_name TEXT DEFAULT ''",
    "ALTER TABLE ahb_invoices ADD COLUMN terms TEXT DEFAULT ''",
    "ALTER TABLE ahb_invoices ADD COLUMN updated_at TEXT",
    # ahb_receipts missing columns
    "ALTER TABLE ahb_receipts ADD COLUMN store_name TEXT DEFAULT ''",
    "ALTER TABLE ahb_receipts ADD COLUMN payment_method TEXT DEFAULT ''",
    "ALTER TABLE ahb_receipts ADD COLUMN total REAL DEFAULT 0",
    # ahb_payroll missing columns
    "ALTER TABLE ahb_payroll ADD COLUMN overtime_hours REAL DEFAULT 0",
    "ALTER TABLE ahb_payroll ADD COLUMN employee_id TEXT",
]

# DDL for tables that may not exist yet
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS ahb_employees (
    id TEXT PRIMARY KEY,
    name TEXT,
    position TEXT,
    hourly_rate REAL,
    pay_type TEXT DEFAULT 'hourly',
    pay_method TEXT,
    phone TEXT,
    email TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS ahb_events (
    id TEXT PRIMARY KEY,
    title TEXT,
    details TEXT,
    date TEXT,
    time TEXT,
    end_time TEXT,
    category TEXT,
    all_day INTEGER DEFAULT 0,
    project_id TEXT,
    employee_id TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS ahb_notes (
    id TEXT PRIMARY KEY,
    title TEXT,
    content TEXT,
    is_list INTEGER,
    is_task INTEGER,
    tags TEXT,
    pinned INTEGER,
    project_id TEXT,
    due_date TEXT,
    checklist_items TEXT,
    author_employee_id TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS ahb_debts (
    id TEXT PRIMARY KEY,
    name TEXT,
    type TEXT,
    frequency TEXT DEFAULT 'Monthly',
    payment_amount REAL,
    due_date TEXT,
    payoff_date TEXT,
    balance REAL,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS ahb_files (
    id TEXT PRIMARY KEY,
    name TEXT,
    file_type TEXT,
    file_path TEXT,
    size INTEGER,
    tags TEXT,
    category TEXT,
    year TEXT,
    project_id TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS ahb_project_phases (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    phase_number INTEGER,
    name TEXT,
    value REAL,
    start_date TEXT,
    end_date TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS ahb_tax_requirements (
    id TEXT PRIMARY KEY,
    title TEXT,
    details TEXT,
    due_date TEXT,
    completed INTEGER,
    category TEXT DEFAULT 'tax',
    created_at TEXT
);
"""


def gen_id():
    return str(uuid.uuid4())


def now_iso():
    return datetime.now().isoformat()


def bool_to_int(val):
    """Convert boolean/truthy value to 0 or 1."""
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, int):
        return 1 if val else 0
    if isinstance(val, str):
        return 1 if val.lower() in ("true", "1", "yes") else 0
    return 0


def safe_float(val, default=0.0):
    try:
        return float(val) if val else default
    except (ValueError, TypeError):
        return default


def clear_ahb_data(conn):
    """Delete all rows from AHB tables."""
    cursor = conn.cursor()
    for table in AHB_TABLES:
        try:
            cursor.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass  # table may not exist yet
    conn.commit()


def build_project_lookup(conn):
    """Return dict mapping project title (lowered) -> project id."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM ahb_projects")
    return {row[1].strip().lower(): row[0] for row in cursor.fetchall() if row[1]}


def build_client_lookup(conn):
    """Return dict mapping client name (lowered) -> client id."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM ahb_clients")
    return {row[1].strip().lower(): row[0] for row in cursor.fetchall() if row[1]}


def build_employee_lookup(conn):
    """Return dict mapping employee name (lowered) -> employee id."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM ahb_employees")
    return {row[1].strip().lower(): row[0] for row in cursor.fetchall() if row[1]}


def resolve_project_id(project_name, lookup):
    if not project_name:
        return None
    return lookup.get(project_name.strip().lower())


def resolve_client_id(client_name, lookup):
    if not client_name:
        return None
    return lookup.get(client_name.strip().lower())


def resolve_employee_id(employee_name, lookup):
    if not employee_name:
        return None
    return lookup.get(employee_name.strip().lower())


def import_clients(conn, records):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            cursor.execute(
                """INSERT INTO ahb_clients
                   (id, name, phone, email, address, city, source, notes, company, tags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), rec.get("name", ""), rec.get("phone", ""), rec.get("email", ""),
                 rec.get("address", ""), rec.get("city", ""), rec.get("source", ""),
                 rec.get("notes", ""), rec.get("company", ""), rec.get("tags", ""), ts, ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] client '{rec.get('name', '?')}': {e}")
    conn.commit()
    return imported


def import_employees(conn, records):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            cursor.execute(
                """INSERT INTO ahb_employees
                   (id, name, position, hourly_rate, pay_type, pay_method, phone, email, active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), rec.get("name", ""), rec.get("position", ""),
                 safe_float(rec.get("hourly_rate")), rec.get("pay_type", "hourly"),
                 rec.get("pay_method", ""), rec.get("phone", ""), rec.get("email", ""),
                 bool_to_int(rec.get("active", True)), ts, ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] employee '{rec.get('name', '?')}': {e}")
    conn.commit()
    return imported


def import_projects(conn, records, client_lookup):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            value = safe_float(rec.get("value"))
            client_id = resolve_client_id(rec.get("client_name"), client_lookup)
            cursor.execute(
                """INSERT INTO ahb_projects
                   (id, client_id, title, address, description, budget_high, status,
                    start_date, end_date, notes, acquisition_type, value, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), client_id, rec.get("name", ""), rec.get("address", ""),
                 rec.get("details", ""), value, rec.get("status", "estimate"),
                 rec.get("start_date", ""), rec.get("end_date", ""), "",
                 rec.get("acquisition_type", ""), value, ts, ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] project '{rec.get('name', '?')}': {e}")
    conn.commit()
    return imported


def import_project_phases(conn, records, project_lookup):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            project_id = resolve_project_id(rec.get("project_name"), project_lookup)
            cursor.execute(
                """INSERT INTO ahb_project_phases
                   (id, project_id, phase_number, name, value, start_date, end_date, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), project_id, rec.get("phase_number", 0), rec.get("name", ""),
                 safe_float(rec.get("value")), rec.get("start_date", ""),
                 rec.get("end_date", ""), rec.get("status", "pending"), ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] phase '{rec.get('name', '?')}': {e}")
    conn.commit()
    return imported


def import_invoices(conn, records, client_lookup, project_lookup):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            line_items = rec.get("line_items", [])
            # Calculate subtotal from line items
            subtotal = 0.0
            if isinstance(line_items, list):
                for item in line_items:
                    qty = safe_float(item.get("quantity", 1))
                    price = safe_float(item.get("unit_price", 0))
                    subtotal += qty * price
            line_items_str = json.dumps(line_items) if isinstance(line_items, list) else str(line_items)

            client_id = resolve_client_id(rec.get("client_name"), client_lookup)
            project_id = resolve_project_id(rec.get("project_name"), project_lookup)

            cursor.execute(
                """INSERT INTO ahb_invoices
                   (id, client_id, project_id, invoice_number, line_items, subtotal, total,
                    status, notes, client_name, project_name, terms, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), client_id, project_id, rec.get("invoice_number", ""),
                 line_items_str, subtotal, subtotal,
                 rec.get("status", "draft"), rec.get("notes", ""),
                 rec.get("client_name", ""), rec.get("project_name", ""),
                 rec.get("terms", ""), ts, ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] invoice '{rec.get('invoice_number', '?')}': {e}")
    conn.commit()
    return imported


def import_payroll(conn, records, employee_lookup):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            hours = safe_float(rec.get("hours"))
            rate = safe_float(rec.get("rate"))
            overtime = safe_float(rec.get("overtime_hours"))
            total = hours * rate
            employee_id = resolve_employee_id(rec.get("employee_name"), employee_lookup)

            cursor.execute(
                """INSERT INTO ahb_payroll
                   (id, worker_name, hours, rate, total, period_start, period_end,
                    notes, overtime_hours, employee_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), rec.get("employee_name", ""), hours, rate, total,
                 rec.get("period_start", ""), rec.get("period_end", ""),
                 rec.get("notes", ""), overtime, employee_id, ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] payroll '{rec.get('employee_name', '?')}': {e}")
    conn.commit()
    return imported


def import_receipts(conn, records):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            total = safe_float(rec.get("total"))
            cursor.execute(
                """INSERT INTO ahb_receipts
                   (id, vendor, amount, category, receipt_date, ocr_text,
                    store_name, payment_method, total, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), rec.get("store_name", ""), total,
                 rec.get("category", ""), rec.get("receipt_date", ""),
                 rec.get("ocr_text", ""), rec.get("store_name", ""),
                 rec.get("payment_method", ""), total, ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] receipt '{rec.get('store_name', '?')}': {e}")
    conn.commit()
    return imported


def import_events(conn, records):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            cursor.execute(
                """INSERT INTO ahb_events
                   (id, title, details, date, category, all_day, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), rec.get("title", ""), rec.get("details", ""),
                 rec.get("date", ""), rec.get("category", ""),
                 bool_to_int(rec.get("all_day", False)), ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] event '{rec.get('title', '?')}': {e}")
    conn.commit()
    return imported


def import_notes(conn, records, project_lookup):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            checklist = rec.get("checklist_items", [])
            checklist_str = json.dumps(checklist) if isinstance(checklist, list) else str(checklist)
            project_id = resolve_project_id(rec.get("project_name"), project_lookup)

            cursor.execute(
                """INSERT INTO ahb_notes
                   (id, title, content, is_list, is_task, tags, pinned, project_id,
                    due_date, checklist_items, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), rec.get("title", ""), rec.get("content", ""),
                 bool_to_int(rec.get("is_list", False)),
                 bool_to_int(rec.get("is_task", False)),
                 rec.get("tags", ""),
                 bool_to_int(rec.get("pinned", False)),
                 project_id, rec.get("due_date", ""), checklist_str, ts, ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] note '{rec.get('title', '?')}': {e}")
    conn.commit()
    return imported


def import_debts(conn, records):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            cursor.execute(
                """INSERT INTO ahb_debts
                   (id, name, type, frequency, payment_amount, due_date, balance, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), rec.get("name", ""), rec.get("type", ""),
                 rec.get("frequency", "Monthly"),
                 safe_float(rec.get("payment_amount")),
                 rec.get("due_date", ""),
                 safe_float(rec.get("balance")), ts, ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] debt '{rec.get('name', '?')}': {e}")
    conn.commit()
    return imported


def import_files(conn, records, project_lookup):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            project_id = resolve_project_id(rec.get("project_name"), project_lookup)
            cursor.execute(
                """INSERT INTO ahb_files
                   (id, name, file_type, tags, category, project_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), rec.get("name", ""), rec.get("file_type", ""),
                 rec.get("tags", ""), rec.get("category", ""), project_id, ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] file '{rec.get('name', '?')}': {e}")
    conn.commit()
    return imported


def import_tax_requirements(conn, records):
    cursor = conn.cursor()
    ts = now_iso()
    imported = 0
    for rec in records:
        try:
            cursor.execute(
                """INSERT INTO ahb_tax_requirements
                   (id, title, details, due_date, completed, category, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (gen_id(), rec.get("title", ""), rec.get("details", ""),
                 rec.get("due_date", ""), bool_to_int(rec.get("completed", False)),
                 rec.get("category", "tax"), ts),
            )
            imported += 1
        except Exception as e:
            print(f"  [SKIP] tax req '{rec.get('title', '?')}': {e}")
    conn.commit()
    return imported


def main():
    parser = argparse.ArgumentParser(description="Import Shaski JSON export into AHB123 SQLite database")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--json", type=str, default=JSON_PATH, help="Path to Shaski JSON export")
    parser.add_argument("--db", type=str, default=DB_PATH, help="Path to SQLite database")
    args = parser.parse_args()

    # Validate paths
    if not os.path.exists(args.json):
        print(f"ERROR: JSON file not found: {args.json}")
        sys.exit(1)
    if not os.path.exists(args.db):
        print(f"ERROR: Database not found: {args.db}")
        sys.exit(1)

    # Load JSON
    print(f"Loading JSON from: {args.json}")
    with open(args.json, "r") as f:
        data = json.load(f)

    # Show record counts
    print("\nShaski export contents:")
    for key in sorted(data.keys()):
        if isinstance(data[key], list):
            print(f"  {key}: {len(data[key])} records")

    # Confirm
    if not args.force:
        print(f"\nThis will CLEAR all existing AHB data in:\n  {args.db}")
        answer = input("Continue? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(0)

    # Connect
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")

    # Create missing tables
    print("\nCreating missing tables...")
    conn.executescript(CREATE_TABLES_SQL)

    # Add missing columns to existing tables
    print("Adding missing columns to existing tables...")
    for stmt in ALTER_TABLES_SQL:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists

    # Clear existing data
    print("Clearing existing AHB data...")
    clear_ahb_data(conn)

    results = {}

    # 1. Clients
    print("\nImporting clients...")
    results["clients"] = import_clients(conn, data.get("clients", []))

    # 2. Employees
    print("Importing employees...")
    results["employees"] = import_employees(conn, data.get("employees", []))

    # Build lookups
    client_lookup = build_client_lookup(conn)
    employee_lookup = build_employee_lookup(conn)

    # 3. Projects (depends on clients)
    print("Importing projects...")
    results["projects"] = import_projects(conn, data.get("projects", []), client_lookup)

    # Build project lookup
    project_lookup = build_project_lookup(conn)

    # 4. Project phases (depends on projects)
    print("Importing project phases...")
    results["project_phases"] = import_project_phases(conn, data.get("project_phases", []), project_lookup)

    # 5. Invoices (depends on clients + projects)
    print("Importing invoices...")
    results["invoices"] = import_invoices(conn, data.get("invoices", []), client_lookup, project_lookup)

    # 6. Payroll (depends on employees)
    print("Importing payroll...")
    results["payroll"] = import_payroll(conn, data.get("payroll", []), employee_lookup)

    # 7. Receipts
    print("Importing receipts...")
    results["receipts"] = import_receipts(conn, data.get("receipts", []))

    # 8. Events
    print("Importing events...")
    results["events"] = import_events(conn, data.get("events", []))

    # 9. Notes (depends on projects)
    print("Importing notes...")
    results["notes"] = import_notes(conn, data.get("notes", []), project_lookup)

    # 10. Debts
    print("Importing debts...")
    results["debts"] = import_debts(conn, data.get("debts", []))

    # 11. Files (depends on projects)
    print("Importing files...")
    results["files"] = import_files(conn, data.get("files", []), project_lookup)

    # 12. Tax requirements
    print("Importing tax requirements...")
    results["tax_requirements"] = import_tax_requirements(conn, data.get("tax_requirements", []))

    conn.close()

    # Summary
    print("\n" + "=" * 50)
    print("IMPORT COMPLETE")
    print("=" * 50)
    total = 0
    for key, count in results.items():
        source_count = len(data.get(key, []))
        status = "OK" if count == source_count else f"PARTIAL ({source_count - count} skipped)"
        print(f"  {key:20s}: {count:4d} / {source_count:4d}  {status}")
        total += count
    print(f"  {'TOTAL':20s}: {total:4d}")
    print()


if __name__ == "__main__":
    main()
