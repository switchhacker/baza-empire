#!/usr/bin/env python3
"""
Baza Empire — Skill Catalog

Discovers every shared + agent-local skill and returns a structured catalog
so any agent can learn what capabilities exist.

SKILL_ARGS:
  filter : "document"             (optional — substring match on name/doc)
  agent  : "phil_hass"            (optional — include agent-local skills of X)
  format : "json" | "markdown"    (default "json")

Returns: list of { name, path, scope, summary, args_hint } — ready to splice
into any agent's system prompt on demand.
"""
import os, sys, json, ast, re

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHARED = os.path.join(FRAMEWORK, "skills", "shared")
AGENTS_DIR = os.path.join(FRAMEWORK, "agents")


def _extract_docstring(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            src = f.read(8000)
    except Exception:
        return ""
    try:
        tree = ast.parse(src)
        doc = ast.get_docstring(tree)
        return (doc or "").strip()
    except SyntaxError:
        m = re.search(r'^\s*"""(.*?)"""', src, re.DOTALL | re.MULTILINE)
        return (m.group(1).strip() if m else "")


def _summary(doc: str) -> str:
    if not doc:
        return ""
    lines = [ln for ln in doc.splitlines() if ln.strip()]
    if not lines:
        return ""
    first = lines[0].strip()
    if first.startswith("Baza Empire"):
        first = lines[1].strip() if len(lines) > 1 else first
    return first[:200]


def _args_hint(doc: str) -> str:
    if not doc:
        return ""
    m = re.search(r'SKILL[_ ]ARGS\s*:?\s*\n?(.+?)(?:\n\n|\Z)', doc, re.DOTALL)
    if not m:
        return ""
    block = m.group(1).strip()
    return block[:400]


def _collect(dir_path: str, scope: str, owner: str = None):
    out = []
    if not os.path.isdir(dir_path):
        return out
    for name in sorted(os.listdir(dir_path)):
        if not (name.endswith(".py") or name.endswith(".sh")):
            continue
        if name.startswith("_"):
            continue
        path = os.path.join(dir_path, name)
        skill_name = name.rsplit(".", 1)[0]
        doc = _extract_docstring(path) if name.endswith(".py") else ""
        out.append({
            "name": skill_name,
            "path": path,
            "scope": scope,
            "owner": owner,
            "summary": _summary(doc),
            "args_hint": _args_hint(doc),
        })
    return out


def catalog(filter_str: str = "", include_agent: str = None):
    items = _collect(SHARED, "shared")
    if include_agent:
        items += _collect(os.path.join(AGENTS_DIR, include_agent, "skills"),
                          "agent-local", include_agent)
    else:
        # include all agent-local skills — any agent can now invoke any skill
        for agent_id in sorted(os.listdir(AGENTS_DIR)) if os.path.isdir(AGENTS_DIR) else []:
            agent_skills = os.path.join(AGENTS_DIR, agent_id, "skills")
            if os.path.isdir(agent_skills):
                items += _collect(agent_skills, "agent-local", agent_id)
    if filter_str:
        needle = filter_str.lower()
        items = [i for i in items if needle in i["name"].lower()
                 or needle in (i["summary"] or "").lower()]
    return items


def as_markdown(items):
    lines = [f"# Baza Empire Skill Catalog ({len(items)} skills)\n"]
    by_scope = {}
    for it in items:
        by_scope.setdefault(it["scope"], []).append(it)
    for scope in sorted(by_scope):
        lines.append(f"\n## {scope}\n")
        for it in by_scope[scope]:
            owner = f" — {it['owner']}" if it.get("owner") else ""
            lines.append(f"- **{it['name']}**{owner}: {it['summary'] or '(no docstring)'}")
    return "\n".join(lines)


if __name__ == "__main__":
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    items = catalog(
        filter_str=args.get("filter", ""),
        include_agent=args.get("agent"),
    )
    fmt = args.get("format", "json")
    if fmt == "markdown":
        print(as_markdown(items))
    else:
        print(json.dumps({"ok": True, "count": len(items), "skills": items}))
