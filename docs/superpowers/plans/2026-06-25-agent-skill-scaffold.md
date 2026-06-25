# Agent Skill Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all 270 skills + ~100 tool-server endpoints discoverable and reliably invocable by agents, via a skill registry/manifest, keyword retrieval, a bounded plan→act→observe→finish loop, and a skill↔tool bridge — all additive and behind a config flag.

**Architecture:** Five new units (`skill_registry`, `skill_selector`, `agent_loop`, `call_tool` skill, `skill_search` skill) plus a small config loader. A build step scans skill files + tool endpoints into `skills_manifest.json` + a SQLite FTS5 index. At request time the selector injects relevant skills into the prompt; the loop runs `##SKILL##` calls (reusing `SkillsEngine`) across multiple observed steps. Everything is gated by `config/scaffold.yaml → enabled` so flag-off behavior is byte-for-byte today's behavior.

**Tech Stack:** Python 3, stdlib `ast` + `sqlite3` (FTS5), PyYAML, pytest, existing `core/skills_engine.py`, `core/tool_client.py`.

---

## Context for the implementer (read first)

- **Skill invocation today:** an LLM emits `##SKILL:name{json}##`. `core/skills_engine.py` → `SkillsEngine(agent_id).parse_and_run(text, ...)` finds every marker, runs each skill file (`skills/shared/<name>.py` or `agents/<id>/skills/<name>.py`) as a subprocess with args in env var `SKILL_ARGS`, and returns `(spliced_text, results)` where `results` is a list of `{"success": bool, "output": str, "skill": str, ...}`. It accepts `exclude=<set of names>`.
- **A skill file** reads `json.loads(os.environ.get("SKILL_ARGS","{}"))` and prints its result to stdout. See `skills/shared/invoice_calculator.py`.
- **Two-pass reground exists in two places** we will generalize:
  - Interactive: `core/base_agent.py` `handle_message` ~lines 1602–1640 (build prompt → `llm_chat` → `parse_and_run` → reformat with skill data).
  - Autonomous: `core/task_runner.py` `_run_skills_and_reformat` (lines 190–254).
- **Tool calls today:** `core/tool_client.py` `ToolClient.call(agent, tool, input_data, task_id=None)` async → `POST http://localhost:8000/tools/<slug>/<tool>` with `{"input": input_data}` → returns `{"success","output","error",...}`. Slug map: `simon_bately→simon, claw_batto→claw, phil_hass→phil, sam_axe→sam`.
- **Existing discovery to reuse:** `skills/shared/skill_catalog.py` already `ast`-parses docstrings into `{name, path, scope, summary, args_hint}`. We extend the *idea*, not that file.
- **Run tests:** `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest <path> -v`. The repo has `pytest.ini` and ~589 existing tests; don't break them.
- **Commits:** `claw-auto-git` auto-commits this tree hourly. Still commit per task (small, message-tagged) — it's fine if auto-git also runs.
- **Paths are relative to** `/home/switchhacker/baza-empire/agent-framework-v3/`.

## File Structure

