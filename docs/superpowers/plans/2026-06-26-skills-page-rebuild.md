# Skills Page Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the dashboard `/skills` page so each skill's purpose is easy to scan, metadata (summary / when-to-use / category / args) is editable through labeled fields, skills can be created via a guided form, and both shared and per-agent skills can be created/edited.

**Architecture:** Three layers. (1) A new focused helper module `dashboard/skill_io.py` composes a skill `.py` file from form fields while preserving the existing code body verbatim. (2) The Flask skill routes in `dashboard/app.py` become scope-aware (shared + per-agent), persist `SKILL_META`, and rebuild the skill manifest on save. (3) `dashboard/templates/skills.html` is rewritten with a category-grouped list and a labeled-fields editor + guided create form. `core/skill_registry.py` is consumed unchanged.

**Tech Stack:** Python 3 / Flask, `ast` (static metadata parse), pytest + Flask test client, vanilla HTML/CSS/JS template (no build step).

**Spec:** `docs/superpowers/specs/2026-06-26-skills-page-rebuild-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `dashboard/skill_io.py` | Pure functions: compose a skill source file from metadata fields, stripping/preserving the code body. No Flask, no I/O. | Create |
| `tests/test_skill_io.py` | Unit tests for `skill_io` (round-trip + body preservation). | Create |
| `dashboard/app.py` | Skill routes: list (enriched), read (scope-aware), save (compose + rebuild), run/delete (scope-aware) + helpers `_resolve_skill_path`, `_rebuild_skill_manifest`. | Modify `4921-5075` |
| `tests/test_skills_api.py` | Flask-client tests for the skill routes. | Create |
| `dashboard/templates/skills.html` | Grouped list + labeled-fields editor + guided create form. | Rewrite |
| `core/skill_registry.py` | Metadata parser/builder — used, not modified. | (read only) |

**Conventions to follow (from existing code):**
- Tests put `ROOT = Path(__file__).resolve().parents[1]` on `sys.path` and import `from dashboard.app import app` (see `tests/test_baza_scaffold_api.py`).
- Routes reference the module global `FRAMEWORK_DIR` (defined `app.py:39`) at call time, so tests redirect skill I/O with `monkeypatch.setattr("dashboard.app.FRAMEWORK_DIR", str(tmp))`.
- Skill name regex (existing): `^[a-z][a-z0-9_]{1,49}$`.
- `core/skill_registry.py` exposes `describe_skill(path, scope)`, `extract_meta(path)`, `build(shared_dir, agents_dir, out_json, out_db, tools=None)`. Categories are the fixed set: `financial, materials, project, client, marketing, infrastructure, data, code, ai, web, document, general`.

---

## Task 1: `skill_io.compose_skill_source` — compose file, preserve body

**Files:**
- Create: `dashboard/skill_io.py`
- Test: `tests/test_skill_io.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_io.py
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.skill_io import compose_skill_source
from core import skill_registry


def _write_and_extract(tmp_path, source):
    p = tmp_path / "s.py"
    p.write_text(source)
    return skill_registry.extract_meta(str(p))


def test_compose_roundtrips_via_extract_meta(tmp_path):
    src = compose_skill_source(
        summary="Create a PDF quote",
        when_to_use="When a client asks for a quote",
        category="financial",
        args={"client_id": "the AHB project id", "amount": "dollar total"},
        body_source="import os, json\nargs = json.loads(os.environ.get('SKILL_ARGS','{}'))\nprint('ok')\n",
    )
    # Source must parse and SKILL_META must read back identically.
    ast.parse(src)
    meta = _write_and_extract(tmp_path, src)
    assert meta["category"] == "financial"
    assert meta["summary"] == "Create a PDF quote"
    assert meta["when_to_use"] == "When a client asks for a quote"
    assert meta["args"] == {"client_id": "the AHB project id", "amount": "dollar total"}


def test_compose_preserves_code_body_verbatim():
    body = "import os, json\nargs = json.loads(os.environ.get('SKILL_ARGS','{}'))\nresult = args.get('x', 1) * 2\nprint(result)\n"
    src = compose_skill_source("s", "w", "general", {}, body)
    # Every logic line survives.
    for line in ("result = args.get('x', 1) * 2", "print(result)"):
        assert line in src


def test_compose_strips_old_header_no_duplicate_meta():
    existing = (
        "#!/usr/bin/env python3\n"
        '"""old summary"""\n\n'
        "SKILL_META = {'category': 'general', 'summary': 'old summary', 'when_to_use': '', 'args': {}}\n\n"
        "print('body')\n"
    )
    src = compose_skill_source("new summary", "now", "code", {}, existing)
    assert src.count("SKILL_META =") == 1
    assert "old summary" not in src
    assert "new summary" in src
    assert "print('body')" in src


def test_compose_inserts_meta_without_clobbering_imports():
    # File has imports above where SKILL_META sits later; logic must survive.
    existing = (
        "#!/usr/bin/env python3\n"
        "import os, json\n"
        "SKILL_META = {'category': 'general', 'summary': 'x', 'when_to_use': '', 'args': {}}\n"
        "args = json.loads(os.environ.get('SKILL_ARGS','{}'))\n"
        "print('keep me')\n"
    )
    src = compose_skill_source("s", "w", "data", {}, existing)
    assert src.count("SKILL_META =") == 1
    assert "import os, json" in src
    assert "print('keep me')" in src


def test_compose_survives_syntax_error_body():
    src = compose_skill_source("s", "w", "general", {}, "def broken(:\n  pass\n")
    assert "SKILL_META =" in src
    assert "def broken(:" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_skill_io.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.skill_io'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/skill_io.py
"""Compose an agent-skill .py file from metadata fields while preserving the
existing code body verbatim. Pure functions — no Flask, no filesystem I/O.

A skill file's "header" is the leading shebang, module docstring, and a
top-level ``SKILL_META`` assignment. compose_skill_source() regenerates ONLY
that header from the form fields and keeps every line of logic below it, so the
metadata form can never clobber real skill code."""
import ast


def _meta_repr(category, summary, when_to_use, args):
    lines = ["SKILL_META = {"]
    lines.append(f"    'category': {category!r},")
    lines.append(f"    'summary': {summary!r},")
    lines.append(f"    'when_to_use': {when_to_use!r},")
    if args:
        lines.append("    'args': {")
        for k, v in args.items():
            lines.append(f"        {str(k)!r}: {str(v)!r},")
        lines.append("    },")
    else:
        lines.append("    'args': {},")
    lines.append("}")
    return "\n".join(lines)


