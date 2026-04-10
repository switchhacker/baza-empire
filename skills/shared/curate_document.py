#!/usr/bin/env python3
"""
Baza Empire — Universal Document Curator Skill

Auto-analyzes any incoming attachment (image, PDF, Word, text) and extracts:
  - doc_type      : COI, permit, contract, w9, license, invoice, photo, sketch, etc.
  - entity        : Who the doc is about/for (AHBCO, client name, vendor)
  - doc_date      : When the doc is dated
  - summary       : 1-3 sentence plain-English summary
  - relevance     : How it fits into Baza Empire / AHB123 operations
  - tags          : 3-8 keywords for search
  - suggested_name: Clean filename in the form `YYYY-MM-DD_entity_doctype.ext`

Writes the result to:
  1. The file's sidecar `.meta` (extending whatever's there)
  2. The new `ahb_documents` table in dashboard/baza_projects.db
  3. Stdout as JSON for the calling agent

SKILL_ARGS:
  file_path: "/abs/path/to/file"
  agent_id : "phil_hass"   (optional — for attribution)
  chat_id  : 12345         (optional)
"""
import os
import sys
import json
import re
import sqlite3
import datetime
import urllib.request
import base64

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
file_path = args.get("file_path", "")
agent_id  = args.get("agent_id", "")
chat_id   = args.get("chat_id", "")

if not file_path or not os.path.exists(file_path):
    print(json.dumps({"error": f"file not found: {file_path}"}))
    sys.exit(1)

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DASHBOARD_DB  = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")

OLLAMA_URL    = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
TEXT_MODEL    = os.environ.get("CURATE_TEXT_MODEL",  "qwen2.5:14b")
VISION_MODEL  = os.environ.get("CURATE_VISION_MODEL", "qwen3-vl:latest")
LITELLM_URL   = os.environ.get("LITELLM_URL", "http://localhost:4000")
LITELLM_KEY   = os.environ.get("LITELLM_KEY", "baza-litellm")

# ─────────────────────────────────────────────────────────────────────────────
# Document table init (idempotent)
# ─────────────────────────────────────────────────────────────────────────────