| File | New/changed | Responsibility |
|---|---|---|
| `config/scaffold.yaml` | new | Flag + loop/retrieval knobs |
| `core/scaffold_config.py` | new | Load + cache scaffold.yaml; `is_enabled(agent_id)` |
| `core/skill_registry.py` | new | Extract metadata, build manifest.json + FTS5, query API, tool ingestion |
| `core/skill_selector.py` | new | Per-request selection → prompt block |
| `core/agent_loop.py` | new | Bounded plan→act→observe→finish loop (DI'd llm_call) |
| `skills/shared/skill_search.py` | new | Mid-loop registry query meta-skill |
| `skills/shared/call_tool.py` | new | `##SKILL##` → tool_client bridge |
| `core/base_agent.py` | changed (thin) | Flag-gated selector block + delegate to agent_loop |
| `core/task_runner.py` | changed (thin) | Flag-gated delegate to agent_loop |
| `dashboard/skills_manifest.json` / `.db` | generated | Manifest + FTS index (gitignored; Claw-excluded) |
| `tests/test_*` | new | TDD per unit |

---

## Task 1: Scaffold config + loader

**Files:**
- Create: `config/scaffold.yaml`
- Create: `core/scaffold_config.py`
- Test: `tests/test_scaffold_config.py`

- [ ] **Step 1: Write `config/scaffold.yaml`**

```yaml
# Agent skill scaffold — master switch + knobs. Flag-off = legacy behavior.
scaffold:
  enabled: false            # global master switch
  max_steps: 6              # agent_loop hard cap on plan→act→observe iterations
  retrieval_top_k: 8        # how many FTS-retrieved skills to inject
  pinned_core:              # always-on skills (injected every request when enabled)
    - artifact_save
    - web_search
    - ahb123_query
    - skill_search
    - call_tool
  per_agent:                # optional per-agent overrides of `enabled`
    claw_batto:
      enabled: false
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_scaffold_config.py
import os, textwrap
from core import scaffold_config

def test_disabled_by_default(tmp_path, monkeypatch):
    cfg = tmp_path / "scaffold.yaml"
    cfg.write_text("scaffold:\n  enabled: false\n")
    monkeypatch.setattr(scaffold_config, "_CONFIG_PATH", str(cfg))
    scaffold_config.reload()
    assert scaffold_config.is_enabled("phil_hass") is False
    assert scaffold_config.max_steps() == 6          # default when key missing
    assert scaffold_config.retrieval_top_k() == 8

def test_per_agent_override(tmp_path, monkeypatch):
    cfg = tmp_path / "scaffold.yaml"
    cfg.write_text(textwrap.dedent("""
        scaffold:
          enabled: false
          per_agent:
            claw_batto:
              enabled: true
    """))
    monkeypatch.setattr(scaffold_config, "_CONFIG_PATH", str(cfg))
    scaffold_config.reload()
    assert scaffold_config.is_enabled("claw_batto") is True
    assert scaffold_config.is_enabled("phil_hass") is False

def test_pinned_core_list(tmp_path, monkeypatch):
    cfg = tmp_path / "scaffold.yaml"
    cfg.write_text("scaffold:\n  enabled: true\n  pinned_core: [artifact_save, call_tool]\n")
    monkeypatch.setattr(scaffold_config, "_CONFIG_PATH", str(cfg))
    scaffold_config.reload()
    assert scaffold_config.pinned_core() == ["artifact_save", "call_tool"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_scaffold_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.scaffold_config'`

- [ ] **Step 4: Write `core/scaffold_config.py`**

```python
"""Loader for config/scaffold.yaml — the master switch for the skill scaffold.
Flag-off (default) means agents run the legacy single-shot / two-pass path."""
import os
import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config", "scaffold.yaml")
_DEFAULTS = {"enabled": False, "max_steps": 6, "retrieval_top_k": 8,
             "pinned_core": ["artifact_save", "web_search", "ahb123_query",
                             "skill_search", "call_tool"],
             "per_agent": {}}
_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    data = {}
    try:
        with open(_CONFIG_PATH) as f:
            data = (yaml.safe_load(f) or {}).get("scaffold", {}) or {}
    except FileNotFoundError:
        data = {}
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in data.items() if v is not None})
    _cache = merged
    return merged


def reload():
    """Drop the cache (used by tests / after editing the yaml)."""
    global _cache
    _cache = None
    return _load()


def is_enabled(agent_id: str | None = None) -> bool:
    cfg = _load()
    if agent_id:
        override = (cfg.get("per_agent") or {}).get(agent_id, {})
        if "enabled" in override:
            return bool(override["enabled"])
    return bool(cfg.get("enabled", False))


def max_steps() -> int:
    return int(_load().get("max_steps", 6))


def retrieval_top_k() -> int:
    return int(_load().get("retrieval_top_k", 8))


def pinned_core() -> list[str]:
    return list(_load().get("pinned_core", []))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_scaffold_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add config/scaffold.yaml core/scaffold_config.py tests/test_scaffold_config.py
git commit -m "feat(scaffold): config flag + loader (disabled by default)"
```

---

## Task 2: Registry — metadata extraction + describe (SKILL_META + legacy fallback)

**Files:**
- Create: `core/skill_registry.py` (first slice — pure functions, no DB yet)
- Test: `tests/test_skill_registry_describe.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_registry_describe.py
import textwrap
from core import skill_registry as reg

def test_extract_meta_literal(tmp_path):
    p = tmp_path / "demo.py"
    p.write_text(textwrap.dedent('''
        SKILL_META = {"category": "financial", "summary": "Total an invoice.",
                      "when_to_use": "User asks to total an invoice.",
                      "args": {"items": "list"}}
        print("ok")
    '''))
    meta = reg.extract_meta(str(p))
    assert meta["category"] == "financial"
    assert meta["summary"] == "Total an invoice."

def test_extract_meta_absent_returns_none(tmp_path):
    p = tmp_path / "nometa.py"
    p.write_text('"""Just a docstring summary."""\nprint(1)\n')
    assert reg.extract_meta(str(p)) is None

def test_extract_meta_malformed_returns_none(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text('SKILL_META = {"category": some_var}\nprint(1)\n')  # not a literal
    assert reg.extract_meta(str(p)) is None

def test_describe_uses_meta(tmp_path):
    p = tmp_path / "invoice_thing.py"
    p.write_text('SKILL_META = {"category":"financial","summary":"S","when_to_use":"W","args":{}}\n')
    d = reg.describe_skill(str(p), scope="shared")
    assert d["name"] == "invoice_thing"
    assert d["type"] == "skill"
    assert d["category"] == "financial"
    assert d["summary"] == "S"
    assert d["source_path"].endswith("invoice_thing.py")

def test_describe_legacy_fallback(tmp_path):
    p = tmp_path / "invoice_legacy.py"
    p.write_text('"""Calculate invoice totals from line items."""\nprint(1)\n')
    d = reg.describe_skill(str(p), scope="shared")
    assert d["summary"].startswith("Calculate invoice totals")
    assert d["category"] == "financial"        # inferred from "invoice_" prefix

def test_infer_category():
    assert reg.infer_category("invoice_calculator") == "financial"
    assert reg.infer_category("drywall_calculator") == "materials"
    assert reg.infer_category("system_health") == "infrastructure"
    assert reg.infer_category("totally_unknown_xyz") == "general"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_skill_registry_describe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.skill_registry'`

- [ ] **Step 3: Write `core/skill_registry.py` (describe slice)**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_skill_registry_describe.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add core/skill_registry.py tests/test_skill_registry_describe.py
git commit -m "feat(scaffold): skill metadata extraction (SKILL_META + legacy fallback)"
```

---

## Task 3: Registry — build manifest.json + FTS5 + search/categories

**Files:**
- Modify: `core/skill_registry.py` (add iteration, build, query, CLI)
- Test: `tests/test_skill_registry_build.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_registry_build.py
import textwrap
from core import skill_registry as reg

def _make_skill(d, name, body):
    (d / f"{name}.py").write_text(body)

def test_build_and_search(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    _make_skill(shared, "invoice_calculator",
                'SKILL_META={"category":"financial","summary":"Total an invoice from line items.",'
                '"when_to_use":"total or price an invoice","args":{}}\n')
    _make_skill(shared, "drywall_calculator",
                '"""Estimate drywall sheets for a room."""\n')
    json_path = tmp_path / "manifest.json"
    db_path = tmp_path / "manifest.db"
    n = reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "noagents"),
                  out_json=str(json_path), out_db=str(db_path), tools=None)
    assert n == 2
    hits = reg.search("invoice total", db_path=str(db_path), top_k=5)
    assert any(h["name"] == "invoice_calculator" for h in hits)
    cats = reg.categories(json_path=str(json_path))
    assert cats.get("financial", 0) >= 1
    assert cats.get("materials", 0) >= 1

