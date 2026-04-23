#!/usr/bin/env python3
"""
Baza Empire — Knowledge Index Rebuild

Builds / refreshes the unified FTS5 search index `baza_knowledge_fts` in
`dashboard/baza_projects.db`. Indexes the AHBCO business corpus so agents can
semantic-ish search across it via `knowledge_search`.

Sources pulled into the index:
  - ahb_projects        (title, address, client_name, description, status)
  - ahb_receipts        (vendor, items_json, notes, date)
  - ahb_invoices        (client_name, description, status, amount)
  - ahb_documents       (doc_type, entity, summary, content_text, tags)
  - ahb_debts           (creditor, description, status)
  - ahb_employees       (name, role, notes)
  - ahb_clients         (name, company, notes, email, phone)
  - ahb_notes           (title, body, tags)
  - Postgres empire_knowledge  (key, value, category)
  - Postgres agent_memory      (agent_id, key, value, category)

Run: SKILL_ARGS='{}' python knowledge_rebuild_index.py
Scheduled daily via cron.
"""
import os, sys, json, sqlite3, traceback

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(FRAMEWORK, "dashboard", "baza_projects.db")

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS baza_knowledge_fts USING fts5(
    source,         -- e.g. 'project', 'receipt', 'document', 'empire_knowledge'
    source_id,      -- primary key from origin table
    title,          -- short human-readable title
    body,           -- searchable text
    tags,           -- space-separated keywords
    tokenize='porter unicode61'
);
"""

def _safe(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        print(f"[skip] {sql[:60]}... -> {e}", file=sys.stderr)
        return []

def _columns(conn, table):
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()

def _insert(conn, source, source_id, title, body, tags=""):
    conn.execute(
        "INSERT INTO baza_knowledge_fts(source, source_id, title, body, tags) VALUES (?,?,?,?,?)",
        (source, str(source_id or ""), (title or "")[:400], (body or "")[:8000], tags or ""),
    )

def rebuild():
    counts = {}
    conn = sqlite3.connect(DB)
    conn.execute(FTS_DDL)
    conn.execute("DELETE FROM baza_knowledge_fts")

    # Projects
    cols = _columns(conn, "ahb_projects")
    if cols:
        selectable = [c for c in ("project_id","id","title","address","client_name","description","status","location") if c in cols]
        rows = _safe(conn, f"SELECT {', '.join(selectable)} FROM ahb_projects")
        for row in rows:
            rec = dict(zip(selectable, row))
            pid = rec.get("project_id") or rec.get("id")
            title = rec.get("title") or f"Project {pid}"
            body = " | ".join(str(rec.get(k) or "") for k in ("address","client_name","description","location"))
            _insert(conn, "project", pid, title, body, rec.get("status") or "")
        counts["projects"] = len(rows)

    # Receipts
    cols = _columns(conn, "ahb_receipts")
    if cols:
        selectable = [c for c in ("id","vendor","amount","date","items_json","project_id","notes","payment_method") if c in cols]
        rows = _safe(conn, f"SELECT {', '.join(selectable)} FROM ahb_receipts")
        for row in rows:
            rec = dict(zip(selectable, row))
            title = f"Receipt: {rec.get('vendor') or 'unknown'} ${rec.get('amount') or '?'}"
            body = " | ".join(str(rec.get(k) or "") for k in ("vendor","date","items_json","notes","project_id","payment_method"))
            _insert(conn, "receipt", rec.get("id"), title, body, rec.get("payment_method") or "")
        counts["receipts"] = len(rows)

    # Invoices
    cols = _columns(conn, "ahb_invoices")
    if cols:
        selectable = [c for c in ("id","client_name","description","amount","status","project_id","invoice_number","due_date") if c in cols]
        rows = _safe(conn, f"SELECT {', '.join(selectable)} FROM ahb_invoices")
        for row in rows:
            rec = dict(zip(selectable, row))
            title = f"Invoice #{rec.get('invoice_number') or rec.get('id')} — {rec.get('client_name') or ''}"
            body = " | ".join(str(rec.get(k) or "") for k in ("client_name","description","status","project_id","due_date","amount"))
            _insert(conn, "invoice", rec.get("id"), title, body, rec.get("status") or "")
        counts["invoices"] = len(rows)

    # Documents
    cols = _columns(conn, "ahb_documents")
    if cols:
        selectable = [c for c in ("id","doc_type","entity","summary","content_text","tags","suggested_name","project_id","doc_date") if c in cols]
        rows = _safe(conn, f"SELECT {', '.join(selectable)} FROM ahb_documents")
        for row in rows:
            rec = dict(zip(selectable, row))
            title = rec.get("suggested_name") or f"{rec.get('doc_type') or 'doc'}: {rec.get('entity') or ''}"
            body = " | ".join(str(rec.get(k) or "") for k in ("summary","content_text","entity","project_id","doc_date"))
            _insert(conn, "document", rec.get("id"), title, body, f"{rec.get('doc_type') or ''} {rec.get('tags') or ''}")
        counts["documents"] = len(rows)

    # Debts
    cols = _columns(conn, "ahb_debts")
    if cols:
        selectable = [c for c in ("id","creditor","amount","status","description","due_date") if c in cols]
        rows = _safe(conn, f"SELECT {', '.join(selectable)} FROM ahb_debts")
        for row in rows:
            rec = dict(zip(selectable, row))
            title = f"Debt: {rec.get('creditor') or ''} ${rec.get('amount') or ''}"
            body = " | ".join(str(rec.get(k) or "") for k in ("description","status","due_date"))
            _insert(conn, "debt", rec.get("id"), title, body, rec.get("status") or "")
        counts["debts"] = len(rows)

    # Employees
    cols = _columns(conn, "ahb_employees")
    if cols:
        selectable = [c for c in ("id","name","role","notes","pay_type","active") if c in cols]
        rows = _safe(conn, f"SELECT {', '.join(selectable)} FROM ahb_employees")
        for row in rows:
            rec = dict(zip(selectable, row))
            title = f"Employee: {rec.get('name') or ''} ({rec.get('role') or ''})"
            body = " | ".join(str(rec.get(k) or "") for k in ("notes","pay_type","active"))
            _insert(conn, "employee", rec.get("id"), title, body, rec.get("role") or "")
        counts["employees"] = len(rows)

    # Clients
    cols = _columns(conn, "ahb_clients")
    if cols:
        selectable = [c for c in ("id","name","company","email","phone","notes","address") if c in cols]
        rows = _safe(conn, f"SELECT {', '.join(selectable)} FROM ahb_clients")
        for row in rows:
            rec = dict(zip(selectable, row))
            title = f"Client: {rec.get('name') or ''}"
            body = " | ".join(str(rec.get(k) or "") for k in ("company","email","phone","notes","address"))
            _insert(conn, "client", rec.get("id"), title, body, "")
        counts["clients"] = len(rows)

    # Notes
    cols = _columns(conn, "ahb_notes")
    if cols:
        selectable = [c for c in ("id","title","body","tags","pinned") if c in cols]
        rows = _safe(conn, f"SELECT {', '.join(selectable)} FROM ahb_notes")
        for row in rows:
            rec = dict(zip(selectable, row))
            _insert(conn, "note", rec.get("id"), rec.get("title") or "Note",
                    rec.get("body") or "", rec.get("tags") or "")
        counts["notes"] = len(rows)

    conn.commit()

    # Postgres knowledge (optional — skip if not reachable)
    try:
        import psycopg2
        pg = psycopg2.connect(
            host=os.environ.get("DB_HOST","localhost"),
            dbname="baza_agents",
            user=os.environ.get("DB_USER","switchhacker"),
            password=os.environ.get("DB_PASSWORD","baza2026"),
        )
        cur = pg.cursor()
        cur.execute("SELECT id, key, value, category, updated_by FROM empire_knowledge")
        for id_, key, value, category, updated_by in cur.fetchall():
            _insert(conn, "empire_knowledge", id_, key or "", value or "", f"{category or ''} {updated_by or ''}")
        counts["empire_knowledge"] = cur.rowcount

        cur.execute("SELECT id, agent_id, key, value, category FROM agent_memory")
        for id_, agent_id, key, value, category in cur.fetchall():
            _insert(conn, "agent_memory", f"{agent_id}:{id_}", f"{agent_id}/{key}", value or "", f"{category or ''} {agent_id}")
        counts["agent_memory"] = cur.rowcount

        pg.close()
        conn.commit()
    except Exception as e:
        counts["postgres_error"] = str(e)[:200]

    conn.close()
    return counts


if __name__ == "__main__":
    try:
        c = rebuild()
        print(json.dumps({"ok": True, "indexed": c}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()[-800:]}))
        sys.exit(1)
