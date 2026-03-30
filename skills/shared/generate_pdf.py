#!/usr/bin/env python3
"""
Baza Empire Skill — generate_pdf
Generate a professional PDF document.
Phil uses this for contracts, proposals, invoices, signed forms, reports.

SKILL_ARGS:
  title       (str)  — Document title
  sections    (list) — [{heading: str, body: str}, ...]
  filename    (str)  — output .pdf filename
  project_id  (str)  — dashboard project (default: shared)
  author      (str)  — shown in header (default: "Phil Hass — AHBCO LLC")
  footer_text (str)  — shown at bottom of each page
  table       (dict) — {headers: [...], rows: [[...], ...]}
  logo_text   (str)  — company name in header (default: "ALL HOME BUILDING CO LLC")
"""
import os
import sys
import json
import re
import time
import urllib.request
from datetime import date

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, PageBreak
    )
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
except ImportError:
    print(json.dumps({"error": "reportlab not installed. Run: venv/bin/pip install reportlab"}))
    sys.exit(1)

DASHBOARD_URL = os.environ.get("BAZA_DASHBOARD_URL", "http://localhost:8888")
ARTIFACTS_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "dashboard", "artifacts"
)

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))

title       = args.get("title", "Document")
sections    = args.get("sections", [])
author      = args.get("author", "Phil Hass — All Home Building Co LLC")
footer_text = args.get("footer_text", f"All Home Building Co LLC  |  ahb123.com  |  Philadelphia, PA")
table_data  = args.get("table", None)
project_id  = args.get("project_id", "shared")
logo_text   = args.get("logo_text", "ALL HOME BUILDING CO LLC")

raw_name = args.get("filename", "")
if not raw_name:
    safe = re.sub(r'[^\w]', '_', title.lower())[:40]
    raw_name = f"{safe}_{int(time.time())}.pdf"
if not raw_name.endswith(".pdf"):
    raw_name += ".pdf"

out_dir  = os.path.join(ARTIFACTS_BASE, project_id)
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, raw_name)

# ── Styles ────────────────────────────────────────────────────────────────────
NAVY  = colors.HexColor("#1F4E79")
GRAY  = colors.HexColor("#666666")
BLACK = colors.black
WHITE = colors.white

styles = getSampleStyleSheet()
style_title    = ParagraphStyle("DocTitle",    parent=styles["Title"],   fontSize=20, textColor=NAVY,  spaceAfter=4)
style_author   = ParagraphStyle("DocAuthor",   parent=styles["Normal"],  fontSize=10, textColor=GRAY,  spaceAfter=2, alignment=TA_CENTER)
style_h1       = ParagraphStyle("H1",          parent=styles["Heading1"],fontSize=13, textColor=NAVY,  spaceBefore=14, spaceAfter=4)
style_body     = ParagraphStyle("Body",        parent=styles["Normal"],  fontSize=10, textColor=BLACK, spaceAfter=4, leading=14)
style_bullet   = ParagraphStyle("Bullet",      parent=styles["Normal"],  fontSize=10, leftIndent=16,   bulletIndent=6, spaceAfter=2)
style_footer   = ParagraphStyle("Footer",      parent=styles["Normal"],  fontSize=8,  textColor=GRAY,  alignment=TA_CENTER)


def _make_header_footer(canvas, doc):
    """Draw header bar and footer on every page."""
    canvas.saveState()
    w, h = letter
    # Header bar
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 0.6*inch, w, 0.6*inch, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(0.5*inch, h - 0.4*inch, logo_text)
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(w - 0.5*inch, h - 0.4*inch, date.today().strftime("%B %d, %Y"))
    # Footer
    canvas.setFillColor(GRAY)
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(w / 2, 0.35*inch, footer_text)
    canvas.drawRightString(w - 0.5*inch, 0.35*inch, f"Page {doc.page}")
    canvas.restoreState()


doc = SimpleDocTemplate(
    out_path,
    pagesize=letter,
    rightMargin=0.75*inch, leftMargin=0.75*inch,
    topMargin=1.1*inch, bottomMargin=0.7*inch,
)

story = []

# ── Title + Author ────────────────────────────────────────────────────────────
story.append(Paragraph(title, style_title))
story.append(Paragraph(author, style_author))
story.append(HRFlowable(width="100%", thickness=1, color=NAVY, spaceAfter=12))

# ── Sections ──────────────────────────────────────────────────────────────────
for sec in sections:
    heading = sec.get("heading", "")
    body    = sec.get("body", "")
    if heading:
        story.append(Paragraph(heading, style_h1))
    if body:
        for line in body.strip().split("\n"):
            line = line.strip()
            if not line:
                story.append(Spacer(1, 6))
                continue
            if line.startswith(("-", "•", "*")):
                story.append(Paragraph(f"• {line.lstrip('-•* ')}", style_bullet))
            else:
                story.append(Paragraph(line, style_body))

# ── Optional table ────────────────────────────────────────────────────────────
if table_data:
    headers = table_data.get("headers", [])
    rows    = table_data.get("rows", [])
    if headers and rows:
        story.append(Spacer(1, 12))
        table_data_formatted = [headers] + rows
        col_width = (6.5 * inch) / max(len(headers), 1)
        t = Table(table_data_formatted, colWidths=[col_width] * len(headers), repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0),  NAVY),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  WHITE),
            ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, 0),  10),
            ("ALIGN",        (0, 0), (-1, -1), "LEFT"),
            ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE",     (0, 1), (-1, -1), 9),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.HexColor("#EBF3FF"), WHITE]),
            ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]))
        story.append(t)
        story.append(Spacer(1, 12))

doc.build(story, onFirstPage=_make_header_footer, onLaterPages=_make_header_footer)

# Register with dashboard
try:
    boundary = "bazapdfboundary"
    with open(out_path, "rb") as f:
        file_data = f.read()
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"project_id\"\r\n\r\n{project_id}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{raw_name}\"\r\n"
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{DASHBOARD_URL}/api/artifacts/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15):
        pass
except Exception:
    pass

print(json.dumps({
    "success": True,
    "filename": raw_name,
    "path": out_path,
    "project_id": project_id,
    "download_url": f"{DASHBOARD_URL}/api/artifacts/download/{project_id}/{raw_name}",
}))