def test_search_sanitizes_query(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    _make_skill(shared, "system_health", '"""Report CPU and memory health."""\n')
    db_path = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(tmp_path / "m.json"), out_db=str(db_path), tools=None)
    # punctuation / FTS operators must not raise
    hits = reg.search('cpu health!! "AND"', db_path=str(db_path), top_k=5)
    assert isinstance(hits, list)

def test_get_returns_descriptor(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    _make_skill(shared, "foo_bar", '"""Does foo."""\n')
    json_path = tmp_path / "m.json"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(json_path), out_db=str(tmp_path / "m.db"), tools=None)
    d = reg.get("foo_bar", json_path=str(json_path))
    assert d and d["summary"].startswith("Does foo")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_skill_registry_build.py -v`
Expected: FAIL with `AttributeError: module 'core.skill_registry' has no attribute 'build'`

- [ ] **Step 3: Append build/query/CLI to `core/skill_registry.py`**

```python
import json
import sqlite3

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
    if tools:
        descriptors.extend(tool_descriptors(tools))   # defined in Task 4
    with open(out_json, "w") as f:
        json.dump({"skills": descriptors}, f, indent=2)
    _write_fts(out_db, descriptors)
    return len(descriptors)


_FTS_SAFE = re.compile(r"[^a-zA-Z0-9_]+")


def _fts_query(raw: str) -> str:
    terms = [t for t in _FTS_SAFE.sub(" ", raw).split() if t]
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_skill_registry_build.py -v`
Expected: PASS (3 passed). (Note: `tool_descriptors` is referenced but only called when `tools` is truthy; these tests pass `tools=None`, so it is not yet needed. It is added in Task 4.)

- [ ] **Step 5: Commit**

```bash
git add core/skill_registry.py tests/test_skill_registry_build.py
git commit -m "feat(scaffold): build skills manifest + FTS5 search/categories + CLI"
```

---

## Task 4: Registry — tool-server ingestion (type:"tool")

**Files:**
- Modify: `core/skill_registry.py` (add `tool_descriptors`)
- Test: `tests/test_skill_registry_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_registry_tools.py
from core import skill_registry as reg

def test_tool_descriptors_from_registry_dict():
    tools = {
        "claw_batto": ["run-command", "disk-usage"],
        "sam_axe": ["generate-image"],
    }
    descs = reg.tool_descriptors(tools)
    names = {d["name"] for d in descs}
    assert "claw_batto/run-command" in names
    sam = next(d for d in descs if d["name"] == "sam_axe/generate-image")
    assert sam["type"] == "tool"
    assert sam["args"]["agent"] == "sam_axe"
    assert sam["args"]["tool"] == "generate-image"

