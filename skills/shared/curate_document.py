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

def _top_vendor_hint(limit: int = 10) -> str:
    """Pull the most common vendors we've filed so the vision model can
    recognize them even through OCR noise. Cached via function default isn't
    worth it here — this runs once per image."""
    try:
        import sqlite3 as _sq
        conn = _sq.connect(DASHBOARD_DB)
        rows = conn.execute(
            """SELECT COALESCE(NULLIF(store_name,''), vendor) AS n, COUNT(*) c
                 FROM ahb_receipts
                WHERE COALESCE(NULLIF(store_name,''), vendor) IS NOT NULL
                  AND COALESCE(NULLIF(store_name,''), vendor) != ''
                GROUP BY LOWER(COALESCE(NULLIF(store_name,''), vendor))
                ORDER BY c DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        names = [r[0] for r in rows if r[0]]
        if names:
            return ", ".join(names)
    except Exception:
        pass
    return "The Home Depot, Lowe's, Sherwin-Williams, Harbor Freight, Wawa, Exxon, Redner's, Dunkin', Sheetz"


def _tesseract_text(path: str) -> str:
    """Run Tesseract OCR and return raw text, empty on failure.
    Belt-and-suspenders for the vision LLM: qwen3-vl has failed on clear
    receipts before by returning 'a photo of a document' with no transcription.
    Appending Tesseract's literal text ensures the curator + safety net always
    see real OCR signals if they exist."""
    try:
        import pytesseract
        from PIL import Image, ImageFilter, ImageEnhance
        img = Image.open(path).convert("L")
        img = ImageEnhance.Contrast(img).enhance(2.0)
        img = img.filter(ImageFilter.SHARPEN)
        txt = pytesseract.image_to_string(img, config="--psm 6").strip()
        return txt
    except Exception:
        return ""


def analyze_image(path: str) -> str:
    """Send image to vision LLM for description + run Tesseract in parallel.
    Returns `<vision text>\\n\\n── OCR (tesseract) ──\\n<ocr text>` so both
    signals reach the curator. The curator's classification rules + safety
    net scan the combined text for receipt markers.

    Receipt-first: if ANY two receipt signals are visible (store name, priced
    items, TOTAL line, tax, tender, store/register number, thermal font,
    date printed), the model MUST treat it as a receipt and transcribe every
    line top to bottom. Only images with zero receipt signals should be
    described as jobsite photos."""
    vendor_hint = _top_vendor_hint()
    prompt = (
        "You are reading an image sent to a contractor's business inbox. First, "
        "scan for RECEIPT signals: (1) a store/vendor name at the top, (2) a list "
        "of items with prices, (3) a TOTAL or AMOUNT DUE line, (4) tax line, "
        "(5) tender/card/cash line, (6) date printed on the paper, (7) store or "
        "register number, (8) thermal-paper printed text. If ANY TWO of these "
        "are present, this IS a receipt. Do NOT describe it as a photo.\n\n"
        f"Vendors we often see (OCR typos expected): {vendor_hint}. If the "
        "store matches one of these even through noise, use its canonical name.\n\n"
        "If this IS a receipt, transcribe EVERY visible text line top-to-bottom "
        "— store name, address, cashier/teller name, items with prices, subtotal, "
        "tax, total, payment method, last 4 digits, date, time. Be exhaustive.\n\n"
        "If zero receipt signals: describe what you see (document / jobsite photo / "
        "ID / plan / other) with the same exhaustive detail.\n\n"
        "Never return a one-sentence generic description — every readable detail matters."
    )
    # Kick off Tesseract in a background thread so it runs alongside vision.
    import threading
    tesseract_out = {"text": ""}
    def _run_tess():
        tesseract_out["text"] = _tesseract_text(path)
    t = threading.Thread(target=_run_tess, daemon=True)
    t.start()

    vision_text = ""
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
        vision_text = (data.get("response") or "").strip()
    except Exception:
        pass
    # Fallback: LiteLLM proxy (cloud vision) if Ollama vision was empty
    if not vision_text:
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
            vision_text = (data["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            pass

    # Wait up to 60s for Tesseract to finish; merge both signals so the curator
    # (and receipt-signal safety net) see actual OCR text even if the vision
    # model hallucinated a "document photo" description.
    t.join(timeout=60)
    tess_text = tesseract_out.get("text") or ""

    parts = []
    if vision_text:
        parts.append(vision_text)
    if tess_text and len(tess_text) > 20:
        parts.append("── OCR (tesseract) ──\n" + tess_text)
    if not parts:
        return "[vision returned nothing and tesseract OCR failed]"
    return "\n\n".join(parts)


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
  photos (before/during/after), client correspondence, vendor RECEIPTS, plans/blueprints.
- Common entities: AHBCO itself, our clients (homeowners), vendors (suppliers, subcontractors),
  insurance carriers, government agencies (PA L&I, Philadelphia DLI).

CLASSIFICATION RULES (apply in order, first match wins):
1. If the content contains ANY dollar amount paired with an itemized list (prices + item names),
   OR a "TOTAL" / "AMOUNT DUE" line, OR a store register number / thermal-receipt formatting:
   doc_type = "receipt". Set entity to the STORE NAME (not our company or the client).
   doc_date must be the date printed ON the receipt (the purchase date), NOT today.
2. If it's a certificate with policy number + insurance carrier: coi.
3. If it's a tax form with SSN/EIN and boxes labeled 1-9: w9.
4. If it's a government-issued permit / license number: permit or license.
5. If it clearly describes a jobsite / work-in-progress / condition with NO dollar amounts and
   NO itemized price lines: project_photo.
6. Otherwise use the closest match from the allowed set.

NEVER default to project_photo just because content is short. If in doubt between receipt and
project_photo, pick receipt.

Read the document content below and return ONLY a JSON object with these EXACT keys
(use null for unknown fields):

{
  "doc_type": "one of: coi, w9, license, permit, contract, change_order, invoice, estimate, lien_waiver, lead_form, project_photo, blueprint, receipt, correspondence, id_document, tax_document, other",
  "entity": "primary entity this doc is for or about (e.g. 'The Home Depot', 'John Smith — 123 Main St', 'Liberty Mutual', 'City of Philadelphia'). For receipts, this is the STORE name.",
  "doc_date": "YYYY-MM-DD if visible (for receipts: the date on the receipt, NOT today), else null",
  "summary": "1-3 sentence plain-English summary of what this document is and says",
  "relevance": "1-2 sentence explanation of why this matters to Baza/AHBCO operations and which agent should care most",
  "tags": ["3-8", "lowercase", "search", "keywords"],
  "suggested_name": "clean filename in format YYYY-MM-DD_entity_doctype.ext (use 'unknown' for missing date, snake_case_entity, .ext from original)",
  "project_hint": "if this doc obviously belongs to a known project, the project name or address, else null",
  "confidence": 0.0-1.0
}

Document content:
"""


# Receipt-signal regex — used in the post-parse safety net. If the curator
# returns project_photo but the extracted text clearly contains receipt
# signals, flip the classification.
_RECEIPT_SIGNAL_RX = re.compile(
    r'(\btotal\b[^\n]{0,40}\$?\s*\d+\.\d{2})|'
    r'(\bsubtotal\b)|'
    r'(\bamount\s*due\b)|'
    r'(\bbalance\s*due\b)|'
    r'(\btax\b[^\n]{0,30}\$?\s*\d+\.\d{2})|'
    r'(\b(?:visa|mastercard|debit|credit|amex)\b[^\n]{0,20}\d{4})|'
    r'(\breceipt\b)|'
    r'(\b\d+\.\d{2,3}\s*gal(?:lons?)?\b)',
    re.I,
)


def _has_receipt_signals(text: str) -> bool:
    """Return True if the extracted content clearly shows receipt markers.
    Used to override the curator when it weakly guesses project_photo."""
    if not text:
        return False
    hits = 0
    seen = set()
    for m in _RECEIPT_SIGNAL_RX.finditer(text):
        for i, g in enumerate(m.groups()):
            if g and i not in seen:
                hits += 1
                seen.add(i)
    return hits >= 2

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

# 2a. Normalize doc_type — LLMs sometimes invent values like "document" that
# aren't in our allowed enum. Force anything off-list back to "other" so the
# downstream dispatcher and safety net behave predictably.
ALLOWED_DOC_TYPES = {
    "coi", "w9", "license", "permit", "contract", "change_order",
    "invoice", "estimate", "lien_waiver", "lead_form", "project_photo",
    "blueprint", "receipt", "correspondence", "id_document",
    "tax_document", "other",
}
dt = (analysis.get("doc_type") or "").strip().lower()
if dt not in ALLOWED_DOC_TYPES:
    analysis["doc_type"] = "other"
    dt = "other"

# 2b. Safety net: curator sometimes classifies clear receipts as project_photo
# / other / document / correspondence when the vision text is thin. If the
# extracted content shows two+ receipt signals (TOTAL/SUBTOTAL/AMOUNT DUE/tax
# line/card-tender/gallons) flip to receipt. Lower confidence so Serge knows
# it's a soft override.
_FLIPPABLE = {"project_photo", "other", "correspondence", None, ""}
if (dt in _FLIPPABLE) and _has_receipt_signals(content_text or ""):
    analysis["doc_type"] = "receipt"
    try:
        analysis["confidence"] = min(float(analysis.get("confidence") or 0.0), 0.6)
    except Exception:
        analysis["confidence"] = 0.6
    if not (analysis.get("tags") or []):
        analysis["tags"] = ["receipt", "auto-reclassified"]
    else:
        analysis["tags"] = list(analysis["tags"]) + ["auto-reclassified"]

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