def _strip_header(source):
    """Return source with leading shebang, module docstring, and a top-level
    SKILL_META assignment removed; leading blank lines trimmed."""
    lines = source.splitlines()
    remove = set()
    if lines and lines[0].startswith("#!"):
        remove.add(0)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        body = [l for i, l in enumerate(lines) if i not in remove]
        return "\n".join(body).lstrip("\n")
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(getattr(tree.body[0], "value", None), ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        d = tree.body[0]
        for i in range(d.lineno - 1, d.end_lineno):
            remove.add(i)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SKILL_META" for t in node.targets):
            for i in range(node.lineno - 1, node.end_lineno):
                remove.add(i)
            break
    body = [l for i, l in enumerate(lines) if i not in remove]
    return "\n".join(body).lstrip("\n")


def compose_skill_source(summary, when_to_use, category, args, body_source):
    """Build a full skill .py source: shebang + docstring(summary) + SKILL_META
    + the preserved code body extracted from body_source."""
    body = _strip_header(body_source or "")
    doc = (summary or "").replace('"""', "'''")
    header = "#!/usr/bin/env python3\n"
    header += f'"""{doc}"""\n\n'
    header += _meta_repr(category or "general", summary or "", when_to_use or "", args or {}) + "\n\n"
    out = header + body
    if not out.endswith("\n"):
        out += "\n"
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_skill_io.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/skill_io.py tests/test_skill_io.py
git commit -m "feat(skills): skill_io.compose_skill_source preserves code body while writing SKILL_META"
```

---

## Task 2: `/api/skills/list` returns category + summary + when_to_use

**Files:**
- Modify: `dashboard/app.py:4921-4955` (`api_skills_list`)
- Test: `tests/test_skills_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skills_api.py
import os
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Redirect all skill I/O into a temp framework tree."""
    (tmp_path / "skills" / "shared").mkdir(parents=True)
    (tmp_path / "agents" / "simon_bately" / "skills").mkdir(parents=True)
    (tmp_path / "dashboard").mkdir(parents=True)
    import dashboard.app as appmod
    monkeypatch.setattr(appmod, "FRAMEWORK_DIR", str(tmp_path))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_list_includes_category_and_summary(client, tmp_path):
    _write(tmp_path, "skills/shared/make_quote.py",
           '"""Create a PDF quote"""\n'
           "SKILL_META = {'category': 'financial', 'summary': 'Create a PDF quote', 'when_to_use': 'on request', 'args': {}}\n"
           "print('x')\n")
    r = client.get("/api/skills/list")
    assert r.status_code == 200
    rows = r.get_json()
    row = next(x for x in rows if x["name"] == "make_quote")
    assert row["scope"] == "shared"
    assert row["category"] == "financial"
    assert row["summary"] == "Create a PDF quote"
    assert row["when_to_use"] == "on request"


def test_list_includes_per_agent_scope(client, tmp_path):
    _write(tmp_path, "agents/simon_bately/skills/ping.py",
           '"""Ping something"""\nprint("pong")\n')
    rows = client.get("/api/skills/list").get_json()
    row = next(x for x in rows if x["name"] == "ping")
    assert row["scope"] == "simon_bately"
    assert row["category"]  # inferred, non-empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_skills_api.py -v`
Expected: FAIL — `KeyError: 'category'` (current list only returns name/path/scope/size/modified/description)

- [ ] **Step 3: Write minimal implementation**

Replace the body of `api_skills_list` (`app.py:4921-4955`) with:

```python
@app.route('/api/skills/list')
def api_skills_list():
    from core import skill_registry
    skills = []
    shared_dir = os.path.join(FRAMEWORK_DIR, "skills", "shared")
    if os.path.isdir(shared_dir):
        for f in sorted(Path(shared_dir).glob("*.py")):
            if f.stem in ("__init__", "skill_registry"):
                continue
            stat = os.stat(f)
            d = skill_registry.describe_skill(str(f), "shared")
            skills.append({
                'name': f.stem, 'path': str(f), 'scope': 'shared',
                'category': d.get('category', 'general'),
                'summary': d.get('summary', ''),
                'when_to_use': d.get('when_to_use', ''),
                'description': d.get('summary', ''),
                'size': stat.st_size,
                'modified': datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    agents_dir = os.path.join(FRAMEWORK_DIR, "agents")
    if os.path.isdir(agents_dir):
        for agent_dir in sorted(Path(agents_dir).iterdir()):
            skill_dir = agent_dir / "skills"
            if skill_dir.is_dir():
                for f in sorted(skill_dir.glob("*.py")):
                    if f.stem in ("__init__", "skill_registry"):
                        continue
                    stat = os.stat(f)
                    d = skill_registry.describe_skill(str(f), f"agent:{agent_dir.name}")
                    skills.append({
                        'name': f.stem, 'path': str(f), 'scope': agent_dir.name,
                        'category': d.get('category', 'general'),
                        'summary': d.get('summary', ''),
                        'when_to_use': d.get('when_to_use', ''),
                        'description': d.get('summary', ''),
                        'size': stat.st_size,
                        'modified': datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    })
    return jsonify(skills)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_skills_api.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/app.py tests/test_skills_api.py
git commit -m "feat(skills): list endpoint returns category/summary/when_to_use via skill_registry"
```

---

## Task 3: `/api/skills/read` scope-aware + returns metadata fields

**Files:**
- Modify: `dashboard/app.py` — replace `api_skill_read` (`5007-5013`); add helper `_resolve_skill_path` just above the Skills routes (after `app.py:4919`)
- Test: `tests/test_skills_api.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_skills_api.py
def test_read_shared_returns_metadata_fields(client, tmp_path):
    _write(tmp_path, "skills/shared/make_quote.py",
           '"""Create a PDF quote"""\n'
           "SKILL_META = {'category': 'financial', 'summary': 'Create a PDF quote', 'when_to_use': 'on request', 'args': {'amount': 'total'}}\n"
           "print('x')\n")
    r = client.get("/api/skills/read/shared/make_quote")
    assert r.status_code == 200
    data = r.get_json()
    assert data["name"] == "make_quote"
    assert data["scope"] == "shared"
    assert data["category"] == "financial"
    assert data["summary"] == "Create a PDF quote"
    assert data["when_to_use"] == "on request"
    assert data["args"] == {"amount": "total"}
    assert "SKILL_META" in data["code"]


def test_read_per_agent(client, tmp_path):
    _write(tmp_path, "agents/simon_bately/skills/ping.py", '"""Ping"""\nprint("pong")\n')
    data = client.get("/api/skills/read/simon_bately/ping").get_json()
    assert data["scope"] == "simon_bately"
    assert "pong" in data["code"]


def test_read_rejects_path_traversal_scope(client, tmp_path):
    r = client.get("/api/skills/read/..%2f..%2fetc/passwd")
    assert r.status_code in (400, 404)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_skills_api.py -k read -v`
Expected: FAIL — 404 / routing error (current read route is `/api/skills/read/<skill_name>`, no scope segment)

- [ ] **Step 3: Write minimal implementation**

Add this helper immediately after the `skills_page()` route (after `app.py:4919`):

```python
import re as _skill_re

def _resolve_skill_path(scope, name, for_create=False):
    """Map (scope, name) -> absolute .py path. Returns (path, error_json,
    status). scope is 'shared' or an existing agent id. Guards traversal."""
    name = (name or "").strip()
    scope = (scope or "shared").strip()
    if not _skill_re.match(r'^[a-z][a-z0-9_]{1,49}$', name):
        return None, {'error': 'invalid name'}, 400
    if scope == 'shared':
        base = os.path.join(FRAMEWORK_DIR, "skills", "shared")
    else:
        if not _skill_re.match(r'^[a-z][a-z0-9_]{1,40}$', scope):
            return None, {'error': 'invalid scope'}, 400
        agent_dir = os.path.join(FRAMEWORK_DIR, "agents", scope)
        if not os.path.isdir(agent_dir):
            return None, {'error': f'unknown agent scope: {scope}'}, 400
        base = os.path.join(agent_dir, "skills")
        if for_create:
            os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{name}.py"), None, 200
```

Replace `api_skill_read` (`app.py:5007-5013`) with:

```python
@app.route('/api/skills/read/<scope>/<skill_name>')
def api_skill_read(scope, skill_name):
    from core import skill_registry
    path, err, status = _resolve_skill_path(scope, skill_name)
    if err:
        return jsonify(err), status
    if not os.path.exists(path):
        return jsonify({'error': 'not found'}), 404
    d = skill_registry.describe_skill(path, 'shared' if scope == 'shared' else f'agent:{scope}')
    return jsonify({
        'name': skill_name, 'scope': scope, 'path': path,
        'category': d.get('category', 'general'),
        'summary': d.get('summary', ''),
        'when_to_use': d.get('when_to_use', ''),
        'args': d.get('args', {}),
        'code': open(path).read(),
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_skills_api.py -k read -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/app.py tests/test_skills_api.py
git commit -m "feat(skills): read endpoint is scope-aware and returns SKILL_META fields"
```

---

## Task 4: `/api/skills/save` composes file + per-agent + rebuilds manifest

**Files:**
- Modify: `dashboard/app.py` — replace `api_skill_save` (`5015-5030`); add helper `_rebuild_skill_manifest` after `_resolve_skill_path`
- Test: `tests/test_skills_api.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_skills_api.py
from core import skill_registry


def test_save_shared_writes_parseable_meta(client, tmp_path):
    r = client.post("/api/skills/save", json={
        "scope": "shared", "name": "make_quote",
        "summary": "Create a PDF quote", "when_to_use": "on request",
        "category": "financial", "args": {"amount": "dollar total"},
        "code": "import os, json\nargs = json.loads(os.environ.get('SKILL_ARGS','{}'))\nprint('hi')\n",
    })
    assert r.status_code == 200, r.get_json()
    path = tmp_path / "skills" / "shared" / "make_quote.py"
    assert path.exists()
    meta = skill_registry.extract_meta(str(path))
    assert meta["category"] == "financial"
    assert meta["summary"] == "Create a PDF quote"
    assert meta["args"] == {"amount": "dollar total"}
    assert "print('hi')" in path.read_text()


def test_save_per_agent_lands_in_agent_dir(client, tmp_path):
    r = client.post("/api/skills/save", json={
        "scope": "simon_bately", "name": "ping",
        "summary": "Ping", "when_to_use": "", "category": "general",
        "args": {}, "code": "print('pong')\n",
    })
    assert r.status_code == 200, r.get_json()
    assert (tmp_path / "agents" / "simon_bately" / "skills" / "ping.py").exists()


def test_save_rejects_unknown_agent_scope(client):
    r = client.post("/api/skills/save", json={
        "scope": "nope_agent", "name": "x", "summary": "s",
        "when_to_use": "", "category": "general", "args": {}, "code": "print(1)\n",
    })
    assert r.status_code == 400


def test_save_rebuilds_manifest(client, tmp_path):
    client.post("/api/skills/save", json={
        "scope": "shared", "name": "make_quote", "summary": "q",
        "when_to_use": "", "category": "financial", "args": {}, "code": "print(1)\n",
    })
    assert (tmp_path / "dashboard" / "skills_manifest.json").exists()


def test_save_survives_manifest_failure(client, tmp_path, monkeypatch):
    import dashboard.app as appmod
    monkeypatch.setattr(appmod, "_rebuild_skill_manifest", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.post("/api/skills/save", json={
        "scope": "shared", "name": "make_quote", "summary": "q",
        "when_to_use": "", "category": "financial", "args": {}, "code": "print(1)\n",
    })
    assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_skills_api.py -k save -v`
Expected: FAIL — `extract_meta` returns None / KeyError (current save writes raw code, ignores summary/meta; has no `scope` handling)

- [ ] **Step 3: Write minimal implementation**

Add this helper after `_resolve_skill_path`:

```python
def _rebuild_skill_manifest():
    """Best-effort rebuild of the skill manifest so agents see new metadata.
    Never raises to the caller."""
    try:
        from core import skill_registry
        skill_registry.build(
            shared_dir=os.path.join(FRAMEWORK_DIR, "skills", "shared"),
            agents_dir=os.path.join(FRAMEWORK_DIR, "agents"),
            out_json=os.path.join(FRAMEWORK_DIR, "dashboard", "skills_manifest.json"),
            out_db=os.path.join(FRAMEWORK_DIR, "dashboard", "skills_manifest.db"),
        )
        return True
    except Exception as e:
        app.logger.warning("skill manifest rebuild failed: %s", e)
        return False
```

Replace `api_skill_save` (`app.py:5015-5030`) with:

```python
@app.route('/api/skills/save', methods=['POST'])
def api_skill_save():
    import stat as _stat
    from dashboard.skill_io import compose_skill_source
    data = request.json or {}
    scope = (data.get('scope') or 'shared').strip()
    name = (data.get('name') or '').strip()
    code = data.get('code') or ''
    if not name or not code.strip():
        return jsonify({'error': 'name and code required'}), 400
    path, err, status = _resolve_skill_path(scope, name, for_create=True)
    if err:
        return jsonify(err), status
    source = compose_skill_source(
        summary=(data.get('summary') or '').strip(),
        when_to_use=(data.get('when_to_use') or '').strip(),
        category=(data.get('category') or 'general').strip(),
        args=data.get('args') or {},
        body_source=code,
    )
    with open(path, 'w') as f:
        f.write(source)
    os.chmod(path, os.stat(path).st_mode | _stat.S_IXUSR)
    try:
        _rebuild_skill_manifest()
    except Exception as e:
        app.logger.warning("manifest rebuild raised: %s", e)
    return jsonify({'success': True, 'path': path, 'scope': scope, 'name': name})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_skills_api.py -k save -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/app.py tests/test_skills_api.py
git commit -m "feat(skills): save composes SKILL_META, supports per-agent scope, rebuilds manifest"
```

---

## Task 5: `/api/skills/run` and `/api/skills/delete` become scope-aware

**Files:**
- Modify: `dashboard/app.py` — `api_skill_run` (`5032-5061`) and `api_skill_delete` (`5063-5075`)
- Test: `tests/test_skills_api.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_skills_api.py
def test_run_per_agent_skill(client, tmp_path):
    _write(tmp_path, "agents/simon_bately/skills/echo.py",
           "import os, json\nargs = json.loads(os.environ.get('SKILL_ARGS','{}'))\nprint(args.get('msg',''))\n")
    r = client.post("/api/skills/run", json={"scope": "simon_bately", "name": "echo", "args": {"msg": "hello"}})
    assert r.status_code == 200
    data = r.get_json()
    assert data["success"] is True
    assert "hello" in data["output"]


def test_delete_blocks_protected(client):
    r = client.post("/api/skills/delete", json={"scope": "shared", "name": "create_skill"})
    assert r.status_code == 400


def test_delete_per_agent(client, tmp_path):
    p = _write(tmp_path, "agents/simon_bately/skills/tmpskill.py", 'print("x")\n')
    r = client.post("/api/skills/delete", json={"scope": "simon_bately", "name": "tmpskill"})
    assert r.status_code == 200
    assert not p.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_skills_api.py -k "run_per_agent or delete" -v`
Expected: FAIL — run/delete ignore `scope`, look only in `skills/shared` → 404 for per-agent skills

- [ ] **Step 3: Write minimal implementation**

Replace `api_skill_run` (`app.py:5032-5061`) with:

```python
@app.route('/api/skills/run', methods=['POST'])
def api_skill_run():
    data = request.json or {}
    scope = (data.get('scope') or 'shared').strip()
    name = (data.get('name') or '').strip()
    args = data.get('args', {})
    path, err, status = _resolve_skill_path(scope, name)
    if err:
        return jsonify(err), status
    if not os.path.exists(path):
        return jsonify({'error': f'skill not found: {name}'}), 404
    import time as _time
    env = os.environ.copy()
    env['SKILL_ARGS'] = json.dumps(args)
    env['AGENT_ID'] = scope if scope != 'shared' else 'dashboard'
    t0 = _time.time()
    try:
        result = subprocess.run([VENV_PYTHON, path], capture_output=True, text=True, timeout=30, env=env)
        elapsed = int((_time.time() - t0) * 1000)
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout[:8000],
            'error': result.stderr[:2000] if result.returncode != 0 else '',
            'duration_ms': elapsed,
            'exit_code': result.returncode,
        })
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'timeout (30s)', 'output': ''})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'output': ''})
```

Replace `api_skill_delete` (`app.py:5063-5075`) with:

```python
@app.route('/api/skills/delete', methods=['POST'])
def api_skill_delete():
    data = request.json or {}
    scope = (data.get('scope') or 'shared').strip()
    name = (data.get('name') or '').strip()
    protected = {'create_skill', 'save_artifact', 'artifact_save', 'update_task'}
    if not name or name in protected:
        return jsonify({'error': 'cannot delete protected skill'}), 400
    path, err, status = _resolve_skill_path(scope, name)
    if err:
        return jsonify(err), status
    if not os.path.exists(path):
        return jsonify({'error': 'not found'}), 404
    os.remove(path)
    try:
        _rebuild_skill_manifest()
    except Exception as e:
        app.logger.warning("manifest rebuild raised: %s", e)
    return jsonify({'success': True})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_skills_api.py -v`
Expected: PASS (all skills_api tests pass)

- [ ] **Step 5: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/app.py tests/test_skills_api.py
git commit -m "feat(skills): run and delete endpoints are scope-aware"
```

---

## Task 6: Rewrite `skills.html` — grouped list + labeled fields + guided create

**Files:**
- Rewrite: `dashboard/templates/skills.html`

This is a UI task — verified manually (Task 7), not by pytest. Replace the entire file with the content below.

- [ ] **Step 1: Write the new template**

Overwrite `dashboard/templates/skills.html` with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Baza Empire — Skills Lab</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:#07070f;color:#e0e0e0;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
    a{color:inherit;text-decoration:none}
    .nav{background:#0d0d1e;border-bottom:1px solid #1a1a3a;padding:0 32px;display:flex;align-items:center;position:sticky;top:0;z-index:200;flex-wrap:nowrap;overflow:visible}
    .nav-link{padding:20px 18px;font-size:13px;font-weight:600;color:#666;border-bottom:3px solid transparent;white-space:nowrap}
    .nav-link.active{color:#e0e0e0;border-bottom-color:#e94560}
    .nav-dropdown{position:relative}
    .nav-submenu{display:none;position:absolute;top:100%;left:0;background:#0d0d1e;border:1px solid #1a1a3a;border-radius:0 0 8px 8px;min-width:160px;z-index:200;padding:4px 0}
    .nav-dropdown:hover>.nav-submenu,.nav-dropdown.open>.nav-submenu{display:block}
    .container{max-width:1400px;margin:0 auto;padding:28px 32px}
    .page-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:12px}
    .page-title{font-size:22px;font-weight:800;color:#fff}
    .page-sub{font-size:12px;color:#444;margin-top:3px}
    .skills-stats{display:flex;gap:16px}
    .skill-stat{font-size:12px;color:#444}
    .skill-stat span{color:#aaa;font-weight:700;margin-left:4px}
    .skills-layout{display:grid;grid-template-columns:320px 1fr;border:1px solid #1a1a2e;border-radius:12px;overflow:hidden;min-height:640px}

    /* Sidebar */
    .skills-sidebar{background:#0a0a18;border-right:1px solid #1a1a2e;display:flex;flex-direction:column}
    .sidebar-header{padding:14px 16px;border-bottom:1px solid #1a1a2e}
    .sidebar-search{width:100%;background:#111;border:1px solid #1a1a2e;border-radius:6px;padding:8px 12px;color:#e0e0e0;font-size:12px;outline:none}
    .sidebar-search:focus{border-color:#7c3aed}
    .skill-list{flex:1;overflow-y:auto;padding:8px}
    .cat-group{margin-bottom:6px}
    .cat-head{display:flex;align-items:center;gap:6px;padding:8px 10px;cursor:pointer;color:#6a6a8a;font-size:11px;font-weight:800;letter-spacing:1px;text-transform:uppercase;user-select:none}
    .cat-head:hover{color:#c4b5fd}
    .cat-head .chev{font-size:9px;transition:transform .15s}
    .cat-group.collapsed .chev{transform:rotate(-90deg)}
    .cat-count{color:#3a3a5a;font-weight:700}
    .cat-body.collapsed{display:none}
    .skill-item{padding:9px 12px;border-radius:7px;cursor:pointer;border:1px solid transparent;margin:0 0 2px 6px}
    .skill-item:hover{background:#111;border-color:#1a1a2e}
    .skill-item.active{background:#1a0a3a;border-color:#7c3aed}
    .skill-item-row{display:flex;align-items:center;justify-content:space-between;gap:6px}
    .skill-item-name{font-size:13px;font-weight:600;color:#ccc;font-family:monospace}
    .skill-item.active .skill-item-name{color:#c4b5fd}
    .skill-item-summary{font-size:10px;color:#555;margin-top:2px;line-height:1.3}
    .skill-scope{font-size:9px;padding:1px 6px;border-radius:8px;white-space:nowrap}
    .scope-shared{background:#0a2a1a;color:#00d084;border:1px solid #00d084}
    .scope-agent{background:#1a0a3a;color:#c4b5fd;border:1px solid #7c3aed}
    .sidebar-new-btn{margin:8px;padding:11px;background:#1a0a3a;border:1px dashed #7c3aed;border-radius:7px;color:#c4b5fd;font-size:12px;font-weight:700;cursor:pointer;text-align:center}
    .sidebar-new-btn:hover{background:#250e50}

    /* Editor */
    .skills-editor{display:flex;flex-direction:column;background:#0e0e1e;min-width:0}
    .editor-head{padding:14px 18px;border-bottom:1px solid #1a1a2e;background:#0a0a18;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
    .editor-head .title{font-size:15px;font-weight:800;color:#fff;font-family:monospace}
    .editor-head .spacer{flex:1}
    .meta-form{padding:18px;display:flex;flex-direction:column;gap:14px;overflow-y:auto}
    .field{display:flex;flex-direction:column;gap:5px}
    .field label{font-size:11px;font-weight:700;color:#6a6a8a;text-transform:uppercase;letter-spacing:.5px}
    .field .hint{font-size:10px;color:#3a3a5a;font-weight:500;text-transform:none;letter-spacing:0}
    .fin{background:#111;border:1px solid #2a2a4a;border-radius:6px;padding:9px 12px;color:#e0e0e0;font-size:13px;outline:none;width:100%}
    .fin:focus{border-color:#7c3aed}
    .fin:read-only{opacity:.6;cursor:not-allowed}
    .row2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
    select.fin{cursor:pointer}
    .args-table{display:flex;flex-direction:column;gap:6px}
    .arg-row{display:grid;grid-template-columns:160px 1fr 32px;gap:8px;align-items:center}
    .arg-row .rm{background:#2a0d0d;color:#ff6666;border:1px solid #5a1a1a;border-radius:6px;cursor:pointer;padding:8px;font-size:12px}
    .add-arg{align-self:flex-start;background:#111;color:#aaa;border:1px solid #2a2a4a;border-radius:6px;padding:6px 12px;font-size:11px;font-weight:700;cursor:pointer}
    .add-arg:hover{border-color:#7c3aed;color:#c4b5fd}

    .code-section{border-top:1px solid #1a1a2e;margin-top:4px}
    .code-head{padding:10px 18px;display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none;color:#6a6a8a;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:1px}
    .code-head:hover{color:#c4b5fd}
    .code-head .chev{font-size:9px;transition:transform .15s}
    .code-section.collapsed .chev{transform:rotate(-90deg)}
    .code-section.collapsed .code-editor,.code-section.collapsed .gen-skel{display:none}
    .code-editor{width:100%;background:#050510;border:none;border-top:1px solid #1a1a2e;color:#c9d1d9;font-family:'Cascadia Code','Fira Code','Courier New',monospace;font-size:13px;padding:18px;outline:none;resize:vertical;line-height:1.6;min-height:280px;tab-size:4}
    .gen-skel{margin:10px 18px}

    .btn{padding:9px 16px;border-radius:7px;font-size:12px;font-weight:700;cursor:pointer;border:none}
    .btn:hover{opacity:.88}
    .btn-primary{background:linear-gradient(135deg,#e94560,#7c3aed);color:#fff}
    .btn-secondary{background:#111;color:#aaa;border:1px solid #2a2a4a}
    .btn-secondary:hover{border-color:#7c3aed;color:#c4b5fd}
    .btn-success{background:#0d2a0d;color:#66cc66;border:1px solid #1a5a1a}
    .btn-danger{background:#2a0d0d;color:#ff6666;border:1px solid #5a1a1a}
    .btn-sm{padding:7px 13px;font-size:11px}

    .run-panel{border-top:1px solid #1a1a2e;background:#080814}
    .run-head{padding:10px 18px;display:flex;align-items:center;gap:10px;cursor:pointer;user-select:none}
    .run-title{font-size:11px;font-weight:800;color:#444;text-transform:uppercase;letter-spacing:1px;flex:1}
    .run-body{padding:14px 18px;display:none}
    .run-body.open{display:block}
    .run-args{width:100%;background:#111;border:1px solid #2a2a4a;border-radius:6px;padding:9px 12px;color:#e0e0e0;font-size:12px;font-family:monospace;outline:none}
    .run-output{margin-top:10px;background:#050510;border:1px solid #1a1a2e;border-radius:7px;padding:14px;font-family:monospace;font-size:12px;color:#8b949e;max-height:220px;overflow-y:auto;white-space:pre-wrap;display:none}
    .run-output.visible{display:block}
    .run-output.success{color:#00d084}
    .run-output.error{color:#ff6666}
    .run-status{font-size:11px;color:#444;margin-top:6px}

    .empty-editor{display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;color:#333;gap:12px;padding:60px}
    .empty-editor .icon{font-size:48px;opacity:.2}
    .empty-editor h3{font-size:16px;color:#444}
    .empty-editor p{font-size:12px;text-align:center;line-height:1.6}
    .footer{text-align:center;padding:28px;color:#222;font-size:12px;margin-top:40px}
    @media(max-width:768px){.skills-layout{grid-template-columns:1fr}.row2{grid-template-columns:1fr}}
  </style>
</head>
<body>

{% set nav_active = 'skills' %}
{% include "_nav.html" %}

<div class="container">
  <div class="page-header">
    <div>
      <div class="page-title">⚙ Skills Lab</div>
      <div class="page-sub">Browse what each skill does, customize it, or build a new one — for shared use or a specific agent.</div>
    </div>
    <div class="skills-stats">
      <div class="skill-stat">Shared:<span id="count-shared">—</span></div>
      <div class="skill-stat">Agent:<span id="count-agent">—</span></div>
      <div class="skill-stat">Total:<span id="count-total">—</span></div>
    </div>
  </div>

  <div class="skills-layout">
    <!-- Sidebar -->
    <div class="skills-sidebar">
      <div class="sidebar-header">
        <input class="sidebar-search" type="text" placeholder="🔍 Search skills..." oninput="filterSkills(this.value)" id="skills-search">
      </div>
      <div class="skill-list" id="skills-list"><div style="text-align:center;padding:30px;color:#333;font-size:12px">Loading…</div></div>
      <div class="sidebar-new-btn" onclick="newSkill()">+ New Skill</div>
    </div>

    <!-- Editor -->
    <div class="skills-editor" id="editor-area">
      <div class="empty-editor" id="editor-empty">
        <div class="icon">⚙</div>
        <h3>Skills Lab</h3>
        <p>Pick a skill to see what it does and customize it.<br>Or click <b>+ New Skill</b> to build one with a guided form.</p>
      </div>

      <div id="editor-body" style="display:none;flex-direction:column;min-height:0">
        <div class="editor-head">
          <span class="title" id="ed-title">new_skill</span>
          <span class="spacer"></span>
          <button class="btn btn-success btn-sm" onclick="saveSkill()">💾 Save</button>
          <button class="btn btn-primary btn-sm" onclick="toggleRun()">▶ Run</button>
          <button class="btn btn-danger btn-sm" onclick="deleteSkill()" id="del-btn">✕ Delete</button>
        </div>

        <div class="meta-form">
          <div class="row2">
            <div class="field">
              <label>Skill name <span class="hint">— snake_case, locked after creation</span></label>
              <input class="fin" id="f-name" placeholder="generate_quote">
            </div>
            <div class="field">
              <label>Owner / scope <span class="hint">— who can use it</span></label>
              <select class="fin" id="f-scope"></select>
            </div>
          </div>
          <div class="field">
            <label>What it does <span class="hint">— one line; shown in the list</span></label>
            <input class="fin" id="f-summary" placeholder="Create a PDF quote for a project">
          </div>
          <div class="field">
            <label>When to use it <span class="hint">— helps the agent pick this skill</span></label>
            <input class="fin" id="f-when" placeholder="When a client asks for a price">
          </div>
          <div class="field">
            <label>Category</label>
            <select class="fin" id="f-category"></select>
          </div>
          <div class="field">
            <label>Arguments <span class="hint">— name + what it means</span></label>
            <div class="args-table" id="args-table"></div>
            <button class="add-arg" onclick="addArgRow()">+ add argument</button>
          </div>
        </div>

        <div class="code-section collapsed" id="code-section">
          <div class="code-head" onclick="toggleCode()">
            <span class="chev">▾</span> Advanced — Python code
          </div>
          <button class="btn btn-secondary btn-sm gen-skel" onclick="generateSkeleton()">⤵ Generate skeleton from fields above</button>
          <textarea class="code-editor" id="f-code" spellcheck="false"></textarea>
        </div>

        <div class="run-panel">
          <div class="run-head" onclick="toggleRun()">
            <span class="run-title">▶ Run panel</span>
            <span id="run-chev" style="color:#444">▾</span>
          </div>
          <div class="run-body" id="run-body">
            <div style="display:flex;gap:8px;align-items:center">
              <input class="run-args" id="run-args" placeholder='JSON args: {"key":"value"} — empty = {}'>
              <button class="btn btn-success btn-sm" onclick="runSkill()" id="run-btn">▶ Run Now</button>
            </div>
            <pre class="run-output" id="run-output"></pre>
            <div class="run-status" id="run-status"></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="footer">Baza Empire Agent Framework v3 — All Home Building Co LLC</div>

<script>
const CATEGORIES = ["financial","materials","project","client","marketing","infrastructure","data","code","ai","web","document","general"];
let allSkills = [];
let current = null;           // {scope, name} of the open skill, or null for new
let runOpen = false;
const collapsed = {};          // category -> bool

function updateClock(){ const c=document.getElementById('nav-clock'); if(c) c.textContent=new Date().toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'}); }
setInterval(updateClock,1000); updateClock();

function esc(s){ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

async function loadSkills(){
  try{
    allSkills = await (await fetch('/api/skills/list')).json();
    document.getElementById('count-shared').textContent = allSkills.filter(s=>s.scope==='shared').length;
    document.getElementById('count-agent').textContent = allSkills.filter(s=>s.scope!=='shared').length;
    document.getElementById('count-total').textContent = allSkills.length;
    populateScopeOptions();
    renderList(allSkills);
  }catch(e){
    document.getElementById('skills-list').innerHTML = '<div style="color:#ff5555;font-size:12px;padding:16px">Error: '+esc(e.message)+'</div>';
  }
}

function agentScopes(){
  return [...new Set(allSkills.filter(s=>s.scope!=='shared').map(s=>s.scope))].sort();
}

function populateScopeOptions(){
  const sel = document.getElementById('f-scope');
  const opts = ['shared', ...agentScopes()];
  sel.innerHTML = opts.map(o=>`<option value="${esc(o)}">${o==='shared'?'Shared (all agents)':esc(o)}</option>`).join('');
  document.getElementById('f-category').innerHTML = CATEGORIES.map(c=>`<option value="${c}">${c}</option>`).join('');
}

function renderList(skills){
  const el = document.getElementById('skills-list');
  if(!skills.length){ el.innerHTML='<div style="color:#333;font-size:12px;padding:16px;text-align:center">No skills found</div>'; return; }
  const groups = {};
  skills.forEach(s=>{ (groups[s.category||'general'] ||= []).push(s); });
  const cats = Object.keys(groups).sort((a,b)=>CATEGORIES.indexOf(a)-CATEGORIES.indexOf(b));
  el.innerHTML = cats.map(cat=>{
    const isC = !!collapsed[cat];
    const rows = groups[cat].map(s=>{
      const active = current && current.scope===s.scope && current.name===s.name;
      return `<div class="skill-item ${active?'active':''}" onclick="openSkill('${esc(s.scope)}','${esc(s.name)}')">
        <div class="skill-item-row">
          <span class="skill-item-name">${esc(s.name)}</span>
          <span class="skill-scope ${s.scope==='shared'?'scope-shared':'scope-agent'}">${s.scope==='shared'?'shared':esc(s.scope)}</span>
        </div>
        ${s.summary?`<div class="skill-item-summary">${esc(s.summary).substring(0,60)}${s.summary.length>60?'…':''}</div>`:''}
      </div>`;
    }).join('');
    return `<div class="cat-group ${isC?'collapsed':''}">
      <div class="cat-head" onclick="toggleCat('${cat}')"><span class="chev">▾</span>${cat} <span class="cat-count">(${groups[cat].length})</span></div>
      <div class="cat-body ${isC?'collapsed':''}">${rows}</div>
    </div>`;
  }).join('');
}

function toggleCat(cat){ collapsed[cat]=!collapsed[cat]; renderList(currentFilter()); }

let _filterQ = '';
function currentFilter(){
  const q=_filterQ.toLowerCase();
  if(!q) return allSkills;
  return allSkills.filter(s=> s.name.toLowerCase().includes(q) || (s.summary||'').toLowerCase().includes(q) || (s.category||'').toLowerCase().includes(q));
}
function filterSkills(q){ _filterQ=q; renderList(currentFilter()); }

function showEditor(){ document.getElementById('editor-empty').style.display='none'; document.getElementById('editor-body').style.display='flex'; }

function setArgs(args){
  const t=document.getElementById('args-table'); t.innerHTML='';
  Object.entries(args||{}).forEach(([k,v])=>addArgRow(k,v));
}
function addArgRow(k='',v=''){
  const t=document.getElementById('args-table');
  const row=document.createElement('div'); row.className='arg-row';
  row.innerHTML=`<input class="fin arg-k" placeholder="arg_name" value="${esc(k)}">
    <input class="fin arg-v" placeholder="what it means" value="${esc(v)}">
    <button class="rm" onclick="this.parentElement.remove()">✕</button>`;
  t.appendChild(row);
}
function collectArgs(){
  const out={};
  document.querySelectorAll('#args-table .arg-row').forEach(r=>{
    const k=r.querySelector('.arg-k').value.trim(); const v=r.querySelector('.arg-v').value.trim();
    if(k) out[k]=v;
  });
  return out;
}

async function openSkill(scope,name){
  current={scope,name};
  try{
    const data = await (await fetch(`/api/skills/read/${encodeURIComponent(scope)}/${encodeURIComponent(name)}`)).json();
    if(data.error) throw new Error(data.error);
    showEditor();
    document.getElementById('ed-title').textContent=name;
    const ni=document.getElementById('f-name'); ni.value=data.name; ni.readOnly=true;
    const sc=document.getElementById('f-scope'); sc.value=data.scope; sc.disabled=true;
    document.getElementById('f-summary').value=data.summary||'';
    document.getElementById('f-when').value=data.when_to_use||'';
    document.getElementById('f-category').value=data.category||'general';
    setArgs(data.args);
    document.getElementById('f-code').value=data.code||'';
    document.getElementById('del-btn').style.display='inline-block';
    renderList(currentFilter());
  }catch(e){ alert('Error loading skill: '+e.message); }
}

function newSkill(){
  current=null;
  showEditor();
  document.getElementById('ed-title').textContent='new skill';
  const ni=document.getElementById('f-name'); ni.value=''; ni.readOnly=false;
  const sc=document.getElementById('f-scope'); sc.disabled=false; sc.value='shared';
  document.getElementById('f-summary').value='';
  document.getElementById('f-when').value='';
  document.getElementById('f-category').value='general';
  setArgs({});
  document.getElementById('f-code').value='';
  document.getElementById('del-btn').style.display='none';
  document.getElementById('code-section').classList.remove('collapsed');
  renderList(currentFilter());
}

function generateSkeleton(){
  const args=collectArgs();
  const argHints=Object.keys(args).map(k=>`# args[${JSON.stringify(k)}] — ${args[k]}`).join('\n');
  document.getElementById('f-code').value =
`import os, json

args = json.loads(os.environ.get('SKILL_ARGS', '{}'))
${argHints?argHints+'\n':''}
# Your skill logic here
result = "ok"
print(result)
`;
  document.getElementById('code-section').classList.remove('collapsed');
}

async function saveSkill(){
  const name=document.getElementById('f-name').value.trim();
  const scope=document.getElementById('f-scope').value;
  if(!name){ alert('Skill name is required'); return; }
  if(!/^[a-z][a-z0-9_]{1,49}$/.test(name)){ alert('Name must be snake_case (a-z, 0-9, underscore), 2–50 chars'); return; }
  let code=document.getElementById('f-code').value;
  if(!code.trim()){ generateSkeleton(); code=document.getElementById('f-code').value; }
  const payload={ scope, name,
    summary:document.getElementById('f-summary').value.trim(),
    when_to_use:document.getElementById('f-when').value.trim(),
    category:document.getElementById('f-category').value,
    args:collectArgs(), code };
  try{
    const res=await fetch('/api/skills/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await res.json();
    if(!res.ok) throw new Error(data.error||'Save failed');
    current={scope,name};
    document.getElementById('f-name').readOnly=true;
    document.getElementById('f-scope').disabled=true;
    document.getElementById('del-btn').style.display='inline-block';
    await loadSkills();
    toast('Saved: '+name);
  }catch(e){ alert('Error: '+e.message); }
}

async function runSkill(){
  if(!current){ alert('Save the skill before running it.'); return; }
  let args; try{ args=JSON.parse(document.getElementById('run-args').value.trim()||'{}'); }
  catch(e){ alert('Invalid JSON args: '+e.message); return; }
  const btn=document.getElementById('run-btn'), out=document.getElementById('run-output'), st=document.getElementById('run-status');
  btn.textContent='⏳ Running…'; btn.disabled=true; out.className='run-output visible'; out.textContent='…'; st.textContent='';
  try{
    const t0=Date.now();
    const data=await (await fetch('/api/skills/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:current.scope,name:current.name,args})})).json();
    const ms=Date.now()-t0;
    if(data.success){ out.textContent=data.output||'(no output)'; out.className='run-output visible success'; st.textContent=`✓ ${data.duration_ms||ms}ms`; }
    else{ out.textContent=data.error||'Unknown error'; out.className='run-output visible error'; st.textContent=`✗ failed after ${ms}ms`; }
  }catch(e){ out.textContent=e.message; out.className='run-output visible error'; }
  finally{ btn.textContent='▶ Run Now'; btn.disabled=false; }
}

async function deleteSkill(){
  if(!current){ return; }
  if(!confirm(`Delete skill "${current.name}"? This cannot be undone.`)) return;
  try{
    const res=await fetch('/api/skills/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:current.scope,name:current.name})});
    const data=await res.json();
    if(!res.ok) throw new Error(data.error);
    const n=current.name; current=null;
    document.getElementById('editor-body').style.display='none';
    document.getElementById('editor-empty').style.display='flex';
    await loadSkills();
    toast('Deleted: '+n);
  }catch(e){ alert('Error: '+e.message); }
}

function toggleCode(){ document.getElementById('code-section').classList.toggle('collapsed'); }
function toggleRun(){ runOpen=!runOpen; document.getElementById('run-body').classList.toggle('open',runOpen); document.getElementById('run-chev').textContent=runOpen?'▴':'▾'; }

function toast(msg){
  const t=document.createElement('div'); t.textContent=msg;
  Object.assign(t.style,{position:'fixed',bottom:'24px',right:'24px',background:'#1a0a3a',border:'1px solid #7c3aed',color:'#c4b5fd',padding:'12px 20px',borderRadius:'8px',fontSize:'13px',fontWeight:'600',zIndex:'9999',transition:'opacity .4s'});
  document.body.appendChild(t); setTimeout(()=>{t.style.opacity='0';setTimeout(()=>t.remove(),400)},2500);
}

loadSkills();
</script>
</body>
</html>
```

- [ ] **Step 2: Verify template renders (no server restart needed for syntax check)**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('dashboard/templates')).get_template('skills.html'); print('template OK')"`
Expected: `template OK` (Jinja parses the file; `_nav.html` include resolves)

- [ ] **Step 3: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/templates/skills.html
git commit -m "feat(skills): grouped list + labeled-fields editor + guided create UI"
```

---

## Task 7: Restart dashboard, manual smoke test, full suite

**Files:** none (verification + integration)

- [ ] **Step 1: Run the full skill test set**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_skill_io.py tests/test_skills_api.py -v`
Expected: PASS (all green)

- [ ] **Step 2: Run the broader suite to confirm no regressions**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest -q 2>&1 | tail -20`
Expected: Same pass count as before plus the new tests; the only pre-existing failure should be `test_split_detection` (unrelated — documented in the session log). If any OTHER test fails, stop and investigate before continuing.

- [ ] **Step 3: Restart the dashboard (Jinja template cache — required)**

Run: `sudo systemctl restart baza-dashboard.service && sleep 2 && systemctl is-active baza-dashboard.service`
Expected: `active`

- [ ] **Step 4: Smoke test the live endpoints**

Run:
```bash
curl -s localhost:8888/api/skills/list | python3 -c "import sys,json; d=json.load(sys.stdin); print('skills:',len(d)); print('sample:',{k:d[0][k] for k in ('name','scope','category','summary')} if d else 'none')"
```
Expected: a non-zero skill count and a sample row showing `category` + `summary` populated.

- [ ] **Step 5: Manual browser check**

Open `http://localhost:8888/skills` (or via Tailscale Serve) and confirm:
- Skills are grouped under category headers with counts; headers collapse/expand.
- Clicking a skill fills the labeled fields (What it does / When to use / Category / Arguments) and the code shows under "Advanced — Python code".
- `+ New Skill` → fill fields → "Generate skeleton" → Save creates the file; it appears in the list under its category.
- Switch scope to an agent on a new skill → Save → it lands under that agent (verify it shows with a purple agent badge).
- Editing an existing skill's summary and saving updates the list summary; the code body below `SKILL_META` is unchanged (open it again and confirm logic intact).
- Run an existing skill from the Run panel; output shows.

- [ ] **Step 6: Append session-log entry**

Get timestamp: `date '+%Y-%m-%d %H:%M'`
Append to `~/Desktop/baza-session-log.md`:
```
### <timestamp> | Skills page rebuilt — shipped
- /skills page: category-grouped list, labeled metadata fields (summary/when_to_use/category/args), guided create form + scope selector (shared + per-agent), Advanced code section. Backend: skill_io.compose_skill_source (preserves code body), scope-aware read/save/run/delete, SKILL_META persisted, manifest rebuilt on save/delete. Fixes prior silent description-discard bug. Tests: test_skill_io + test_skills_api. baza-dashboard restarted.
```

- [ ] **Step 7: Final commit (if the auto-git timer hasn't already)**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add -A && git commit -m "chore(skills): session log + final integration" || echo "nothing to commit (auto-git may have run)"
```

---

## Self-Review Notes

**Spec coverage:**
- Make purpose scannable → Task 2 (list enriched) + Task 6 (grouped list w/ summaries). ✓
- Guided create + raw editor ("Both") → Task 6 (`newSkill`, `generateSkeleton`, always-available code section). ✓
- Editable metadata via labeled fields, persisted, manifest rebuilt → Task 1 + Task 4. ✓
- Shared + per-agent scope → Task 3/4/5 (`_resolve_skill_path`) + Task 6 scope selector. ✓
- Label what's what (grouped, badges, field labels) → Task 6. ✓
- Save preserves code body verbatim → Task 1 (`_strip_header`/`compose_skill_source`) + tests. ✓
- Path-traversal guard → Task 3 (`_resolve_skill_path`) + test. ✓
- Manifest rebuild non-fatal → Task 4 (`_rebuild_skill_manifest` + survives-failure test). ✓
- Protected delete list preserved → Task 5. ✓

**Type/name consistency:** `_resolve_skill_path(scope, name, for_create=False)` returns `(path, err, status)` — used identically in Tasks 3/4/5. `compose_skill_source(summary, when_to_use, category, args, body_source)` signature matches Task 1 def and Task 4 call. Frontend `current = {scope, name}` used consistently in open/run/delete. Route `/api/skills/read/<scope>/<skill_name>` matches frontend `openSkill(scope,name)` URL.

**Placeholders:** none — all steps contain runnable code/commands.