def test_build_includes_tools(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    (shared / "noop.py").write_text('"""noop."""\n')
    db = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(tmp_path / "m.json"), out_db=str(db),
              tools={"sam_axe": ["generate-image"]})
    hits = reg.search("generate image", db_path=str(db), top_k=5)
    assert any(h["type"] == "tool" and "generate-image" in h["name"] for h in hits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_skill_registry_tools.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'tool_descriptors'`

- [ ] **Step 3: Add `tool_descriptors` to `core/skill_registry.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_skill_registry_tools.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/skill_registry.py tests/test_skill_registry_tools.py
git commit -m "feat(scaffold): ingest tool-server endpoints into manifest"
```

---

## Task 5: Skill selector

**Files:**
- Create: `core/skill_selector.py`
- Test: `tests/test_skill_selector.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_selector.py
from core import skill_registry as reg
from core import skill_selector

def _seed(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    (shared / "invoice_calculator.py").write_text(
        'SKILL_META={"category":"financial","summary":"Total an invoice.",'
        '"when_to_use":"total an invoice","args":{}}\n')
    (shared / "drywall_calculator.py").write_text('"""Estimate drywall sheets."""\n')
    (shared / "artifact_save.py").write_text('"""Save an artifact file."""\n')
    json_path = tmp_path / "m.json"; db_path = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(json_path), out_db=str(db_path), tools=None)
    return str(json_path), str(db_path)

def test_select_includes_retrieved_and_pinned(tmp_path):
    json_path, db_path = _seed(tmp_path)
    res = skill_selector.select(
        "please total this invoice", agent_id="phil_hass",
        pinned=["artifact_save"], role_pins=[], top_k=5,
        json_path=json_path, db_path=db_path)
    names = {s["name"] for s in res["skills"]}
    assert "invoice_calculator" in names          # retrieved
    assert "artifact_save" in names                # pinned always present
    assert "financial" in res["categories"]

def test_select_empty_query_still_returns_pinned(tmp_path):
    json_path, db_path = _seed(tmp_path)
    res = skill_selector.select(
        "", agent_id="phil_hass", pinned=["artifact_save"], role_pins=["drywall_calculator"],
        top_k=5, json_path=json_path, db_path=db_path)
    names = {s["name"] for s in res["skills"]}
    assert "artifact_save" in names and "drywall_calculator" in names

def test_render_block_is_compact_text(tmp_path):
    json_path, db_path = _seed(tmp_path)
    res = skill_selector.select("invoice", agent_id="phil_hass", pinned=["artifact_save"],
                                role_pins=[], top_k=5, json_path=json_path, db_path=db_path)
    block = skill_selector.render_block(res)
    assert "RELEVANT SKILLS" in block
    assert "invoice_calculator" in block
    assert "skill_search" in block                 # nudge to self-discover
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_skill_selector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.skill_selector'`

- [ ] **Step 3: Write `core/skill_selector.py`**

```python
"""Per-request skill/tool selection. Given a message + agent, return the set of
skills to put in front of the LLM: pinned core + agent role-pins + top-K FTS
retrieved, plus a category index. Rendered into a compact prompt block."""
from core import skill_registry as reg


def select(message: str, agent_id: str | None = None,
           pinned: list[str] | None = None, role_pins: list[str] | None = None,
           top_k: int = 8, json_path: str = reg.DEFAULT_JSON,
           db_path: str = reg.DEFAULT_DB) -> dict:
    pinned = pinned or []
    role_pins = role_pins or []
    chosen: dict[str, dict] = {}

    def _add(name: str):
        if name in chosen:
            return
        d = reg.get(name, json_path=json_path)
        if d:
            chosen[name] = d

    for n in pinned:
        _add(n)
    for n in role_pins:
        _add(n)
    if message.strip():
        for hit in reg.search(message, db_path=db_path, top_k=top_k):
            chosen.setdefault(hit["name"], hit)

    return {
        "skills": list(chosen.values()),
        "categories": reg.categories(json_path=json_path),
        "agent_id": agent_id,
    }


def render_block(selection: dict) -> str:
    lines = ["== RELEVANT SKILLS FOR THIS REQUEST =="]
    for s in selection["skills"]:
        args = s.get("args") or {}
        arg_hint = ", ".join(f'"{k}":<{v}>' for k, v in list(args.items())[:4]) if args else ""
        call = f'##SKILL:{s["name"]}{{{arg_hint}}}##' if s.get("type") == "skill" \
            else f'##SKILL:call_tool{{"agent":"{args.get("agent","")}",' \
                 f'"tool":"{args.get("tool","")}","input":{{}}}}##'
        summ = s.get("summary", "")
        when = f" — {s['when_to_use']}" if s.get("when_to_use") else ""
        lines.append(f"{call}\n    {summ}{when}")
    cats = selection.get("categories", {})
    if cats:
        cat_str = ", ".join(f"{c}({n})" for c, n in sorted(cats.items()))
        lines.append(f"\nYou also have skills in: {cat_str}.")
        lines.append('Call ##SKILL:skill_search{"query":"..."}## to discover more skills mid-task.')
    lines.append("== END RELEVANT SKILLS ==")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_skill_selector.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/skill_selector.py tests/test_skill_selector.py
git commit -m "feat(scaffold): per-request skill selector + prompt block renderer"
```

---

## Task 6: `skill_search` meta-skill

**Files:**
- Create: `skills/shared/skill_search.py`
- Test: `tests/test_skill_search_skill.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_search_skill.py
import json, os, subprocess, sys
from core import skill_registry as reg

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def test_skill_search_outputs_matches(tmp_path, monkeypatch):
    shared = tmp_path / "shared"; shared.mkdir()
    (shared / "invoice_calculator.py").write_text(
        'SKILL_META={"category":"financial","summary":"Total an invoice.",'
        '"when_to_use":"total an invoice","args":{}}\n')
    json_path = tmp_path / "m.json"; db_path = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(json_path), out_db=str(db_path), tools=None)
    env = dict(os.environ)
    env["SKILL_ARGS"] = json.dumps({"query": "invoice total"})
    env["SKILL_MANIFEST_DB"] = str(db_path)
    env["SKILL_MANIFEST_JSON"] = str(json_path)
    out = subprocess.run([sys.executable, os.path.join(FRAMEWORK, "skills", "shared", "skill_search.py")],
                         capture_output=True, text=True, env=env, timeout=30)
    assert out.returncode == 0
    assert "invoice_calculator" in out.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_skill_search_skill.py -v`
Expected: FAIL (skill file missing → nonzero return / FileNotFound)

- [ ] **Step 3: Write `skills/shared/skill_search.py`**

```python
#!/usr/bin/env python3
"""Search the skill registry for skills/tools matching a query. Lets an agent
discover capabilities mid-task instead of relying only on the injected list."""
SKILL_META = {
    "category": "general",
    "summary": "Search the skill/tool registry by keyword.",
    "when_to_use": "When you need a capability not in the listed skills.",
    "args": {"query": "keywords describing the capability", "top_k": "int, optional"},
}
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import skill_registry as reg

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
query = args.get("query", "")
top_k = int(args.get("top_k", 8))
db_path = os.environ.get("SKILL_MANIFEST_DB", reg.DEFAULT_DB)

hits = reg.search(query, db_path=db_path, top_k=top_k)
if not hits:
    print(f"No skills found for query: {query!r}")
else:
    for h in hits:
        kind = h.get("type", "skill")
        print(f"- {h['name']} [{kind}/{h.get('category','')}] — {h.get('summary','')}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_skill_search_skill.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add skills/shared/skill_search.py tests/test_skill_search_skill.py
git commit -m "feat(scaffold): skill_search meta-skill for mid-task discovery"
```

---

## Task 7: `call_tool` bridge skill

**Files:**
- Create: `skills/shared/call_tool.py`
- Test: `tests/test_call_tool_skill.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_call_tool_skill.py
import json, os, subprocess, sys

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(FRAMEWORK, "skills", "shared", "call_tool.py")

def _run(args, env_extra=None):
    env = dict(os.environ)
    env["SKILL_ARGS"] = json.dumps(args)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, SKILL], capture_output=True, text=True,
                          env=env, timeout=30)

def test_missing_agent_or_tool_errors():
    out = _run({"agent": "", "tool": ""})
    assert out.returncode != 0 or "error" in out.stdout.lower()

def test_unreachable_server_reports_error():
    # point at a dead port so the call fails fast and cleanly (no traceback to stderr crash)
    out = _run({"agent": "sam_axe", "tool": "generate-image", "input": {"prompt": "x"}},
               env_extra={"TOOL_SERVER_URL": "http://localhost:9"})
    assert out.returncode == 0                    # skill itself must not crash
    payload = json.loads(out.stdout)
    assert payload["success"] is False
    assert "error" in payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_call_tool_skill.py -v`
Expected: FAIL (skill file missing)

- [ ] **Step 3: Write `skills/shared/call_tool.py`**

```python
#!/usr/bin/env python3
"""Bridge: invoke any tool-server endpoint through the ##SKILL## path.
Lets an agent reach the ~100 HTTP tools (Sam imaging, Claw devops, edge, etc.)
with the same mechanism it uses for skills."""
SKILL_META = {
    "category": "general",
    "summary": "Call a tool-server endpoint (agent/tool) with an input dict.",
    "when_to_use": "To run an HTTP tool such as sam_axe/generate-image or claw_batto/run-command.",
    "args": {"agent": "e.g. sam_axe", "tool": "e.g. generate-image", "input": "dict of inputs"},
}
import json
import os

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
agent = (args.get("agent") or "").strip()
tool = (args.get("tool") or "").strip()
tool_input = args.get("input") or {}

if not agent or not tool:
    print(json.dumps({"success": False, "error": "call_tool requires non-empty 'agent' and 'tool'"}))
    raise SystemExit(1)

base = os.environ.get("TOOL_SERVER_URL", "http://localhost:8000")
slug_map = {"simon_bately": "simon", "claw_batto": "claw", "phil_hass": "phil", "sam_axe": "sam"}
slug = slug_map.get(agent, agent)
url = f"{base}/tools/{slug}/{tool}"

try:
    import httpx
    resp = httpx.post(url, json={"input": tool_input}, timeout=120)
    resp.raise_for_status()
    print(json.dumps(resp.json()))
except Exception as e:
    print(json.dumps({"success": False, "error": f"{type(e).__name__}: {e}", "tool": f"{slug}/{tool}"}))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_call_tool_skill.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add skills/shared/call_tool.py tests/test_call_tool_skill.py
git commit -m "feat(scaffold): call_tool bridge skill (##SKILL## -> tool server)"
```

---

## Task 8: Agent loop (plan→act→observe→finish)

**Files:**
- Create: `core/agent_loop.py`
- Test: `tests/test_agent_loop.py`

The loop is decoupled from any specific inference path: the caller passes an
`llm_call(messages, system) -> str` callable and a `SkillsEngine`-like object
exposing `parse_and_run(text, **kw) -> (spliced, results)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_loop.py
from core import agent_loop

class FakeEngine:
    """Runs no real skills; reports markers as successful with canned output."""
    def __init__(self, outputs): self.outputs = outputs
    def parse_and_run(self, text, **kw):
        results = []
        spliced = text
        for name, out in self.outputs.items():
            if f"##SKILL:{name}" in text:
                results.append({"success": True, "skill": name, "output": out})
                spliced = spliced.replace(f"##SKILL:{name}{{}}##", f"[SKILL RESULT: {name}] {out}")
        return spliced, results

def test_loop_stops_on_final_marker():
    calls = []
    def llm(messages, system):
        calls.append(messages)
        if len(calls) == 1:
            return '##SKILL:invoice_calculator{}##'
        return 'FINAL: total is $100'
    eng = FakeEngine({"invoice_calculator": "total=100"})
    res = agent_loop.run_loop(llm, eng, system="sys", user="total it",
                              max_steps=6, finish_markers=("FINAL:",))
    assert "total is $100" in res["final"]
    assert res["steps"] == 2

def test_loop_stops_when_no_skill_markers():
    def llm(messages, system):
        return "Here is your answer, no skills needed."
    eng = FakeEngine({})
    res = agent_loop.run_loop(llm, eng, system="sys", user="hi", max_steps=6)
    assert res["steps"] == 1
    assert "answer" in res["final"]

def test_loop_respects_max_steps():
    def llm(messages, system):
        return '##SKILL:invoice_calculator{}##'   # never finishes on its own
    eng = FakeEngine({"invoice_calculator": "x"})
    res = agent_loop.run_loop(llm, eng, system="sys", user="go", max_steps=3)
    assert res["steps"] == 3
    assert res["truncated"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_agent_loop.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.agent_loop'`

- [ ] **Step 3: Write `core/agent_loop.py`**

```python
"""Bounded plan→act→observe→finish loop. Generalizes the two-pass reground in
base_agent/task_runner to N steps. Inference is dependency-injected so it works
for both the async base_agent path and the sync task_runner path.

llm_call(messages, system) -> str        # messages: [{"role","content"}, ...]
engine.parse_and_run(text, **kw) -> (spliced_text, results)
"""
import re

_SKILL_MARKER = re.compile(r"##SKILL:")


def run_loop(llm_call, engine, system: str, user: str, *,
             max_steps: int = 6, exclude=None,
             finish_markers=("FINAL:", "TASK_COMPLETE"),
             observe_intro: str | None = None,
             parse_kwargs: dict | None = None) -> dict:
    parse_kwargs = parse_kwargs or {}
    observe_intro = observe_intro or (
        "Here is the REAL data your skills returned. Use ONLY this data — do not "
        "invent values. If the task is done, reply with FINAL: followed by the "
        "answer. Otherwise call more skills.")
    messages = [{"role": "user", "content": user}]
    final_text = ""
    truncated = False
    steps = 0

    for steps in range(1, max_steps + 1):
        response = llm_call(messages, system) or ""
        messages.append({"role": "assistant", "content": response})

        has_markers = bool(_SKILL_MARKER.search(response))
        if not has_markers:
            final_text = response
            break

        spliced, results = engine.parse_and_run(response, **parse_kwargs)
        successful = [r for r in results if r.get("success")]

        if any(m in response for m in finish_markers):
            final_text = spliced
            break

        if not successful:
            # markers present but nothing ran — surface honest spliced text and stop
            final_text = spliced
            break

        if steps == max_steps:
            final_text = spliced
            truncated = True
            break

        skill_data = "\n\n".join(f"[{r.get('skill','skill')} output]\n{r.get('output','')}"
                                 for r in successful)
        messages.append({"role": "user", "content": f"{observe_intro}\n\n{skill_data}"})
    else:  # pragma: no cover - for-range always breaks or exhausts
        truncated = True

    return {"final": final_text, "steps": steps, "truncated": truncated,
            "transcript": messages}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_agent_loop.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/agent_loop.py tests/test_agent_loop.py
git commit -m "feat(scaffold): bounded plan-act-observe-finish agent loop"
```

---

## Task 9: Integrate into `base_agent` (flag-gated) + regression guard

**Files:**
- Modify: `core/base_agent.py` (`build_system_prompt` ~line 153; `handle_message` ~line 1602–1660)
- Test: `tests/test_base_agent_scaffold_integration.py`

The change is **additive and flag-gated**. When `scaffold_config.is_enabled(agent_id)` is False, the existing code path runs unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_base_agent_scaffold_integration.py
from core import scaffold_config
from core import skill_selector

def test_selector_block_injected_when_enabled(monkeypatch, tmp_path):
    # Build a tiny manifest and point the selector at it
    from core import skill_registry as reg
    shared = tmp_path / "shared"; shared.mkdir()
    (shared / "invoice_calculator.py").write_text(
        'SKILL_META={"category":"financial","summary":"Total an invoice.",'
        '"when_to_use":"total an invoice","args":{}}\n')
    jp = tmp_path / "m.json"; db = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(jp), out_db=str(db), tools=None)

    sel = skill_selector.select("total this invoice", agent_id="phil_hass",
                                pinned=[], role_pins=[], top_k=5,
                                json_path=str(jp), db_path=str(db))
    block = skill_selector.render_block(sel)
    assert "invoice_calculator" in block and "RELEVANT SKILLS" in block

