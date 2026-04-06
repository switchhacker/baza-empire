#!/usr/bin/env python3
"""
Baza Empire Skill — print_document
Send files, images, generated text, or artifacts to the HP Smart Tank 5101 printer.

SKILL_ARGS:
  file_path    (str)  — absolute path to file to print (PDF, image, text, docx, etc.)
  artifact     (str)  — artifact filename to find and print (searches dashboard/artifacts/)
  text         (str)  — raw text to print directly (creates a temp PDF)
  title        (str)  — title for text-based prints (default: "Print Job")
  copies       (int)  — number of copies (default: 1)
  color        (bool) — color print (default: true, false = grayscale)
  duplex       (bool) — double-sided (default: false)
  paper_size   (str)  — Letter, A4, Legal (default: Letter)
  quality      (str)  — Draft, Normal, High (default: Normal)
  fit_to_page  (bool) — scale content to fit page (default: true)
  orientation  (str)  — portrait, landscape (default: portrait)
  pages        (str)  — page range e.g. "1-3" or "1,3,5" (default: all)
  action       (str)  — "print" (default), "status", "queue", "cancel"
  job_id       (int)  — job ID for cancel action

Examples:
  ##SKILL:print_document{"file_path":"/path/to/invoice.pdf"}##
  ##SKILL:print_document{"artifact":"contract_smith.pdf","project_id":"proj-ahb123"}##
  ##SKILL:print_document{"text":"Meeting notes:\\n- Budget approved\\n- Start date March 15","title":"Meeting Notes"}##
  ##SKILL:print_document{"action":"status"}##
  ##SKILL:print_document{"action":"queue"}##
  ##SKILL:print_document{"action":"cancel","job_id":123}##
"""
import os
import sys
import json
import subprocess
import tempfile
import glob as globmod

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))

action = args.get("action", "print")
PRINTER_NAME = os.environ.get("BAZA_PRINTER", "HP_Smart_Tank_5101")
ARTIFACTS_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "dashboard", "artifacts")

# ── Helper: find printer name dynamically ────────────────────────────────────

def find_printer():
    """Find the HP printer name from CUPS. Prefer the direct USB printer over implicit class."""
    try:
        result = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=5)
        candidates = []
        for line in result.stdout.splitlines():
            if "HP" in line and ("Smart" in line or "5100" in line or "5101" in line):
                name = line.split()[1]
                candidates.append(name)
        # Prefer HP_Smart_Tank_5101 (direct HPLIP) over implicit class
        for c in candidates:
            if "5101" in c:
                return c
        # Fall back to any non-implicit-class candidate
        for c in candidates:
            if "implicit" not in c.lower() and "318079" not in c:
                return c
        if candidates:
            return candidates[0]
    except Exception:
        pass
    # Check system default
    try:
        result = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=5)
        if "destination:" in result.stdout:
            return result.stdout.split("destination:")[-1].strip()
    except Exception:
        pass
    return PRINTER_NAME


def get_printer_status():
    """Get printer status info."""
    printer = find_printer()
    info = {"printer": printer}
    try:
        result = subprocess.run(["lpstat", "-p", printer, "-l"], capture_output=True, text=True, timeout=5)
        info["status"] = result.stdout.strip() if result.stdout.strip() else "Unknown"
    except Exception as e:
        info["status"] = f"Error: {e}"
    try:
        result = subprocess.run(["lpstat", "-o", printer], capture_output=True, text=True, timeout=5)
        jobs = result.stdout.strip().splitlines() if result.stdout.strip() else []
        info["pending_jobs"] = len(jobs)
        info["jobs"] = jobs[:10]
    except Exception:
        info["pending_jobs"] = 0
        info["jobs"] = []
    return info


