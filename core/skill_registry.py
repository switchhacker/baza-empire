"""Skill registry — scans skill files (and tool endpoints) into a searchable
manifest. Metadata is read STATICALLY with ast (never importing/executing the
skill, which runs as a subprocess). Skills without a SKILL_META literal are
auto-described from docstring + filename + an inferred category."""
import ast
import json
import os
import re
import sqlite3

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


DEFAULT_JSON = os.path.join(FRAMEWORK, "dashboard", "skills_manifest.json")
DEFAULT_DB = os.path.join(FRAMEWORK, "dashboard", "skills_manifest.db")
_EXCLUDE_NAMES = {"__init__", "skill_registry"}


def _iter_skill_files(shared_dir: str, agents_dir: str):
    if os.path.isdir(shared_dir):
        for fn in sorted(os.listdir(shared_dir)):
            if fn.endswith(".py") and os.path.splitext(fn)[0] not in _EXCLUDE_NAMES:
                yield os.path.join(shared_dir, fn), "shared"
    if os.path.isdir(agents_dir):
        for agent in sorted(os.listdir(agents_dir)):
            sk = os.path.join(agents_dir, agent, "skills")
            if os.path.isdir(sk):
                for fn in sorted(os.listdir(sk)):
                    if fn.endswith(".py") and os.path.splitext(fn)[0] not in _EXCLUDE_NAMES:
                        yield os.path.join(sk, fn), f"agent:{agent}"


def _write_fts(db_path: str, descriptors: list[dict]):
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    con.execute("CREATE VIRTUAL TABLE skills_fts USING fts5("
                "name, summary, when_to_use, category, type, "
                "scope UNINDEXED, source_path UNINDEXED)")
    con.executemany(
        "INSERT INTO skills_fts (name, summary, when_to_use, category, type, scope, source_path) "
        "VALUES (?,?,?,?,?,?,?)",
        [(d["name"], d.get("summary", ""), d.get("when_to_use", ""), d.get("category", ""),
          d.get("type", "skill"), d.get("scope", ""), d.get("source_path", ""))
         for d in descriptors])
    con.commit()
    con.close()


def build(shared_dir: str = SHARED_DIR, agents_dir: str = AGENTS_DIR,
          out_json: str = DEFAULT_JSON, out_db: str = DEFAULT_DB,
          tools=None) -> int:
    """Scan skills (+ optional tool registry dict) → manifest.json + FTS5 db.
    Returns the number of descriptors written."""
    descriptors = [describe_skill(path, scope) for path, scope in
                   _iter_skill_files(shared_dir, agents_dir)]
    if tools and "tool_descriptors" in globals():
        descriptors.extend(tool_descriptors(tools))   # defined in the tool-ingestion task
    with open(out_json, "w") as f:
        json.dump({"skills": descriptors}, f, indent=2)
    _write_fts(out_db, descriptors)
    return len(descriptors)


_FTS_SAFE = re.compile(r"[^a-zA-Z0-9_]+")


def _fts_query(raw: str) -> str:
    # Quote each term so FTS5 reserved words (AND/OR/NOT) are treated as literals,
    # not operators. _FTS_SAFE already stripped quotes, so terms are safe to wrap.
    terms = [f'"{t}"' for t in _FTS_SAFE.sub(" ", raw).split() if t]
    return " OR ".join(terms) if terms else '""'


def search(query: str, db_path: str = DEFAULT_DB, top_k: int = 8) -> list[dict]:
    if not os.path.exists(db_path):
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT name, summary, when_to_use, category, type, scope, source_path "
            "FROM skills_fts WHERE skills_fts MATCH ? ORDER BY rank LIMIT ?",
            (_fts_query(query), top_k)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    return [dict(r) for r in rows]


def categories(json_path: str = DEFAULT_JSON) -> dict[str, int]:
    try:
        with open(json_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, int] = {}
    for d in data.get("skills", []):
        out[d.get("category", "general")] = out.get(d.get("category", "general"), 0) + 1
    return out


def get(name: str, json_path: str = DEFAULT_JSON) -> dict | None:
    try:
        with open(json_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    for d in data.get("skills", []):
        if d["name"] == name:
            return d
    return None


def tool_descriptors(tools: dict) -> list[dict]:
    """Convert a tool-server registry dict {agent: [tool,...]} into manifest
    descriptors of type 'tool'. These are invoked from an agent via the
    call_tool bridge skill."""
    out = []
    for agent, tool_list in (tools or {}).items():
        for tool in tool_list:
            name = f"{agent}/{tool}"
            out.append({
                "name": name,
                "type": "tool",
                "scope": "tool-server",
                "category": infer_category(f"{agent} {tool}"),
                "summary": f"Tool-server endpoint {name}.",
                "when_to_use": f"Invoke via call_tool with agent={agent}, tool={tool}.",
                "args": {"agent": agent, "tool": tool, "input": "dict of tool inputs"},
                "source_path": "tool-server",
            })
    return out


if __name__ == "__main__":
    import sys
    if "--build" in sys.argv:
        tool_dict = None
        try:
            import httpx
            tool_dict = httpx.get("http://localhost:8000/tools", timeout=3).json()
        except Exception:
            tool_dict = None   # tool server optional at build time
        count = build(tools=tool_dict)
        print(f"Built manifest: {count} descriptors → {DEFAULT_JSON}")