def test_scaffold_disabled_by_default():
    scaffold_config.reload()
    assert scaffold_config.is_enabled("phil_hass") is False
```

(This proves the selector machinery the integration calls; the wiring below is verified by the existing `base_agent` tests still passing in Step 4.)

- [ ] **Step 2: Run test to verify it fails (or passes trivially) then do the wiring**

Run: `venv/bin/python -m pytest tests/test_base_agent_scaffold_integration.py -v`
Expected: PASS (these assert on already-built units). Now wire `base_agent`.

- [ ] **Step 3: Edit `core/base_agent.py` — gate the skill block in `build_system_prompt`**

Add near the top of the file (with the other imports):

```python
from core import scaffold_config
```

In `build_system_prompt`, **wrap** the hard-coded WEB TOOLS / ARTIFACTS / AHB123 blocks so that when the flag is on we *append the selector block instead of* the static lists. Concretely, after the existing static blocks (just before the `if extra:` at ~line 233), add:

```python
        # Scaffold: when enabled, append a per-request relevant-skills block.
        # (Static blocks above remain as a fallback / pinned baseline.)
        if scaffold_config.is_enabled(getattr(self, "AGENT_ID", None)) and extra.startswith("REQUEST::"):
            try:
                from core import skill_selector
                msg = extra[len("REQUEST::"):]
                role_pins = list(getattr(self, "_role_skill_pins", []) or [])
                sel = skill_selector.select(msg, agent_id=self.AGENT_ID,
                                            pinned=scaffold_config.pinned_core(),
                                            role_pins=role_pins,
                                            top_k=scaffold_config.retrieval_top_k())
                prompt += "\n\n" + skill_selector.render_block(sel)
                extra = ""  # consumed
            except Exception as e:
                logger.warning(f"[scaffold] selector skipped: {e}")
