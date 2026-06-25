"""Skill registry — scans skill files (and tool endpoints) into a searchable
manifest. Metadata is read STATICALLY with ast (never importing/executing the
skill, which runs as a subprocess). Skills without a SKILL_META literal are
auto-described from docstring + filename + an inferred category."""
import ast
import os
import re

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED_DIR = os.path.join(FRAMEWORK, "skills", "shared")
AGENTS_DIR = os.path.join(FRAMEWORK, "agents")

# name-prefix / keyword → category. First match wins (ordered).
_CATEGORY_RULES = [
    ("financial",       ("invoice", "payroll", "tax", "profit", "margin", "cash",
                         "payment", "bid", "estimate", "pricing", "overhead", "roi",
                         "retainage", "depreciation", "loan", "kpi")),
    ("materials",       ("concrete", "drywall", "flooring", "lumber", "paint", "tile",
                         "hvac", "electrical", "plumbing", "roof", "staircase",
                         "cabinet", "door", "window", "material", "calculator")),
    ("project",         ("project", "timeline", "scope", "punch", "change_order",
                         "field_log", "progress", "milestone", "schedule")),
    ("client",          ("client", "onboard", "follow_up", "followup", "referral",
                         "survey", "warranty", "complaint", "lead")),
    ("marketing",       ("flyer", "social", "media_kit", "brand", "campaign",
                         "showcase", "ad_")),
    ("infrastructure",  ("system", "disk", "memory", "gpu", "network", "docker",
                         "service", "backup", "ssl", "port", "speedtest", "log",
                         "process", "deploy", "health")),
    ("data",            ("file", "csv", "json", "hash", "archive", "zip", "convert",
                         "integrity")),
    ("code",            ("git", "diff", "lint", "format", "regex", "test_", "repo")),
    ("ai",              ("ocr", "classify", "sentiment", "entity", "summar",
                         "translate", "caption", "vision", "image", "knowledge")),
    ("web",             ("web_", "scrape", "fetch", "search", "headers")),
    ("document",        ("pdf", "docx", "xlsx", "markdown", "html", "print", "proof")),
]


def infer_category(name: str) -> str:
    low = name.lower()
    for cat, kws in _CATEGORY_RULES:
        if any(kw in low for kw in kws):
            return cat
    return "general"


def extract_meta(path: str) -> dict | None:
    """Return the SKILL_META dict if the file declares one as a literal, else None."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            src = f.read(20000)
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "SKILL_META":
                    try:
                        val = ast.literal_eval(node.value)
                        return val if isinstance(val, dict) else None
                    except (ValueError, TypeError):
                        return None
    return None


def _first_docline(path: str) -> str:
    src = ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            src = f.read(8000)
        doc = ast.get_docstring(ast.parse(src))
        if doc:
            return doc.strip().splitlines()[0].strip()
    except (OSError, SyntaxError):
        pass
    # fallback: first non-shebang comment
    try:
        for line in src.splitlines():
            s = line.strip()
            if s.startswith("#") and not s.startswith("#!"):
                return s.lstrip("# ").strip()
    except Exception:
        pass
    return ""


def describe_skill(path: str, scope: str) -> dict:
    name = os.path.splitext(os.path.basename(path))[0]
    meta = extract_meta(path)
    if meta:
        return {
            "name": name,
            "type": "skill",
            "scope": scope,
            "category": meta.get("category") or infer_category(name),
            "summary": meta.get("summary", "") or _first_docline(path),
            "when_to_use": meta.get("when_to_use", ""),
            "args": meta.get("args", {}),
            "source_path": path,
        }
    return {
        "name": name,
        "type": "skill",
        "scope": scope,
        "category": infer_category(name),
        "summary": _first_docline(path),
        "when_to_use": "",
        "args": {},
        "source_path": path,
    }
