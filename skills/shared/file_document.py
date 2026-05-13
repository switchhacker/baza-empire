#!/usr/bin/env python3
"""
Baza Empire — Post-Curate Document Filer

Runs AFTER curate_document. Takes the curator's analysis + the file path
and performs the downstream filing:

  - receipt      -> receipt_ocr + insert into ahb_receipts
  - permit/coi/license/w9/contract/change_order/lien_waiver/invoice/estimate
                 -> resolve project_hint to ahb_projects.id, update ahb_documents.project_id
  - other        -> already in ahb_documents, nothing to do

SKILL_ARGS:
  file_path     : "/abs/path/to/file"        (required)
  analysis      : {...curate_document output...}  (required)
  caption       : "this is a receipt"        (optional; overrides doc_type)
  agent_id      : "phil_hass"                (optional; default "phil_hass")
  default_proj  : "proj-ahb123"              (optional; fallback when no hint)
"""
import os
import sys
import json
import sqlite3
import datetime
import uuid
import re
import subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
file_path    = args.get("file_path", "")
analysis     = args.get("analysis") or {}
caption      = (args.get("caption") or "").lower()
agent_id     = args.get("agent_id") or "phil_hass"
default_proj = args.get("default_proj") or "proj-ahb123"

if not file_path or not os.path.exists(file_path):
    print(json.dumps({"success": False, "error": f"file not found: {file_path}"}))
    sys.exit(1)

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DASHBOARD_DB  = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
SKILLS_DIR    = os.path.join(FRAMEWORK_DIR, "skills", "shared")

RECEIPT_DOC_TYPES = {"receipt"}
PROJECT_DOC_TYPES = {
    "permit", "coi", "license", "w9", "contract", "change_order",
    "lien_waiver", "invoice", "estimate", "blueprint", "tax_document",
    "correspondence", "id_document", "project_photo",
}

CAPTION_OVERRIDES = [
    (re.compile(r"\b(receipt|invoice paid|purchase)\b"),      "receipt"),
    (re.compile(r"\bpermit\b"),                               "permit"),
    (re.compile(r"\b(coi|cert(?:ificate)? of insurance)\b"),  "coi"),
    (re.compile(r"\bw[- ]?9\b"),                              "w9"),
    (re.compile(r"\blicense\b"),                              "license"),
    (re.compile(r"\bcontract\b"),                             "contract"),
    (re.compile(r"\bchange order\b"),                         "change_order"),
    (re.compile(r"\blien waiver\b"),                          "lien_waiver"),
    (re.compile(r"\bestimate\b"),                             "estimate"),
    (re.compile(r"\bblueprint|plans?\b"),                     "blueprint"),
]

def _conn():
    c = sqlite3.connect(DASHBOARD_DB)
    c.row_factory = sqlite3.Row
    return c

def apply_caption_override(doc_type):
    if not caption:
        return doc_type
    for rx, t in CAPTION_OVERRIDES:
        if rx.search(caption):
            return t
    return doc_type

def run_skill_subprocess(name, skill_args):
    """Invoke another skill script directly. Returns parsed JSON or {}."""
    script = os.path.join(SKILLS_DIR, f"{name}.py")
    if not os.path.exists(script):
        return {"_error": f"skill script missing: {name}"}
    env = os.environ.copy()
    env["SKILL_ARGS"] = json.dumps(skill_args)
    env["AGENT_ID"]   = agent_id
    try:
        out = subprocess.check_output(
            [sys.executable, script], env=env, timeout=240, stderr=subprocess.STDOUT
        )
        txt = out.decode("utf-8", errors="ignore").strip()
        try:
            return json.loads(txt)
        except Exception:
            return {"_raw": txt[:2000]}
    except subprocess.CalledProcessError as e:
        return {"_error": f"{name} exit {e.returncode}", "_raw": e.output.decode(errors="ignore")[:1000]}
    except Exception as e:
        return {"_error": f"{name} failed: {e}"}