```

Note: callers pass the user message as `extra="REQUEST::<text>"` only when they want retrieval; the legacy `build_system_prompt()` (no extra) is untouched and still cached.

- [ ] **Step 4: Edit `core/base_agent.py` — gate the loop in `handle_message`**

Replace the Pass-1/Pass-2 block (~lines 1602–1660) with a flag check that delegates to `agent_loop` when enabled, else runs the existing two-pass verbatim:

```python
        if scaffold_config.is_enabled(self.AGENT_ID):
            from core import agent_loop
            system = self.build_system_prompt(extra=f"REQUEST::{text}")
            routed_model = self._route_model(text)
            loop = asyncio.get_event_loop()

            def _llm(messages, system_prompt):
                return self.llm_chat(messages, system_prompt, model_override=routed_model)

            res = await loop.run_in_executor(
                None, lambda: agent_loop.run_loop(
                    _llm, self.skills, system=system, user=text,
                    max_steps=scaffold_config.max_steps(),
                    parse_kwargs={"chat_id": chat_id}))
            response = res["final"] or "_(no response)_"
            skill_results = []  # loop handled skills internally
        else:
            # ── legacy path (unchanged) ──
            system = self.build_system_prompt()
            routed_model = self._route_model(text)
            loop = asyncio.get_event_loop()
            t0 = time.time()
            response = await loop.run_in_executor(
                None, lambda: self.llm_chat(messages, system, model_override=routed_model))
            if not response:
                response = "_(no response)_"
            response, skill_results = self.skills.parse_and_run(response, chat_id=chat_id)
            successful_skills = [r for r in skill_results if r.get("success")]
            if successful_skills:
                # ... EXISTING reformat block stays exactly as-is ...
                pass  # (keep the current lines 1625–1660 here unchanged)