def _init_table():
    conn = sqlite3.connect(DASHBOARD_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ahb_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL UNIQUE,
            original_name TEXT,
            suggested_name TEXT,
            doc_type TEXT,
            entity TEXT,
            doc_date TEXT,
            summary TEXT,
            relevance TEXT,
            tags TEXT,
            confidence REAL,
            agent_id TEXT,
            chat_id TEXT,
            project_id TEXT,
            content_text TEXT,
            file_size INTEGER,
            file_kind TEXT,
            curated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
_init_table()


# ─────────────────────────────────────────────────────────────────────────────
# Text extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_text(path: str) -> tuple[str, str]:
    """Return (extracted_text, file_kind)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            try:
                import pdfplumber
                txt = []
                with pdfplumber.open(path) as pdf:
                    for page in pdf.pages[:30]:
                        t = page.extract_text() or ""
                        if t.strip():
                            txt.append(t)
                return "\n".join(txt)[:18000], "pdf"
            except Exception as e:
                return f"[pdf extract error: {e}]", "pdf"
        if ext == ".docx":
            try:
                import docx
                d = docx.Document(path)
                return "\n".join(p.text for p in d.paragraphs)[:18000], "docx"
            except Exception as e:
                return f"[docx extract error: {e}]", "docx"
        if ext in (".txt", ".md", ".rtf", ".csv", ".html", ".xml", ".log"):
            with open(path, "r", errors="ignore") as f:
                return f.read()[:18000], "text"
        if ext in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff"):
            return "", "image"
        return "", "binary"
    except Exception as e:
        return f"[extract error: {e}]", "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Vision analysis (for images)
# ─────────────────────────────────────────────────────────────────────────────

def analyze_image(path: str) -> str:
    """Send image to vision LLM for description. Tries Ollama first, then LiteLLM."""
    prompt = (
        "Look at this image carefully. This was sent to a contractor's business assistant. "
        "Describe in detail: what is this? If it's a document (certificate, license, permit, "
        "invoice, contract, ID, receipt), read all visible text including company names, dates, "
        "policy numbers, addresses, dollar amounts, signatures. If it's a jobsite photo, describe "
        "the scene, what work is happening or has been done, materials visible, condition. "
        "Be exhaustive — every readable detail matters."
    )
    # Try Ollama vision first (local, free)
    try:
        with open(path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        payload = json.dumps({
            "model": VISION_MODEL,
            "prompt": prompt,
            "images": [img_b64],
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 1500}
        }).encode()
        req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
        return data.get("response", "").strip()
    except Exception as e:
        pass
    # Fallback: LiteLLM proxy (cloud vision)
    try:
        with open(path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        payload = json.dumps({
            "model": "gpt-4o-mini",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                ]
            }],
            "max_tokens": 1500,
        }).encode()
        req = urllib.request.Request(
            f"{LITELLM_URL}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {LITELLM_KEY}"}
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[vision unavailable: {e}]"


# ─────────────────────────────────────────────────────────────────────────────
# LLM curator — extracts structured fields
# ─────────────────────────────────────────────────────────────────────────────

CURATOR_PROMPT = """You are Phil Hass, the document curator for All Home Building Co LLC (AHBCO),
a Philadelphia residential general contractor. A new file just arrived. Your job is to identify it
and extract structured metadata so it can be filed in the company's Document Library.

Known context:
- AHBCO is owned by Sergey Tkach (Serge). Phone 800-484-6404. Address: 2725 Colmar Ave, Bensalem PA.
- Common doc types we receive: Certificate of Insurance (COI), W9, contractor license,
  building permit, contract, change order, invoice, estimate, lien waiver, lead form, project
  photos (before/during/after), client correspondence, vendor receipts, plans/blueprints.
- Common entities: AHBCO itself, our clients (homeowners), vendors (suppliers, subcontractors),
  insurance carriers, government agencies (PA L&I, Philadelphia DLI).

Read the document content below and return ONLY a JSON object with these EXACT keys
(use null for unknown fields):

{
  "doc_type": "one of: coi, w9, license, permit, contract, change_order, invoice, estimate, lien_waiver, lead_form, project_photo, blueprint, receipt, correspondence, id_document, tax_document, other",
  "entity": "primary entity this doc is for or about (e.g. 'AHBCO', 'John Smith — 123 Main St', 'Liberty Mutual', 'City of Philadelphia')",
  "doc_date": "YYYY-MM-DD if visible, else null",
  "summary": "1-3 sentence plain-English summary of what this document is and says",
  "relevance": "1-2 sentence explanation of why this matters to Baza/AHBCO operations and which agent should care most",
  "tags": ["3-8", "lowercase", "search", "keywords"],
  "suggested_name": "clean filename in format YYYY-MM-DD_entity_doctype.ext (use 'unknown' for missing date, snake_case_entity, .ext from original)",
  "project_hint": "if this doc obviously belongs to a known project, the project name or address, else null",
  "confidence": 0.0-1.0
}

Document content:
"""

def curate_with_llm(text: str, original_name: str, file_kind: str) -> dict:
    """Run the curator prompt against the local text model and parse JSON."""
    full_prompt = CURATOR_PROMPT + f"\n\nOriginal filename: {original_name}\nFile kind: {file_kind}\n\n{text[:14000]}\n\nJSON:"
    try:
        payload = json.dumps({
            "model": TEXT_MODEL,
            "prompt": full_prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 800}
        }).encode()
        req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
        raw = data.get("response", "").strip()
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
        result = json.loads(raw)
        # Sanitize tags
        if isinstance(result.get("tags"), str):
            result["tags"] = [t.strip() for t in result["tags"].split(",") if t.strip()]
        return result
    except Exception as e:
        return {"error": str(e), "doc_type": "other", "entity": None,
                "doc_date": None, "summary": text[:200] if text else "(no analysis)",
                "relevance": None, "tags": [], "suggested_name": original_name,
                "confidence": 0.0}


# ─────────────────────────────────────────────────────────────────────────────
# Main flow
# ─────────────────────────────────────────────────────────────────────────────

original_name = os.path.basename(file_path)
ext = os.path.splitext(original_name)[1].lower()
file_size = os.path.getsize(file_path)

# 1. Extract content (text or vision description)
content_text, file_kind = extract_text(file_path)
if file_kind == "image":
    content_text = analyze_image(file_path)

# 2. Curate via LLM
analysis = curate_with_llm(content_text, original_name, file_kind)

# 3. Sanitize suggested_name (preserve original ext)
suggested = analysis.get("suggested_name", "")
if suggested:
    base, sgg_ext = os.path.splitext(suggested)
    if sgg_ext.lower() != ext.lower():
        suggested = base + ext
    suggested = re.sub(r'[^\w.\-_]', '_', suggested)
analysis["suggested_name"] = suggested or original_name

# 4. Persist to ahb_documents (idempotent on file_path)
try:
    conn = sqlite3.connect(DASHBOARD_DB)
    conn.execute("""
        INSERT INTO ahb_documents
            (file_path, original_name, suggested_name, doc_type, entity, doc_date,
             summary, relevance, tags, confidence, agent_id, chat_id, content_text,
             file_size, file_kind)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(file_path) DO UPDATE SET
            suggested_name=excluded.suggested_name,
            doc_type=excluded.doc_type,
            entity=excluded.entity,
            doc_date=excluded.doc_date,
            summary=excluded.summary,
            relevance=excluded.relevance,
            tags=excluded.tags,
            confidence=excluded.confidence,
            agent_id=excluded.agent_id,
            content_text=excluded.content_text,
            curated_at=CURRENT_TIMESTAMP
    """, (
        file_path, original_name, analysis["suggested_name"],
        analysis.get("doc_type"), analysis.get("entity"),
        analysis.get("doc_date"), analysis.get("summary"),
        analysis.get("relevance"),
        json.dumps(analysis.get("tags") or []),
        float(analysis.get("confidence") or 0),
        agent_id, str(chat_id), content_text[:8000],
        file_size, file_kind,
    ))
    conn.commit()
    conn.close()
except Exception as e:
    analysis["_db_error"] = str(e)

# 5. Update sidecar meta with curated fields
try:
    meta_path = file_path + ".meta"
    existing = ""
    if os.path.exists(meta_path):
        with open(meta_path, "r") as f:
            existing = f.read()
    new_meta = existing + (
        f"\n# Curated by Phil at {datetime.datetime.now().isoformat()}\n"
        f"doc_type={analysis.get('doc_type')}\n"
        f"entity={analysis.get('entity')}\n"
        f"doc_date={analysis.get('doc_date')}\n"
        f"summary={(analysis.get('summary') or '').replace(chr(10),' ')[:500]}\n"
        f"tags={','.join(analysis.get('tags') or [])}\n"
        f"suggested_name={analysis.get('suggested_name')}\n"
    )
    with open(meta_path, "w") as f:
        f.write(new_meta)
except Exception:
    pass

print(json.dumps(analysis, indent=2))