def get_queue():
    """Get all jobs in the print queue."""
    try:
        result = subprocess.run(["lpstat", "-o"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().splitlines() if result.stdout.strip() else []
        return {"jobs": lines, "count": len(lines)}
    except Exception as e:
        return {"error": str(e)}


def cancel_job(job_id=None):
    """Cancel a print job or all jobs."""
    try:
        if job_id:
            subprocess.run(["cancel", str(job_id)], capture_output=True, text=True, timeout=5)
            return {"cancelled": job_id}
        else:
            subprocess.run(["cancel", "-a"], capture_output=True, text=True, timeout=5)
            return {"cancelled": "all"}
    except Exception as e:
        return {"error": str(e)}


# ── Helper: resolve file to print ────────────────────────────────────────────

def resolve_file():
    """Find the file to print from various input methods."""
    # Direct file path
    file_path = args.get("file_path", "")
    if file_path:
        if os.path.exists(file_path):
            return file_path
        return None, f"File not found: {file_path}"

    # Artifact search
    artifact = args.get("artifact", "")
    if artifact:
        project_id = args.get("project_id", "")
        # Search in specific project or all projects
        search_dirs = []
        if project_id:
            search_dirs.append(os.path.join(ARTIFACTS_BASE, project_id))
        # Also search all project dirs
        if os.path.isdir(ARTIFACTS_BASE):
            for d in os.listdir(ARTIFACTS_BASE):
                full = os.path.join(ARTIFACTS_BASE, d)
                if os.path.isdir(full):
                    search_dirs.append(full)

        for d in search_dirs:
            # Exact match
            exact = os.path.join(d, artifact)
            if os.path.exists(exact):
                return exact
            # Glob match
            matches = globmod.glob(os.path.join(d, f"*{artifact}*"))
            if matches:
                return matches[0]

        return None, f"Artifact not found: {artifact}"

    # Raw text — generate a temp PDF
    text = args.get("text", "")
    if text:
        return text_to_pdf(text, args.get("title", "Print Job"))

    return None, "No file_path, artifact, or text provided"


def text_to_pdf(text, title="Print Job"):
    """Convert raw text to a printable PDF."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib import colors

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir="/tmp", prefix="baza_print_")
        doc = SimpleDocTemplate(tmp.name, pagesize=letter,
                                leftMargin=0.75*inch, rightMargin=0.75*inch,
                                topMargin=0.75*inch, bottomMargin=0.75*inch)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("PrintTitle", parent=styles["Heading1"],
                                     fontSize=16, spaceAfter=12, textColor=colors.HexColor("#1a1a2e"))
        body_style = ParagraphStyle("PrintBody", parent=styles["Normal"],
                                    fontSize=11, leading=15, spaceAfter=6)

        story = []
        story.append(Paragraph(title, title_style))
        story.append(Spacer(1, 12))

        # Handle line breaks in text
        for line in text.split("\n"):
            if line.strip():
                # Escape XML special chars
                safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(safe, body_style))
            else:
                story.append(Spacer(1, 8))

        doc.build(story)
        return tmp.name
    except ImportError:
        # Fallback: write as plain text file (lp can print .txt)
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, dir="/tmp",
                                          prefix="baza_print_", mode="w")
        tmp.write(f"{title}\n{'='*len(title)}\n\n{text}\n")
        tmp.close()
        return tmp.name


# ── Build lp command ─────────────────────────────────────────────────────────

def build_lp_command(file_path):
    """Build the lp command with all options."""
    printer = find_printer()
    cmd = ["lp", "-d", printer]

    copies = args.get("copies", 1)
    if copies > 1:
        cmd.extend(["-n", str(copies)])

    opts = []

    # Color mode
    if args.get("color", True) is False:
        opts.append("ColorModel=Gray")

    # Duplex
    if args.get("duplex", False):
        opts.append("sides=two-sided-long-edge")

    # Paper size
    paper = args.get("paper_size", "Letter")
    paper_map = {"letter": "Letter", "a4": "A4", "legal": "Legal"}
    opts.append(f"PageSize={paper_map.get(paper.lower(), paper)}")

    # Quality
    quality = args.get("quality", "Normal")
    quality_map = {"draft": "Draft", "normal": "Normal", "high": "High"}
    opts.append(f"cupsPrintQuality={quality_map.get(quality.lower(), quality)}")

    # Orientation
    orientation = args.get("orientation", "portrait")
    if orientation.lower() == "landscape":
        opts.append("orientation-requested=4")

    # Fit to page
    if args.get("fit_to_page", True):
        opts.append("fit-to-page=true")

    # Page range
    pages = args.get("pages", "")
    if pages:
        cmd.extend(["-P", str(pages)])

    for opt in opts:
        cmd.extend(["-o", opt])

    cmd.append(file_path)
    return cmd


# ── Main dispatch ────────────────────────────────────────────────────────────

if action == "status":
    info = get_printer_status()
    print(f"Printer: {info['printer']}")
    print(f"Status: {info['status']}")
    print(f"Pending jobs: {info['pending_jobs']}")
    if info['jobs']:
        print("Queue:")
        for j in info['jobs']:
            print(f"  {j}")
    print(json.dumps({"success": True, **info}))

elif action == "queue":
    q = get_queue()
    if "error" in q:
        print(json.dumps({"success": False, "error": q["error"]}))
        sys.exit(1)
    print(f"Print queue: {q['count']} job(s)")
    for j in q['jobs']:
        print(f"  {j}")
    print(json.dumps({"success": True, **q}))

elif action == "cancel":
    job_id = args.get("job_id")
    result = cancel_job(job_id)
    if "error" in result:
        print(json.dumps({"success": False, **result}))
        sys.exit(1)
    print(f"Cancelled: {result['cancelled']}")
    print(json.dumps({"success": True, **result}))

elif action == "print":
    resolved = resolve_file()
    if isinstance(resolved, tuple):
        _, error = resolved
        print(json.dumps({"success": False, "error": error}))
        sys.exit(1)

    file_path = resolved
    ext = os.path.splitext(file_path)[1].lower()

    # For images that lp might not handle well, convert via a known format
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif"}

    print(f"Printing: {os.path.basename(file_path)}")
    print(f"Type: {ext}")
    print(f"Size: {os.path.getsize(file_path)} bytes")

    cmd = build_lp_command(file_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            err = result.stderr.strip() or "Print command failed"
            print(f"Error: {err}")
            print(json.dumps({"success": False, "error": err, "command": " ".join(cmd)}))
            sys.exit(1)

        # Extract job ID from lp output
        output = result.stdout.strip()
        job_id = ""
        if "request id is" in output:
            job_id = output.split("request id is")[1].split()[0]

        print(f"Sent to printer: {find_printer()}")
        print(f"Job: {output}")
        copies = args.get("copies", 1)
        print(json.dumps({
            "success": True,
            "file": os.path.basename(file_path),
            "printer": find_printer(),
            "job_id": job_id,
            "copies": copies,
            "output": output,
        }))
    except subprocess.TimeoutExpired:
        print(json.dumps({"success": False, "error": "Print command timed out"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)

else:
    print(json.dumps({"success": False, "error": f"Unknown action: {action}"}))
    sys.exit(1)