```

> Implementer note: physically keep the original lines 1623–1660 inside the `else:` branch — do not delete them. The only new code is the `if` branch.

- [ ] **Step 5: Run the full base_agent + integration tests**

Run: `venv/bin/python -m pytest tests/test_base_agent_scaffold_integration.py tests/ -k "base_agent or scaffold" -v`
Expected: PASS, and no previously-passing base_agent tests regress.

- [ ] **Step 6: Sanity-check flag-off import path**

Run: `venv/bin/python -c "import core.base_agent; from core import scaffold_config; print('enabled:', scaffold_config.is_enabled('phil_hass'))"`
Expected: prints `enabled: False` (no behavior change live).

- [ ] **Step 7: Commit**

```bash
git add core/base_agent.py tests/test_base_agent_scaffold_integration.py
git commit -m "feat(scaffold): flag-gated selector + agent_loop in base_agent"
```

---

## Task 10: Integrate into `task_runner` (flag-gated)

**Files:**
- Modify: `core/task_runner.py` (`run_task_with_llm` ~line 257; reuse `_run_skills_and_reformat` as fallback)
- Test: `tests/test_task_runner_scaffold.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_task_runner_scaffold.py
from core import scaffold_config

def test_task_runner_respects_flag_off():
    scaffold_config.reload()
    # With scaffold disabled, the loop delegation must be skipped.
    assert scaffold_config.is_enabled("claw_batto") is False

def test_agent_loop_importable_from_task_runner_context():
    from core import agent_loop
    assert hasattr(agent_loop, "run_loop")