def _normalize_addr(s):
    """Strip punctuation, zip codes, common abbreviations for address comparison."""
    s = s.lower().strip()
    s = re.sub(r"[,.\-#()]", " ", s)
    # Remove zip codes (5-digit or 5+4)
    s = re.sub(r"\b\d{5}(?:-\d{4})?\b", "", s)
    # Normalize common abbreviations
    for full, abbr in [("street", "st"), ("avenue", "ave"), ("drive", "dr"),
                       ("road", "rd"), ("boulevard", "blvd"), ("lane", "ln"),
                       ("court", "ct"), ("place", "pl"), ("philadelphia", "phila"),
                       ("pennsylvania", "pa")]:
        s = re.sub(rf"\b{full}\b", abbr, s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _word_overlap(a, b):
    """Return fraction of words in shorter string found in longer string."""
    wa = set(a.split())
    wb = set(b.split())
    if not wa or not wb:
        return 0.0
    shorter, longer = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    if not shorter:
        return 0.0
    return len(shorter & longer) / len(shorter)


def resolve_project(hint):
    """Fuzzy-match hint against ahb_projects.title/address/location/client_name.
    Returns (project_id, match_note) or (None, reason)."""
    if not hint:
        return (None, "no hint")
    hint_n = _normalize_addr(hint)
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT id, title, address, location, client_name FROM ahb_projects"
        ).fetchall()
        conn.close()
    except Exception as e:
        return (None, f"db error: {e}")

    scored = []  # (score, project_id, matched_field)
    for r in rows:
        fields = [
            (r["title"] or ""),
            (r["address"] or ""),
            (r["location"] or ""),
            (r["client_name"] or ""),
        ]
        for raw_f in fields:
            if not raw_f.strip():
                continue
            f_n = _normalize_addr(raw_f)
            # Exact normalized match
            if hint_n == f_n:
                return (r["id"], f"exact match: {raw_f}")
            # Substring match (either direction)
            if hint_n in f_n or f_n in hint_n:
                scored.append((0.95, r["id"], raw_f))
                continue
            # Word-overlap match (>= 60% of the shorter side's words)
            overlap = _word_overlap(hint_n, f_n)
            if overlap >= 0.6:
                scored.append((overlap, r["id"], raw_f))

    if not scored:
        return (None, f"no project matched '{hint}'")

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[0]
    if top[0] >= 0.6:
        return (top[1], f"matched ({top[0]:.0%}): {top[2]}")
    return (None, f"no project matched '{hint}'")