```

- [ ] **Step 2: Run test to verify it fails (or passes trivially) then wire**

Run: `venv/bin/python -m pytest tests/test_task_runner_scaffold.py -v`
Expected: PASS (asserts on built units); proceed to wire the delegation.

- [ ] **Step 3: Edit `core/task_runner.py` — flag-gated loop delegation**

Add import near the top:

```python
from core import scaffold_config
```

Inside `run_task_with_llm`, **before** the existing single Ollama call + `_run_skills_and_reformat`, add a gated branch. Find where `output` is first produced from the LLM (~line 324–356) and wrap:

```python
    if scaffold_config.is_enabled(agent_id) and SkillsEngine is not None:
        from core import agent_loop, skill_selector
        engine = SkillsEngine(agent_id)
        try:
            role_pins = list(agent_cfg.get("skills", []) or [])
        except Exception:
            role_pins = []
        sel = skill_selector.select(
            f"{task['title']} {task.get('description','')}", agent_id=agent_id,
            pinned=scaffold_config.pinned_core(), role_pins=role_pins,
            top_k=scaffold_config.retrieval_top_k())
        system_with_skills = system + "\n\n" + skill_selector.render_block(sel)
        user = f"Task: {task['title']}\nDescription: {task.get('description','')}"

        def _llm(messages, system_prompt):
            payload = {"model": model, "stream": False,
                       "options": {"num_predict": 2000, "temperature": 0.3},
                       "messages": [{"role": "system", "content": system_prompt}] + messages}
            r = requests.post(f"{target_url}/api/chat", json=payload, timeout=LLM_REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()

        res = agent_loop.run_loop(_llm, engine, system=system_with_skills, user=user,
                                  max_steps=scaffold_config.max_steps(),
                                  parse_kwargs={"task_id": task.get("id"),
                                                "project_id": task.get("project_id"),
                                                "exclude": {"artifact_save"}})
        output = res["final"]
        # fall through to the existing artifact-save + TASK_COMPLETE parsing below
```

> The existing non-scaffold path (single call + `_run_skills_and_reformat`) stays as the `else`/default. `output` feeds the same downstream completion-signal parsing either way.

- [ ] **Step 4: Run task_runner tests**

Run: `venv/bin/python -m pytest tests/test_task_runner_scaffold.py tests/ -k "task_runner" -v`
Expected: PASS; no regression in existing task_runner tests.

- [ ] **Step 5: Commit**

```bash
git add core/task_runner.py tests/test_task_runner_scaffold.py
git commit -m "feat(scaffold): flag-gated agent_loop delegation in task_runner"
```

---

## Task 11: Build wiring, gitignore, Claw exclusion, manifest smoke test

**Files:**
- Modify: `.gitignore`
- Modify: `scripts/claw_continuous_review.py` and/or `scripts/claw_fs_watcher.py` (exclusion list)
- Build: generate the real manifest
- Test: `tests/test_manifest_smoke.py`

- [ ] **Step 1: Add the generated manifest to `.gitignore`**

Append to `.gitignore`:

```
dashboard/skills_manifest.json
dashboard/skills_manifest.db
```

- [ ] **Step 2: Exclude the manifest from Claw's reviewer**

Find the exclusion set in `scripts/claw_continuous_review.py` (and `scripts/claw_fs_watcher.py`) that already lists `claw_reviews.db`, `.claw_infra_snapshot.json`, etc. Add `skills_manifest.db` and `skills_manifest.json` to that set. (Grep: `venv/bin/python - <<'PY'` or `grep -n "claw_reviews.db" scripts/claw_*.py`.)

- [ ] **Step 3: Build the real manifest**

Run: `venv/bin/python -m core.skill_registry --build`
Expected: prints `Built manifest: <N> descriptors → .../dashboard/skills_manifest.json` where N ≥ 240 (skills) and grows if the tool server is up.

- [ ] **Step 4: Write the smoke test**

```python
# tests/test_manifest_smoke.py
import os
from core import skill_registry as reg

def test_real_manifest_has_core_skills():
    if not os.path.exists(reg.DEFAULT_JSON):
        import pytest; pytest.skip("manifest not built in this environment")
    assert reg.get("artifact_save") is not None
    assert reg.get("invoice_calculator") is not None
    cats = reg.categories()
    assert cats.get("financial", 0) >= 5
    hits = reg.search("overdue invoice", top_k=5)
    assert any("invoice" in h["name"] for h in hits)
```

- [ ] **Step 5: Run the smoke test**

Run: `venv/bin/python -m pytest tests/test_manifest_smoke.py -v`
Expected: PASS (or SKIP if manifest absent).

- [ ] **Step 6: Full regression sweep**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: the prior baseline of passing tests still passes (note any pre-existing unrelated failure, e.g. `test_split_detection`, separately — do not "fix" by editing unrelated code).

- [ ] **Step 7: Commit**

```bash
git add .gitignore scripts/claw_continuous_review.py scripts/claw_fs_watcher.py tests/test_manifest_smoke.py
git commit -m "chore(scaffold): build manifest, gitignore + Claw-exclude generated files"
```

---

## Task 12: Keep the manifest fresh (rebuild hook)

**Files:**
- Create: `scripts/rebuild_skill_manifest.sh`
- Optional: a systemd path unit OR a line in the existing scaffold-runner

- [ ] **Step 1: Write the rebuild helper**

```bash
#!/usr/bin/env bash
# Rebuild the skill manifest. Safe to run repeatedly; cheap (ast scan).
set -euo pipefail
cd "$(dirname "$0")/.."
venv/bin/python -m core.skill_registry --build
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/rebuild_skill_manifest.sh`

- [ ] **Step 3: Wire it to run on skill-dir changes (choose one, document in commit)**

Option A (preferred, no new unit): in the existing scaffold-runner loop (`core/scaffold_runner.py` or the 30s timer entry), add a cheap mtime check on `skills/shared` + `agents/*/skills` and call `core.skill_registry.build()` when changed.

Option B: a systemd `--user` path unit watching `skills/shared/` that triggers `scripts/rebuild_skill_manifest.sh`.

Implement Option A: add to the scaffold-runner tick:

```python
# in the scaffold runner periodic tick
try:
    from core import skill_registry
    import os
    src_mtime = max(
        [os.path.getmtime(os.path.join(skill_registry.SHARED_DIR, f))
         for f in os.listdir(skill_registry.SHARED_DIR) if f.endswith(".py")] or [0])
    man_mtime = os.path.getmtime(skill_registry.DEFAULT_JSON) \
        if os.path.exists(skill_registry.DEFAULT_JSON) else 0
    if src_mtime > man_mtime:
        skill_registry.build()
except Exception:
    pass
```

- [ ] **Step 4: Build once and verify freshness logic**

Run: `scripts/rebuild_skill_manifest.sh && ls -la dashboard/skills_manifest.*`
Expected: both files present and newly timestamped.

- [ ] **Step 5: Commit**

```bash
git add scripts/rebuild_skill_manifest.sh core/scaffold_runner.py
git commit -m "chore(scaffold): rebuild manifest on skill-dir changes"
```

---

## Rollout (post-implementation, manual)

1. All tasks merged with `enabled: false` — zero behavior change; full suite green.
2. Flip `per_agent.claw_batto.enabled: true` in `config/scaffold.yaml`; restart `baza-agent-claw-batto.service`; observe live + Claw review for a day.
3. Roll to remaining BaseAgent agents; finally set master `enabled: true`.
4. Rollback at any point = set the flag false + restart (no code change).

## Self-review notes (done)

- **Spec coverage:** registry (Tasks 2–4), selector + category index + skill_search (Tasks 5–6), agent_loop (Task 8), call_tool bridge (Task 7), config flag (Task 1), base_agent + task_runner integration (Tasks 9–10), gitignore/Claw-exclusion/build/freshness (Tasks 11–12), TDD + flag-off regression throughout. All spec sections map to a task.
- **Type consistency:** `build(shared_dir, agents_dir, out_json, out_db, tools)`, `search(query, db_path, top_k)`, `get(name, json_path)`, `categories(json_path)`, `tool_descriptors(tools)`, `select(message, agent_id, pinned, role_pins, top_k, json_path, db_path)`, `render_block(selection)`, `run_loop(llm_call, engine, system, user, *, max_steps, exclude, finish_markers, observe_intro, parse_kwargs)` — names used identically across tasks.
- **No placeholders:** every code step shows real code; integration steps preserve existing code explicitly in `else` branches.