def link_document_to_project(project_id):
    """Update ahb_documents.project_id for this file."""
    try:
        conn = _conn()
        conn.execute(
            "UPDATE ahb_documents SET project_id=? WHERE file_path=?",
            (project_id, file_path),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        return f"db error: {e}"

def file_receipt():
    """OCR the file and insert into ahb_receipts."""
    ext = os.path.splitext(file_path)[1].lower()
    ocr = {}
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".tif", ".tiff", ".pdf"):
        ocr = run_skill_subprocess("receipt_ocr", {"image_path": file_path, "mode": "full"})
    structured = (ocr.get("structured") or {}) if isinstance(ocr, dict) else {}
    raw_text   = (ocr.get("ocr_raw") or "") if isinstance(ocr, dict) else ""

    # Fall back to curate fields if OCR missed something
    vendor         = structured.get("store_name") or analysis.get("entity") or ""
    store_location = structured.get("store_location") or ""
    teller_name    = structured.get("teller_name") or ""
    purchase_time  = structured.get("purchase_time") or ""
    # Receipt date MUST be the date printed on the receipt. If OCR didn't find
    # one, leave it blank rather than defaulting to today — Serge was explicit.
    receipt_date = structured.get("purchase_date") or ""
    total        = structured.get("total") or 0
    subtotal     = structured.get("subtotal") or 0
    tax_amount   = structured.get("tax_amount") or 0
    category     = structured.get("category") or ""
    payment      = structured.get("payment_method") or ""
    items        = structured.get("items") or []
    description  = analysis.get("summary") or ""

    # Normalize vendor + infer category via vendor_kb (fuzzy-match against
    # seed + 889 rows of history + learned aliases). Leaves raw vendor alone
    # when no strong match — prevents garbage-in/garbage-out collapses.
    try:
        from vendor_kb import match_vendor, suggest_category_from_items
        canonical, cat_hint, vconf = match_vendor(vendor)
        if canonical and vconf >= 0.85:
            vendor = canonical
        if not category and cat_hint:
            category = cat_hint
        if not category:
            item_cat = suggest_category_from_items(items)
            if item_cat:
                category = item_cat
    except Exception:
        pass

    # Project resolution (optional for receipts)
    project_id, proj_note = resolve_project(analysis.get("project_hint") or "")
    if not project_id:
        project_id = default_proj

    year = ""
    if receipt_date and len(receipt_date) >= 4:
        year = receipt_date[:4]

    # Telegram-inbound receipts PARK in the QuickRF queue (status='ready') so
    # Serge can categorize and confirm the parsed values before they hit
    # ahb_receipts. The queue row's result_json matches the shape the QuickRF
    # confirm modal expects, so the UI can prefill every field.
    qid = uuid.uuid4().hex
    result_json = {
        "success": True,
        "ocr_raw": raw_text,
        "structured": {
            "store_name": vendor,
            "store_location": store_location,
            "teller_name": teller_name,
            "purchase_date": receipt_date,
            "purchase_time": purchase_time,
            "items": items,
            "subtotal": float(subtotal or 0),
            "tax_amount": float(tax_amount or 0),
            "total": float(total or 0),
            "payment_method": payment,
            "category": category,
            "description": description,
            "project_id": project_id or "",
            "project_note": proj_note,
            "year": year,
            "agent_id": agent_id,
        },
        "warnings": [],
    }
    try:
        conn = _conn()
        conn.execute(
            """INSERT INTO ahb_receipt_queue
               (id, image_path, mode, status, result_json, receipt_id, error)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (qid, file_path, "telegram", "ready", json.dumps(result_json), "", ""),
        )
        # Also link in ahb_documents so the project association sticks
        conn.execute("UPDATE ahb_documents SET project_id=? WHERE file_path=?",
                     (project_id, file_path))
        conn.commit()
        conn.close()
    except Exception as e:
        return {"success": False, "step": "queue_receipt", "error": str(e)}

    return {
        "success": True,
        "action": "queued_in_quickrf",
        "queue_id": qid,
        "project_id": project_id,
        "project_note": proj_note,
        "vendor": vendor,
        "store_location": store_location,
        "teller_name": teller_name,
        "total": total,
        "receipt_date": receipt_date,
        "category": category,
        "ocr_ok": bool(ocr.get("success")) if isinstance(ocr, dict) else False,
    }

def file_project_document(doc_type):
    hint = analysis.get("project_hint") or analysis.get("entity") or ""
    project_id, note = resolve_project(hint)
    if project_id:
        link_document_to_project(project_id)
        return {
            "success": True,
            "action": "linked_to_project",
            "doc_type": doc_type,
            "project_id": project_id,
            "project_note": note,
        }
    return {
        "success": True,
        "action": "unassigned",
        "doc_type": doc_type,
        "reason": note,
        "hint": hint,
    }

# ─── Main ────────────────────────────────────────────────────────────────────

doc_type = (analysis.get("doc_type") or "other").lower()
doc_type = apply_caption_override(doc_type)

if doc_type in RECEIPT_DOC_TYPES:
    result = file_receipt()
elif doc_type in PROJECT_DOC_TYPES:
    result = file_project_document(doc_type)
else:
    result = {"success": True, "action": "kept_in_library", "doc_type": doc_type}

result["final_doc_type"] = doc_type
print(json.dumps(result, indent=2))
