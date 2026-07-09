# ahb123.com Click-to-Edit Source Editor (Phase B-iii) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Click-to-edit the public ahb123.com site from the `/web` tab: a stamped preview build in an iframe, source edits written back to `web/ahb123/content/*.html`, and a Draft → Publish flow that deploys to Cloudflare Pages.

**Architecture:** A new offset-preserving fragment editor (`web/ahb123/fragment_doc.py`, stdlib `html.parser`) is the core: it locates nodes by index path, rewrites text/attrs/order in the raw source, and stamps `data-edit-id="<slug>:<path>"` for preview builds only. `build.py` gains `build_preview()` (stamped, links rewritten to `/web/preview/ahb123/…`, editor JS injected). A new dashboard blueprint (`dashboard/web_source_editor.py`) serves the preview and exposes edit/upload/meta/draft/log/publish APIs. `edit.js` gains a "source mode" (activated by the injected `window.BAZA_SOURCE_EDIT`) that saves to those APIs instead of overrides. Publish reuses `web_site_routes.deploy_site()` in a background thread with status polling.

**Tech Stack:** Python stdlib (`html.parser`, `importlib`, `threading`, `subprocess`), Flask blueprint, ES5 JavaScript, pytest.

## Global Constraints

- **Local-first, zero new dependencies.** stdlib + Flask server-side; plain ES5 in `edit.js`.
- **Source-edit endpoints are locked to the `web/ahb123/{content,assets}` subtree.** Reject `..`, absolute paths, unknown slugs, and non-allow-listed upload extensions (`{.png,.jpg,.jpeg,.gif,.webp,.svg}`, 15 MB cap — same as `ui_editor.py`).
- **Fragment writes are atomic** (write tmp file in the same directory, `os.replace`). A parse failure returns 422 and leaves the file untouched.
- **Published builds (`dist/`) are NEVER stamped** with `data-edit-id` and never carry the editor script. Only `.preview/` builds are.
- `web/ahb123/.preview/` is a build artifact: **must be added to `.gitignore`** in the same task that creates it (claw-auto-git commits hourly and must not pick it up).
- Edit id format: `"<slug>:<dotted-path>"` where dotted-path is element-only child indices from the fragment root, e.g. `home:0.2.1`. Empty path (`"home:"`) addresses the fragment's virtual root (used only by `reorder`).
- Valid slugs are exactly `build.py`'s `SLUGS = ["home","services","portfolio","about","contact","plan"]`.
- `web/ahb123/` is not a Python package: dashboard code and tests load its modules **by path** via `importlib.util.spec_from_file_location` (existing pattern: `tests/ahb123_util.py::load`).
- Every task that changes `edit.js`/`edit.css` bumps `?v=` in `dashboard/templates/_nav.html` (both lines) and updates `test_asset_version_bumped`. **B-ii ends at `v=9`, so this plan's edit.js task bumps to `v=10`.** If B-ii has not run yet, bump from whatever the current number is and update the test to match.
- Template changes require `sudo systemctl restart baza-dashboard`.
- Run tests with `venv/bin/python -m pytest` from `/home/switchhacker/baza-empire/agent-framework-v3`.
- Text edits store **plain text** (server escapes with `html.escape`) — the editor never injects raw HTML.

---

### Task 1: `fragment_doc.py` — offset-preserving fragment editor

**Files:**
- Create: `web/ahb123/fragment_doc.py`
- Test: `tests/test_fragment_doc.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces (Tasks 2 & 4 depend on these exact signatures):
  - `class FragmentParseError(ValueError)` — malformed/unbalanced HTML
  - `class FragmentEditError(ValueError)` — valid HTML, invalid operation
  - `class FragmentDoc:`
    - `__init__(self, src: str)` — parses; raises `FragmentParseError`
    - `node(self, path: tuple) -> _Node` — raises `KeyError` if absent; `_Node` has `.tag`, `.attrs` (list of `(name, value)` pairs), `.path`, `.children`
    - `set_text(self, path, text: str) -> str` — new source; refuses void elements and elements with child elements (`FragmentEditError`)
    - `set_attr(self, path, name: str, value) -> str` — set/replace attr; `value=None` removes it
    - `reorder(self, path, order: list) -> str` — `order` is a permutation of `range(n_children)` giving the new sequence by old index; `path=()` reorders the fragment's root elements
    - `stamped(self, prefix: str) -> str` — every element start tag gains `data-edit-id="<prefix>:<dotted-path>"`
  - `parse_path(s: str) -> tuple` — `"0.2.1"` → `(0,2,1)`, `""` → `()`; raises `ValueError` on junk

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fragment_doc.py`:

```python
# tests/test_fragment_doc.py — offset-preserving editor for ahb123 content fragments
import pytest
from ahb123_util import load

fd = load("fragment_doc")

SAMPLE = """<!-- header comment -->
<div class="hero">
  <h1>Big Title</h1>
  <p>Some &amp; text</p>
  <a href="/plan" class="btn">Go</a>
</div>
<section style="padding:4px;">
  <img src="/s/one.jpg" alt="pic">
  <div><span>nested</span></div>
</section>"""


def test_parse_and_paths():
    doc = fd.FragmentDoc(SAMPLE)
    assert [n.tag for n in doc.roots] == ["div", "section"]
    assert doc.node((0, 0)).tag == "h1"
    assert doc.node((1, 0)).tag == "img"      # void element
    assert doc.node((1, 1, 0)).tag == "span"
    with pytest.raises(KeyError):
        doc.node((5,))


def test_parse_path():
    assert fd.parse_path("0.2.1") == (0, 2, 1)
    assert fd.parse_path("") == ()
    with pytest.raises(ValueError):
        fd.parse_path("0.x")


def test_set_text_replaces_only_that_node():
    doc = fd.FragmentDoc(SAMPLE)
    out = doc.set_text((0, 0), 'New <Title> & Co')
    assert "<h1>New &lt;Title&gt; &amp; Co</h1>" in out
    assert "Some &amp; text" in out            # untouched sibling keeps its entity
    assert "<!-- header comment -->" in out    # comments preserved


def test_set_text_refuses_void_and_parents():
    doc = fd.FragmentDoc(SAMPLE)
    with pytest.raises(fd.FragmentEditError):
        doc.set_text((1, 0), "x")              # img is void
    with pytest.raises(fd.FragmentEditError):
        doc.set_text((0,), "x")                # div has element children


def test_set_attr_replace_add_remove():
    doc = fd.FragmentDoc(SAMPLE)
    out = doc.set_attr((1, 0), "src", "/s/two.jpg")
    assert 'src="/s/two.jpg"' in out and 'alt="pic"' in out
    out2 = doc.set_attr((0, 2), "target", "_blank")
    assert 'target="_blank"' in out2 and 'href="/plan"' in out2
    out3 = doc.set_attr((1,), "style", None)
    assert "<section>" in out3


def test_attr_value_escaped():
    doc = fd.FragmentDoc(SAMPLE)
    out = doc.set_attr((0, 2), "title", 'say "hi" & bye')
    assert 'title="say &quot;hi&quot; &amp; bye"' in out


def test_reorder_children_and_roots():
    doc = fd.FragmentDoc(SAMPLE)
    out = doc.reorder((0,), [2, 0, 1])
    assert out.index('<a href="/plan"') < out.index("<h1>") < out.index("<p>")
    out2 = doc.reorder((), [1, 0])
    assert out2.index("<section") < out2.index('<div class="hero">')
    with pytest.raises(fd.FragmentEditError):
        doc.reorder((0,), [0, 1])              # not a permutation of 3 children


def test_stamped_ids_and_roundtrip_stability():
    doc = fd.FragmentDoc(SAMPLE)
    st = doc.stamped("home")
    assert '<div data-edit-id="home:0" class="hero">' in st
    assert '<h1 data-edit-id="home:0.0">' in st
    assert '<img data-edit-id="home:1.0" src="/s/one.jpg"' in st
    # source itself never gains ids, and stamping twice from source is stable
    assert "data-edit-id" not in SAMPLE
    assert fd.FragmentDoc(SAMPLE).stamped("home") == st


def test_edit_then_reparse_paths_stable():
    doc = fd.FragmentDoc(SAMPLE)
    out = doc.set_text((0, 0), "Changed")
    doc2 = fd.FragmentDoc(out)
    assert doc2.node((0, 0)).tag == "h1"
    assert doc2.node((1, 1, 0)).tag == "span"  # paths unaffected by the edit


def test_unbalanced_html_raises():
    with pytest.raises(fd.FragmentParseError):
        fd.FragmentDoc("<div><p>oops</div>")
    with pytest.raises(fd.FragmentParseError):
        fd.FragmentDoc("<div>never closed")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_fragment_doc.py -v`
Expected: FAIL — `load("fragment_doc")` cannot find the module.

- [ ] **Step 3: Implement `web/ahb123/fragment_doc.py`**

```python
#!/usr/bin/env python3
"""Offset-preserving editor for ahb123 content fragments (stdlib only).

Parses a fragment into an element tree that remembers exact source offsets,
so edits rewrite only the targeted span — comments, entities, whitespace and
untouched markup survive byte-for-byte. Used by build.py --preview (stamping)
and the dashboard source-edit endpoints.
"""
from html import escape
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}


class FragmentParseError(ValueError):
    pass


class FragmentEditError(ValueError):
    pass


def parse_path(s):
    """'0.2.1' -> (0, 2, 1); '' -> ()."""
    if s == "":
        return ()
    try:
        return tuple(int(p) for p in s.split("."))
    except ValueError:
        raise ValueError("bad path %r" % s)


class _Node(object):
    __slots__ = ("tag", "attrs", "path", "children",
                 "start", "start_end", "end", "outer_end")
    # start      offset of '<' of the start tag
    # start_end  offset just past '>' of the start tag
    # end        offset of '<' of the end tag (None for void/self-closing)
    # outer_end  offset just past the element (end tag '>' or start_end)


class _Parser(HTMLParser):
    def __init__(self, src):
        HTMLParser.__init__(self, convert_charrefs=False)
        self.src = src
        self._line_off = [0]
        for i, ch in enumerate(src):
            if ch == "\n":
                self._line_off.append(i + 1)
        self.roots = []
        self.stack = []

    def _off(self):
        line, col = self.getpos()
        return self._line_off[line - 1] + col

    def _add(self, node):
        siblings = self.stack[-1].children if self.stack else self.roots
        parent_path = self.stack[-1].path if self.stack else ()
        node.path = parent_path + (len(siblings),)
        siblings.append(node)

    def _new(self, tag, attrs):
        n = _Node()
        n.tag, n.attrs, n.children = tag, list(attrs), []
        n.start = self._off()
        n.start_end = n.start + len(self.get_starttag_text())
        n.end = None
        n.outer_end = n.start_end
        return n

    def handle_starttag(self, tag, attrs):
        n = self._new(tag, attrs)
        self._add(n)
        if tag not in VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self._add(self._new(tag, attrs))

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1].tag != tag:
            raise FragmentParseError(
                "unbalanced </%s> at line %d" % (tag, self.getpos()[0]))
        n = self.stack.pop()
        n.end = self._off()
        gt = self.src.find(">", n.end)
        if gt == -1:
            raise FragmentParseError("unterminated end tag </%s>" % tag)
        n.outer_end = gt + 1


class FragmentDoc(object):
    def __init__(self, src):
        self.src = src
        p = _Parser(src)
        try:
            p.feed(src)
            p.close()
        except FragmentParseError:
            raise
        except Exception as e:
            raise FragmentParseError(str(e))
        if p.stack:
            raise FragmentParseError("unclosed <%s>" % p.stack[-1].tag)
        self.roots = p.roots

    def node(self, path):
        nodes, n = self.roots, None
        for i in path:
            if not isinstance(i, int) or i < 0 or i >= len(nodes):
                raise KeyError("no node at %r" % (path,))
            n = nodes[i]
            nodes = n.children
        if n is None:
            raise KeyError("empty path addresses the virtual root")
        return n

    def set_text(self, path, text):
        n = self.node(path)
        if n.end is None:
            raise FragmentEditError("<%s> is a void element" % n.tag)
        if n.children:
            raise FragmentEditError(
                "<%s> has child elements; refusing text overwrite" % n.tag)
        return self.src[:n.start_end] + escape(str(text)) + self.src[n.end:]

    def set_attr(self, path, name, value):
        n = self.node(path)
        name = str(name).lower()
        attrs = [(k, v) for (k, v) in n.attrs if k != name]
        if value is not None:
            attrs.append((name, str(value)))
        # keep original position when replacing
        if value is not None and any(k == name for (k, _) in n.attrs):
            attrs = [(k, str(value) if k == name else v) for (k, v) in n.attrs]
        parts = [n.tag]
        for k, v in attrs:
            parts.append(k if v is None else '%s="%s"' % (k, escape(v, quote=True)))
        selfclose = self.src[n.start:n.start_end].rstrip().endswith("/>")
        tag_txt = "<" + " ".join(parts) + (" />" if selfclose else ">")
        return self.src[:n.start] + tag_txt + self.src[n.start_end:]

    def _children_of(self, path):
        return self.roots if path == () else self.node(path).children

    def reorder(self, path, order):
        kids = self._children_of(path)
        if sorted(order) != list(range(len(kids))):
            raise FragmentEditError(
                "order must be a permutation of 0..%d" % (len(kids) - 1))
        if len(kids) < 2:
            return self.src
        spans = [(c.start, c.outer_end) for c in kids]
        seps = [self.src[spans[i][1]:spans[i + 1][0]]
                for i in range(len(kids) - 1)]
        rebuilt = []
        for j, idx in enumerate(order):
            rebuilt.append(self.src[spans[idx][0]:spans[idx][1]])
            if j < len(seps):
                rebuilt.append(seps[j])
        return self.src[:spans[0][0]] + "".join(rebuilt) + self.src[spans[-1][1]:]

    def stamped(self, prefix):
        ins = []

        def walk(nodes):
            for n in nodes:
                dotted = ".".join(str(i) for i in n.path)
                ins.append((n.start + 1 + len(n.tag),
                            ' data-edit-id="%s:%s"' % (prefix, dotted)))
                walk(n.children)

        walk(self.roots)
        out = self.src
        for off, txt in sorted(ins, reverse=True):
            out = out[:off] + txt + out[off:]
        return out
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_fragment_doc.py -v`
Expected: all pass. Also sanity-check against real fragments:

```bash
venv/bin/python - <<'EOF'
import sys; sys.path.insert(0, 'tests')
from ahb123_util import load
fd = load("fragment_doc")
import os
for slug in ["home","services","portfolio","about","contact","plan"]:
    src = open(f"web/ahb123/content/{slug}.html", encoding="utf-8").read()
    doc = fd.FragmentDoc(src)
    st = doc.stamped(slug)
    print(slug, len(doc.roots), "roots,", st.count("data-edit-id"), "stamps")
EOF
```
Expected: all 6 fragments parse; stamp counts > 0. **If any real fragment fails to parse, fix the parser (not the fragment) and add the failing construct to the test file.**

- [ ] **Step 5: Commit**

```bash
git add web/ahb123/fragment_doc.py tests/test_fragment_doc.py
git commit -m "feat(ahb123): fragment_doc — offset-preserving fragment editor + stamping"
```

---

### Task 2: `build.py --preview` — stamped preview build

**Files:**
- Modify: `web/ahb123/build.py`
- Modify: `.gitignore` (add `web/ahb123/.preview/`)
- Test: `tests/test_ahb123_preview_build.py`

**Interfaces:**
- Consumes: Task 1's `FragmentDoc(...).stamped(slug)`.
- Produces: `build_preview(preview_dir=None) -> list[str]` in `build.py` (Tasks 3/4 call it by module load); `PREVIEW_PREFIX = "/web/preview/ahb123"`; preview pages contain `data-edit-id`, `window.BAZA_SOURCE_EDIT`, `/static/edit.js`, and root-relative links rewritten under `PREVIEW_PREFIX`. `build_site` (dist) output stays byte-identical to before.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ahb123_preview_build.py`:

```python
# tests/test_ahb123_preview_build.py — build.py --preview stamps + rewrites; dist stays clean
import os
import pytest
from ahb123_util import load

build = load("build")


@pytest.fixture(scope="module")
def preview(tmp_path_factory):
    d = tmp_path_factory.mktemp("preview")
    build.build_preview(str(d))
    return str(d)


def read(base, *parts):
    with open(os.path.join(base, *parts), encoding="utf-8") as f:
        return f.read()


def test_preview_pages_are_stamped_and_editor_injected(preview):
    html = read(preview, "index.html")
    assert 'data-edit-id="home:0"' in html
    assert "window.BAZA_SOURCE_EDIT" in html and '"slug":"home"' in html.replace("'", '"').replace(" ", "")
    assert "/static/edit.js" in html
    svc = read(preview, "services", "index.html")
    assert 'data-edit-id="services:' in svc


def test_preview_links_rewritten_assets_copied(preview):
    html = read(preview, "index.html")
    assert 'href="/web/preview/ahb123/plan"' in html
    assert 'href="/plan"' not in html
    # absolute externals untouched
    assert "https://ahb123.com" in html          # canonical
    assert os.path.isdir(os.path.join(preview, "s"))
    assert os.path.isfile(os.path.join(preview, "assets", "css", "brand.css"))


def test_dist_build_never_stamped(tmp_path):
    dist = tmp_path / "dist"
    build.build_site(str(dist))
    html = read(str(dist), "index.html")
    assert "data-edit-id" not in html
    assert "BAZA_SOURCE_EDIT" not in html
    assert 'href="/plan"' in html                # links NOT rewritten


def test_default_preview_dir_is_dot_preview_and_gitignored():
    assert os.path.basename(build.PREVIEW_DIR) == ".preview"
    root = os.path.dirname(os.path.dirname(os.path.dirname(build.__file__)))
    gi = read(root, ".gitignore")
    assert "web/ahb123/.preview/" in gi
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_ahb123_preview_build.py -v`
Expected: FAIL — `build_preview` / `PREVIEW_DIR` don't exist.

- [ ] **Step 3: Implement in `web/ahb123/build.py`**

Add after the existing imports/constants (`import re` joins the import line; also `import importlib.util`):

```python
PREVIEW_PREFIX = "/web/preview/ahb123"
PREVIEW_DIR = os.path.join(HERE, ".preview")
_EDIT_JS = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                        "dashboard", "static", "edit.js")
_LINK_RE = re.compile(r'\b(href|src)="/(?!/)')


def _load_fragment_doc():
    spec = importlib.util.spec_from_file_location(
        "ahb123_fragment_doc", os.path.join(HERE, "fragment_doc.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _editor_snippet(slug):
    v = int(os.path.getmtime(_EDIT_JS)) if os.path.exists(_EDIT_JS) else 0
    return (
        '<script>window.BAZA_SOURCE_EDIT={"site":"ahb123","slug":"%s"};</script>\n'
        '<link rel="stylesheet" href="/static/edit.css?v=%d">\n'
        '<script defer src="/static/edit.js?v=%d"></script>\n' % (slug, v, v))


def build_preview(preview_dir=None):
    """Editable preview: stamped fragments, links under PREVIEW_PREFIX,
    editor injected. NEVER used for dist/."""
    preview_dir = preview_dir or PREVIEW_DIR
    abs_p = os.path.abspath(preview_dir)
    if abs_p == HERE or HERE.startswith(abs_p + os.sep):
        raise ValueError("refusing to build into source tree: %s" % abs_p)
    fd = _load_fragment_doc()
    if os.path.isdir(preview_dir):
        shutil.rmtree(preview_dir)
    os.makedirs(preview_dir)
    with open(os.path.join(HERE, "content", "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    written = []
    for slug in SLUGS:
        with open(os.path.join(HERE, "content", "%s.html" % slug),
                  encoding="utf-8") as f:
            body = f.read()
        body = fd.FragmentDoc(body).stamped(slug)
        html = render_page(slug, meta[slug], body)
        html = _LINK_RE.sub(lambda m: '%s="%s/' % (m.group(1), PREVIEW_PREFIX), html)
        html = html.replace("</body>", _editor_snippet(slug) + "</body>")
        rel = _dist_relpath(slug)
        dest = os.path.join(preview_dir, rel)
        os.makedirs(os.path.dirname(dest) or preview_dir, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(html)
        written.append(rel)
    shutil.copytree(os.path.join(HERE, "assets", "s"), os.path.join(preview_dir, "s"))
    shutil.copytree(os.path.join(HERE, "assets", "css"),
                    os.path.join(preview_dir, "assets", "css"))
    return sorted(written)
```

Note: the editor snippet lands **after** link rewriting, so its `/static/...` URLs stay pointing at the dashboard. Update `__main__`:

```python
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=os.path.join(HERE, "dist"))
    ap.add_argument("--preview", action="store_true",
                    help="build the stamped editable preview into .preview/")
    args = ap.parse_args()
    if args.preview:
        paths = build_preview()
        print(f"preview: built {len(paths)} pages -> {PREVIEW_DIR}")
    else:
        paths = build_site(args.dist)
        print(f"built {len(paths)} files -> {args.dist}")
```

Append to `.gitignore` (repo root):

```
web/ahb123/.preview/
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_ahb123_preview_build.py tests/test_fragment_doc.py tests/test_ahb123_build.py -v`
(the last file exists if B-i-era build tests are present; skip it if not)
Expected: all pass. Then a real preview build:

```bash
venv/bin/python web/ahb123/build.py --preview && git status --porcelain | head
```
Expected: prints `preview: built 6 pages -> …/.preview`; `git status` shows NO `.preview` entries.

- [ ] **Step 5: Commit**

```bash
git add web/ahb123/build.py .gitignore tests/test_ahb123_preview_build.py
git commit -m "feat(ahb123): build_preview — stamped, link-rewritten, editor-injected preview build"
```

---

### Task 3: Blueprint part 1 — preview serving, draft status, git log

**Files:**
- Create: `dashboard/web_source_editor.py`
- Modify: `dashboard/app.py` (register blueprint right after the `_ui_bp` registration, ~line 16380)
- Test: `tests/test_web_source_editor.py`

**Interfaces:**
- Consumes: Task 2's `build_preview()` / `PREVIEW_DIR` (loaded by path).
- Produces: Blueprint `src_bp`; `GET /web/preview/ahb123/` + `GET /web/preview/ahb123/<path:rel>`; `GET /api/web/ahb123/draft` → `{dirty, files}`; `GET /api/web/ahb123/log` → `{commits:[{sha, subject}]}`; module helpers `_load_site_module(name)`, `git_draft(runner=...)`, `git_log(runner=...)` (Tasks 4/5 add routes to this same file).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_source_editor.py`:

```python
# tests/test_web_source_editor.py — ahb123 source editor blueprint (spec B4)
import os
import sys
import types

import pytest
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "dashboard"))
import web_source_editor as wse


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # isolated preview dir with a known file
    pv = tmp_path / "preview"
    (pv / "sub").mkdir(parents=True)
    (pv / "index.html").write_text("<html>home preview</html>")
    (pv / "sub" / "index.html").write_text("<html>sub</html>")
    monkeypatch.setattr(wse, "PREVIEW_DIR", str(pv))
    app = Flask(__name__)
    app.register_blueprint(wse.src_bp)
    return app.test_client()


def test_preview_serves_index_and_subpaths(client):
    r = client.get("/web/preview/ahb123/")
    assert r.status_code == 200 and b"home preview" in r.data
    r2 = client.get("/web/preview/ahb123/sub/")
    assert r2.status_code == 200 and b"sub" in r2.data


def test_preview_blocks_traversal(client):
    for bad in ["../app.py", "..%2fapp.py", "sub/../../etc/passwd"]:
        r = client.get("/web/preview/ahb123/" + bad)
        assert r.status_code in (404, 400), bad


def test_draft_status_uses_git_porcelain():
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0,
                                     stdout=" M web/ahb123/content/home.html\n",
                                     stderr="")
    d = wse.git_draft(runner=fake_run)
    assert d["dirty"] is True
    assert d["files"] == ["web/ahb123/content/home.html"]
    assert "--porcelain" in calls[0]


def test_draft_clean():
    fake = lambda argv, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr="")
    assert wse.git_draft(runner=fake) == {"dirty": False, "files": []}


def test_git_log_parses_oneline():
    fake = lambda argv, **kw: types.SimpleNamespace(
        returncode=0, stdout="abc1234 fix hero copy\ndef5678 new photo\n", stderr="")
    commits = wse.git_log(runner=fake)
    assert commits[0] == {"sha": "abc1234", "subject": "fix hero copy"}
    assert len(commits) == 2


def test_routes_registered(client):
    app = client.application
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/web/ahb123/draft" in rules
    assert "/api/web/ahb123/log" in rules
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_web_source_editor.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `dashboard/web_source_editor.py`**

```python
"""ahb123.com click-to-edit source editor (spec 2026-07-08 B4).

Serves the stamped preview build, exposes draft/log info, and (Tasks 4-5)
source-edit + publish endpoints. All file operations are locked to the
web/ahb123/{content,assets} subtree; fragment writes are atomic.
"""
import importlib.util
import os
import subprocess

from flask import Blueprint, jsonify, request, send_from_directory

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_DIR = os.path.join(REPO_ROOT, "web", "ahb123")   # data: content/, assets/ (tests monkeypatch this)
_CODE_DIR = SITE_DIR                                   # code: build.py, fragment_doc.py (never monkeypatched)
PREVIEW_DIR = os.path.join(SITE_DIR, ".preview")

src_bp = Blueprint("ahb_source_editor", __name__)

_modules = {}


def _load_site_module(name):
    """Load web/ahb123/<name>.py by path (the site dir is not a package).
    Always loads from _CODE_DIR: tests monkeypatch SITE_DIR to a fake content
    tree that has no Python modules."""
    if name not in _modules:
        spec = importlib.util.spec_from_file_location(
            "ahb123_" + name, os.path.join(_CODE_DIR, name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _modules[name] = mod
    return _modules[name]


def _ensure_preview():
    if not os.path.isfile(os.path.join(PREVIEW_DIR, "index.html")):
        _load_site_module("build").build_preview(PREVIEW_DIR)


@src_bp.get("/web/preview/ahb123/")
@src_bp.get("/web/preview/ahb123/<path:rel>")
def preview(rel=""):
    _ensure_preview()
    base = os.path.realpath(PREVIEW_DIR)
    full = os.path.realpath(os.path.join(base, rel))
    if full != base and not full.startswith(base + os.sep):
        return jsonify({"error": "bad path"}), 404
    if os.path.isdir(full):
        rel = os.path.join(rel, "index.html") if rel else "index.html"
    return send_from_directory(base, rel or "index.html")


def git_draft(runner=subprocess.run):
    res = runner(["git", "status", "--porcelain", "--", "web/ahb123"],
                 cwd=REPO_ROOT, capture_output=True, text=True, timeout=15)
    files = [line[3:].strip() for line in (res.stdout or "").splitlines()
             if line.strip()]
    return {"dirty": bool(files), "files": files}


def git_log(runner=subprocess.run, n=10):
    res = runner(["git", "log", "--oneline", "-%d" % n, "--", "web/ahb123"],
                 cwd=REPO_ROOT, capture_output=True, text=True, timeout=15)
    commits = []
    for line in (res.stdout or "").splitlines():
        sha, _, subject = line.partition(" ")
        if sha:
            commits.append({"sha": sha, "subject": subject})
    return commits


@src_bp.get("/api/web/ahb123/draft")
def api_draft():
    try:
        return jsonify(git_draft())
    except Exception as e:
        return jsonify({"dirty": None, "files": [], "error": str(e)}), 500


@src_bp.get("/api/web/ahb123/log")
def api_log():
    try:
        return jsonify({"commits": git_log()})
    except Exception as e:
        return jsonify({"commits": [], "error": str(e)}), 500
```

Register in `dashboard/app.py` immediately after the `app.register_blueprint(_ui_bp)` line:

```python
# ── ahb123.com source editor (preview + edit + publish — spec B4) ────────────
try:
    from dashboard.web_source_editor import src_bp as _src_bp
except ImportError:
    from web_source_editor import src_bp as _src_bp
app.register_blueprint(_src_bp)
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_web_source_editor.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/web_source_editor.py dashboard/app.py tests/test_web_source_editor.py
git commit -m "feat(web-editor): preview serving + draft/log endpoints (blueprint part 1)"
```

---

### Task 4: Blueprint part 2 — source edit, upload, meta endpoints

**Files:**
- Modify: `dashboard/web_source_editor.py`
- Test: `tests/test_web_source_editor.py` (append)

**Interfaces:**
- Consumes: Task 1's `FragmentDoc`/`parse_path` (via `_load_site_module("fragment_doc")`), Task 2's `build_preview`, Task 3's helpers.
- Produces: `POST /api/web/ahb123/edit` `{edit_id, kind, value}` (kinds `text|style|link|image|attr|reorder`); `POST /api/web/ahb123/upload` (multipart `file`) → `{ok, src, preview}`; `GET/POST /api/web/ahb123/meta`. Task 6's edit.js source mode targets these exact routes/shapes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_source_editor.py`:

```python
@pytest.fixture()
def site(tmp_path, monkeypatch):
    """Isolated fake site tree: content/, assets/s/, .preview rebuilt on demand."""
    sd = tmp_path / "site"
    (sd / "content").mkdir(parents=True)
    (sd / "assets" / "s").mkdir(parents=True)
    (sd / "content" / "home.html").write_text(
        '<div class="hero">\n  <h1>Old Title</h1>\n  <p>Text</p>\n'
        '  <img src="/s/a.jpg" alt="x">\n</div>\n')
    (sd / "content" / "meta.json").write_text(
        '{"home": {"title": "T", "description": "D", "og_image": "/s/og.jpg"}}')
    monkeypatch.setattr(wse, "SITE_DIR", str(sd))
    monkeypatch.setattr(wse, "PREVIEW_DIR", str(tmp_path / "pv"))
    rebuilds = []
    monkeypatch.setattr(wse, "_rebuild_preview", lambda: rebuilds.append(1))
    app = Flask(__name__)
    app.register_blueprint(wse.src_bp)
    c = app.test_client()
    c._site, c._rebuilds = sd, rebuilds
    return c


def frag(site):
    return (site._site / "content" / "home.html").read_text()


def test_edit_text(site):
    r = site.post("/api/web/ahb123/edit", json={
        "edit_id": "home:0.0", "kind": "text", "value": "New Title"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert "<h1>New Title</h1>" in frag(site)
    assert site._rebuilds  # preview rebuilt after the write


def test_edit_image_and_link_and_reorder(site):
    site.post("/api/web/ahb123/edit", json={
        "edit_id": "home:0.2", "kind": "image", "value": "/s/b.jpg"})
    assert 'src="/s/b.jpg"' in frag(site)
    site.post("/api/web/ahb123/edit", json={
        "edit_id": "home:0", "kind": "reorder", "value": [2, 0, 1]})
    f = frag(site)
    assert f.index("<img") < f.index("<h1>") < f.index("<p>")


def test_edit_style_merges_inline(site):
    site.post("/api/web/ahb123/edit", json={
        "edit_id": "home:0.1", "kind": "style",
        "value": {"color": "#fff", "fontSize": "18px"}})
    f = frag(site)
    assert "color:#fff" in f and "font-size:18px" in f
    # second edit merges + removes via empty value
    site.post("/api/web/ahb123/edit", json={
        "edit_id": "home:0.1", "kind": "style",
        "value": {"color": "", "fontWeight": "700"}})
    f2 = frag(site)
    assert "font-weight:700" in f2 and "font-size:18px" in f2
    assert "color:#fff" not in f2


def test_edit_rejects_bad_input(site):
    assert site.post("/api/web/ahb123/edit", json={
        "edit_id": "nosuch:0", "kind": "text", "value": "x"}).status_code == 422
    assert site.post("/api/web/ahb123/edit", json={
        "edit_id": "home:9.9", "kind": "text", "value": "x"}).status_code == 404
    assert site.post("/api/web/ahb123/edit", json={
        "edit_id": "home:0", "kind": "explode", "value": "x"}).status_code == 422
    before = frag(site)
    # text on a parent element refused, file untouched
    assert site.post("/api/web/ahb123/edit", json={
        "edit_id": "home:0", "kind": "text", "value": "x"}).status_code == 422
    assert frag(site) == before


def test_upload_guards_and_writes(site):
    import io
    bad = site.post("/api/web/ahb123/upload", data={
        "file": (io.BytesIO(b"x"), "evil.php")})
    assert bad.status_code == 422
    ok = site.post("/api/web/ahb123/upload", data={
        "file": (io.BytesIO(b"\x89PNG fake"), "My Photo.png")})
    j = ok.get_json()
    assert j["ok"] and j["src"].startswith("/s/") and j["src"].endswith(".png")
    name = j["src"][len("/s/"):]
    assert (site._site / "assets" / "s" / name).exists()
    assert "/" not in name.replace("", "")  # no traversal in generated name


def test_meta_get_and_post(site):
    g = site.get("/api/web/ahb123/meta?slug=home").get_json()
    assert g["title"] == "T"
    p = site.post("/api/web/ahb123/meta", json={
        "slug": "home", "title": "New T", "description": "New D"})
    assert p.status_code == 200
    import json as _json
    m = _json.loads((site._site / "content" / "meta.json").read_text())
    assert m["home"]["title"] == "New T" and m["home"]["og_image"] == "/s/og.jpg"
    assert site.get("/api/web/ahb123/meta?slug=nope").status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_web_source_editor.py -v`
Expected: new tests FAIL (routes missing; `_rebuild_preview` missing).

- [ ] **Step 3: Implement in `dashboard/web_source_editor.py`**

Add imports at top: `import json`, `import re`, `import uuid`. Add after the existing helpers:

```python
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
EDIT_KINDS = {"text", "style", "link", "image", "attr", "reorder"}
SLUGS = ["home", "services", "portfolio", "about", "contact", "plan"]
_CAMEL_RE = re.compile(r"[A-Z]")


def _rebuild_preview():
    _load_site_module("build").build_preview(PREVIEW_DIR)


def _fragment_file(slug):
    if slug not in SLUGS:
        return None
    return os.path.join(SITE_DIR, "content", "%s.html" % slug)


def _atomic_write(path, data):
    tmp = path + ".tmp-edit"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


def _kebab(prop):
    return _CAMEL_RE.sub(lambda m: "-" + m.group(0).lower(), prop)


def _merge_style(existing, props):
    """Merge camelCase props into an inline style string. Empty value deletes."""
    cur = {}
    for part in (existing or "").split(";"):
        if ":" in part:
            k, _, v = part.partition(":")
            cur[k.strip()] = v.strip()
    for k, v in (props or {}).items():
        kk = _kebab(str(k))
        if v in ("", None):
            cur.pop(kk, None)
        else:
            cur[kk] = str(v)
    return ";".join("%s:%s" % kv for kv in cur.items()) or None


@src_bp.post("/api/web/ahb123/edit")
def api_edit():
    fd = _load_site_module("fragment_doc")
    b = request.get_json(force=True, silent=True) or {}
    edit_id = b.get("edit_id")
    kind = b.get("kind")
    value = b.get("value")
    if not isinstance(edit_id, str) or ":" not in edit_id:
        return jsonify({"error": "edit_id must be '<slug>:<path>'"}), 422
    if kind not in EDIT_KINDS:
        return jsonify({"error": "kind must be one of %s" % sorted(EDIT_KINDS)}), 422
    slug, _, path_s = edit_id.partition(":")
    frag_path = _fragment_file(slug)
    if not frag_path or not os.path.isfile(frag_path):
        return jsonify({"error": "unknown slug %r" % slug}), 422
    try:
        path = fd.parse_path(path_s)
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    with open(frag_path, encoding="utf-8") as f:
        src = f.read()
    try:
        doc = fd.FragmentDoc(src)
        if kind == "text":
            out = doc.set_text(path, str(value))
        elif kind == "link":
            out = doc.set_attr(path, "href", str(value))
        elif kind == "image":
            out = doc.set_attr(path, "src", str(value))
        elif kind == "attr":
            v = value or {}
            if not isinstance(v, dict) or not v.get("name"):
                return jsonify({"error": "attr value needs {name, value}"}), 422
            out = doc.set_attr(path, v["name"], v.get("value"))
        elif kind == "style":
            if not isinstance(value, dict):
                return jsonify({"error": "style value must be an object"}), 422
            node = doc.node(path)
            existing = dict(node.attrs).get("style")
            out = doc.set_attr(path, "style", _merge_style(existing, value))
        else:  # reorder
            if (not isinstance(value, list)
                    or not all(isinstance(i, int) for i in value)):
                return jsonify({"error": "reorder value must be a list of ints"}), 422
            out = doc.reorder(path, value)
    except KeyError:
        return jsonify({"error": "no node at %s" % edit_id}), 404
    except (fd.FragmentParseError, fd.FragmentEditError, ValueError) as e:
        return jsonify({"error": str(e)}), 422
    _atomic_write(frag_path, out)
    try:
        _rebuild_preview()
    except Exception as e:
        return jsonify({"ok": True, "warning": "preview rebuild failed: %s" % e})
    return jsonify({"ok": True})


@src_bp.post("/api/web/ahb123/upload")
def api_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "no file"}), 422
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"error": "extension %s not allowed" % ext}), 422
    if request.content_length and request.content_length > MAX_UPLOAD_BYTES:
        return jsonify({"error": "file too large"}), 422
    blob = f.read(MAX_UPLOAD_BYTES + 1)
    if len(blob) > MAX_UPLOAD_BYTES:
        return jsonify({"error": "file too large"}), 422
    stem = re.sub(r"[^a-z0-9]+", "-",
                  os.path.splitext(f.filename)[0].lower()).strip("-")[:40] or "img"
    name = "%s-%s%s" % (stem, uuid.uuid4().hex[:8], ext)
    dest_dir = os.path.join(SITE_DIR, "assets", "s")
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, name), "wb") as out:
        out.write(blob)
    try:
        _rebuild_preview()
    except Exception:
        pass
    return jsonify({"ok": True, "src": "/s/" + name,
                    "preview": "/web/preview/ahb123/s/" + name})


@src_bp.get("/api/web/ahb123/meta")
def api_meta_get():
    slug = request.args.get("slug", "")
    meta_path = os.path.join(SITE_DIR, "content", "meta.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    if slug not in meta:
        return jsonify({"error": "unknown slug"}), 422
    return jsonify(dict(meta[slug], slug=slug))


@src_bp.post("/api/web/ahb123/meta")
def api_meta_post():
    b = request.get_json(force=True, silent=True) or {}
    slug = b.get("slug")
    meta_path = os.path.join(SITE_DIR, "content", "meta.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    if slug not in meta:
        return jsonify({"error": "unknown slug"}), 422
    for field in ("title", "description", "og_image"):
        if field in b:
            if not isinstance(b[field], str):
                return jsonify({"error": "%s must be a string" % field}), 422
            meta[slug][field] = b[field]
    _atomic_write(meta_path, json.dumps(meta, indent=2) + "\n")
    try:
        _rebuild_preview()
    except Exception:
        pass
    return jsonify({"ok": True})
```

**Note:** `_fragment_file` recomputes from the module-level `SITE_DIR` at call time so tests can monkeypatch `wse.SITE_DIR` — do not capture `SITE_DIR` into per-route constants. Same for `PREVIEW_DIR` (Task 3 already reads it inside the route).

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_web_source_editor.py tests/test_fragment_doc.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/web_source_editor.py tests/test_web_source_editor.py
git commit -m "feat(web-editor): source edit/upload/meta endpoints with atomic fragment writes"
```

---

### Task 5: Blueprint part 3 — Publish background job + status

**Files:**
- Modify: `dashboard/web_source_editor.py`
- Test: `tests/test_web_source_editor.py` (append)

**Interfaces:**
- Consumes: `web_site_routes.deploy_site(runner)` (existing: venv python runs `build.py` then `deploy.py`, returns the `*.pages.dev` URL or raises `RuntimeError`).
- Produces: `POST /api/web/ahb123/publish` → 202 `{ok, state:'building'}` or 409 while in flight; `GET /api/web/ahb123/publish/status` → `{state: idle|building|done|error, url, error, started_at, finished_at}`; helper `start_publish(runner=None, background=True)`. Task 7's UI polls these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_source_editor.py`:

```python
def _reset_publish():
    wse._publish.update(state="idle", url=None, error=None,
                        started_at=None, finished_at=None)


def test_publish_success_flow(monkeypatch):
    _reset_publish()
    monkeypatch.setattr(wse, "_deploy_site",
                        lambda: "https://abc123.ahb123.pages.dev")
    assert wse.start_publish(background=False) is True
    assert wse._publish["state"] == "done"
    assert wse._publish["url"].endswith("pages.dev")
    assert wse._publish["started_at"] and wse._publish["finished_at"]


def test_publish_error_captured(monkeypatch):
    _reset_publish()

    def boom():
        raise RuntimeError("wrangler exploded")
    monkeypatch.setattr(wse, "_deploy_site", boom)
    wse.start_publish(background=False)
    assert wse._publish["state"] == "error"
    assert "wrangler exploded" in wse._publish["error"]


def test_publish_refuses_concurrent(monkeypatch):
    _reset_publish()
    wse._publish["state"] = "building"
    assert wse.start_publish(background=False) is False
    _reset_publish()


def test_publish_routes(client, monkeypatch):
    _reset_publish()
    monkeypatch.setattr(wse, "_deploy_site", lambda: "https://x.pages.dev")
    s = client.get("/api/web/ahb123/publish/status").get_json()
    assert s["state"] == "idle"
    r = client.post("/api/web/ahb123/publish")
    assert r.status_code == 202
    wse._publish_thread.join(timeout=10)
    s2 = client.get("/api/web/ahb123/publish/status").get_json()
    assert s2["state"] == "done" and s2["url"] == "https://x.pages.dev"
    # concurrent guard
    wse._publish["state"] = "building"
    assert client.post("/api/web/ahb123/publish").status_code == 409
    _reset_publish()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_web_source_editor.py -k publish -v`
Expected: FAIL — publish machinery missing.

- [ ] **Step 3: Implement in `dashboard/web_source_editor.py`**

Add `import threading`, `import time` to imports. Then:

```python
# ── Publish (build + wrangler deploy) as a background job ───────────────────
_publish = {"state": "idle", "url": None, "error": None,
            "started_at": None, "finished_at": None}
_publish_lock = threading.Lock()
_publish_thread = None


def _deploy_site():
    """Indirection point (monkeypatched in tests)."""
    try:
        from dashboard.web_site_routes import deploy_site
    except ImportError:
        from web_site_routes import deploy_site
    return deploy_site()


def _run_publish():
    global _publish
    try:
        url = _deploy_site()
        _publish.update(state="done", url=url, error=None,
                        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        _publish.update(state="error", error=str(e),
                        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))


def start_publish(background=True):
    """Returns True if a publish was started, False if one is in flight."""
    global _publish_thread
    with _publish_lock:
        if _publish["state"] == "building":
            return False
        _publish.update(state="building", url=None, error=None,
                        started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                        finished_at=None)
    if background:
        _publish_thread = threading.Thread(target=_run_publish, daemon=True)
        _publish_thread.start()
    else:
        _run_publish()
    return True


@src_bp.post("/api/web/ahb123/publish")
def api_publish():
    if not start_publish():
        return jsonify({"ok": False, "error": "publish already in flight"}), 409
    return jsonify({"ok": True, "state": "building"}), 202


@src_bp.get("/api/web/ahb123/publish/status")
def api_publish_status():
    return jsonify(dict(_publish))
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_web_source_editor.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/web_source_editor.py tests/test_web_source_editor.py
git commit -m "feat(web-editor): publish background job with status polling + concurrency guard"
```

---

### Task 6: edit.js source mode

**Files:**
- Modify: `dashboard/static/edit.js`
- Modify: `dashboard/templates/_nav.html` (bump `?v=` — from 9 to 10 if B-ii ran; otherwise from the current number)
- Test: `tests/test_editor_wiring.py` (append + version bump)

**Interfaces:**
- Consumes: `window.BAZA_SOURCE_EDIT = {site, slug}` (injected by Task 2's preview build), Task 4's `/api/web/ahb123/edit` + `/api/web/ahb123/upload`, `data-edit-id` attributes.
- Produces: nothing downstream.

**Behavior contract:**
- `SOURCE` mode is on iff `window.BAZA_SOURCE_EDIT` exists.
- In SOURCE mode: overrides fetch, MutationObserver, and stale reporter are all skipped; Edit Mode starts ON; `sessionStorage` is never read or written (it is shared with the dashboard origin and must not leak between the two editors).
- Click selection climbs to the nearest ancestor with `data-edit-id`; clicks on unstamped chrome (base.html nav/footer) are ignored.
- Saves POST `{edit_id, kind, value}` to `/api/web/ahb123/edit` (kind mapping: `order`→`reorder`, others 1:1), then reload the page after a short toast (the server rebuilt the preview).
- Image upload posts to `/api/web/ahb123/upload` and uses the returned `src` (`/s/…` site path) as the value; the URL input placeholder explains that paths are site-relative. The Data Hub picker button is hidden in SOURCE mode (dashboard-only URLs would break on the public site).
- Hide and Reset element sections are not rendered in SOURCE mode (no override store to revert from; hiding = deleting source, out of scope v1).
- Reorder (if B-ii ran and the Reorder section exists): in SOURCE mode `persistOrder` sends `{edit_id: <parent id>, kind: 'reorder', value: [old indices in new sequence]}` where each index is the last dotted segment of the child's `data-edit-id`, and the parent id is the child id minus its last segment (`home:0.2` → `home:0`; `home:2` → `home:`). Children without `data-edit-id` block the save with a toast. If B-ii has not run, skip this bullet.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_editor_wiring.py`:

```python
def test_source_mode_present():
    js = read("dashboard", "static", "edit.js")
    for needle in ["BAZA_SOURCE_EDIT", "data-edit-id", "api/web/ahb123/edit",
                   "api/web/ahb123/upload", "saveSource"]:
        assert needle in js, f"missing {needle}"

def test_source_mode_never_touches_sessionstorage():
    js = read("dashboard", "static", "edit.js")
    # every sessionStorage use must be behind the !SOURCE guard
    import re
    for m in re.finditer(r"sessionStorage", js):
        ctx = js[max(0, m.start()-200):m.start()]
        assert "SOURCE" in ctx, "sessionStorage use not guarded for source mode"
```

Update `test_asset_version_bumped` to the new number.

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py -v`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

In `edit.js`:

(a) top of the IIFE, after `var PAGE = ...`:

```js
var SOURCE = window.BAZA_SOURCE_EDIT || null; // ahb123 preview iframe mode
```

(b) `setEditMode` — guard the storage write:

```js
  if (!SOURCE) { try { sessionStorage.setItem('bazaEdit', on ? '1' : '0'); } catch (e) {} }
```

(c) `initToggle` — guard the storage read:

```js
  var qs = new URLSearchParams(location.search);
  if (SOURCE) { setEditMode(true); }
  else if (qs.get('edit') === '1' || sessionStorage.getItem('bazaEdit') === '1') setEditMode(true);
```

(d) click selection — climb to the stamped ancestor:

```js
document.addEventListener('click', function (e) {
  if (!editMode || inChrome(e.target)) return;
  var t = e.target;
  if (SOURCE) {
    while (t && t !== document.body &&
           !(t.getAttribute && t.getAttribute('data-edit-id'))) t = t.parentElement;
    if (!t || t === document.body) return;
  }
  e.preventDefault();
  e.stopPropagation();
  select(t);
}, true);
```

(e) source save + routing. Add near `saveOverrideFor` (or `saveOverride` if B-ii hasn't run — then adapt the same way for the boolean-resolving variant):

```js
function saveSource(el, kind, value) {
  var id = el && el.getAttribute && el.getAttribute('data-edit-id');
  var map = { text: 'text', image: 'image', link: 'link', style: 'style',
              attr: 'attr', order: 'reorder' };
  if (!id || !map[kind]) return Promise.resolve({ ok: false, id: null, prev: null });
  return fetch('/api/web/ahb123/edit', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ edit_id: id, kind: map[kind], value: value })
  }).then(function (r) {
    if (!r.ok) throw new Error('save failed');
    setTimeout(function () { location.reload(); }, 700); // preview was rebuilt
    return { ok: true, id: null, prev: null };
  }).catch(function () { return { ok: false, id: null, prev: null }; });
}
```

At the top of `saveOverrideFor` add:

```js
  if (SOURCE) return saveSource(el, kind, value);
```

(f) `boot()` — skip the overrides machinery in SOURCE mode:

```js
function boot() {
  initToggle();
  if (SOURCE) return; // preview iframe: no overrides, no observer, no stale report
  refresh().then(function () {
    ...
```

(g) `buildInspector` gates — wrap the Hide/Reset section and (from B-ii) the Data Hub picker button and undo calls:

```js
  if (!SOURCE) {
    // visibility + reset section (existing code moves inside this guard)
  }
```

and for the image section's upload handler, switch the endpoint by mode:

```js
      var upUrl = SOURCE ? '/api/web/ahb123/upload' : '/api/ui/upload';
      fetch(upUrl, { method: 'POST', body: fd })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          var picked = j.url || j.src;
          if (picked) { url.value = picked; toast('uploaded — hit Save image'); }
          else toast('✗ ' + (j.error || 'upload failed'));
        })
```

(h) if the B-ii Reorder section exists, make `persistOrder` mode-aware:

```js
    function persistOrder() {
      var kids = Array.prototype.filter.call(par.children, function (c) { return !inChrome(c); });
      if (SOURCE) {
        var ids = kids.map(function (c) { return c.getAttribute('data-edit-id'); });
        if (ids.some(function (x) { return !x; })) { toast('✗ unstamped children — cannot reorder'); return; }
        var indices = ids.map(function (x) { return parseInt(x.split(':')[1].split('.').pop(), 10); });
        var first = ids[0];
        var parentId = /\.\d+$/.test(first) ? first.replace(/\.\d+$/, '')
                                            : first.replace(/:\d+$/, ':');
        fetch('/api/web/ahb123/edit', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ edit_id: parentId, kind: 'reorder', value: indices })
        }).then(function (r) {
          toast(r.ok ? '✓ order saved' : '✗ save failed');
          if (r.ok) setTimeout(function () { location.reload(); }, 700);
        }).catch(function () { toast('✗ save failed'); });
        return;
      }
      var keys = kids.map(childKeyFor);
      saveOverrideFor(par, 'order', keys).then(function (res) {
        toast(res.ok ? '✓ order saved' : '✗ save failed');
      });
    }
```

(i) skip `showUndo` calls in SOURCE mode (`if (res.ok && !SOURCE) showUndo(res);`) — source undo is the git history.

- [ ] **Step 4: Bump `?v=`** in `_nav.html` (both lines) and in `test_asset_version_bumped`.

- [ ] **Step 5: Run tests**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add dashboard/static/edit.js dashboard/templates/_nav.html tests/test_editor_wiring.py
git commit -m "feat(editor): source mode — data-edit-id selection + ahb123 save routing"
```

---

### Task 7: `/web` — ahb123 editor section (iframe, Draft, Publish, history, meta)

**Files:**
- Modify: `dashboard/templates/web.html`
- Test: `tests/test_web_page.py` (append)

**Interfaces:**
- Consumes: Task 3's preview + draft + log routes, Task 4's meta routes, Task 5's publish routes.
- Produces: the user-facing editor. Nothing downstream.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_page.py`:

```python
def test_web_page_has_ahb123_editor_section():
    html = read("dashboard", "templates", "web.html")
    for needle in ["/web/preview/ahb123/", "api/web/ahb123/publish",
                   "api/web/ahb123/draft", "api/web/ahb123/meta",
                   "api/web/ahb123/log", "ahb-frame"]:
        assert needle in html, f"missing {needle}"

def test_web_page_slug_pills():
    html = read("dashboard", "templates", "web.html")
    for slug in ["home", "services", "portfolio", "about", "contact", "plan"]:
        assert f"'{slug}'" in html or f'"{slug}"' in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_web_page.py -v`
Expected: new tests FAIL.

- [ ] **Step 3: Implement the section in `web.html`**

Insert a new card between the "Sites" card and the "Baza Dash pages" card:

```html
  <!-- ahb123.com source editor -->
  <div class="card">
    <div class="card-head">
      <div class="card-title">🏠 ahb123.com — click-to-edit (draft → publish)</div>
      <div style="display:flex;gap:10px;align-items:center">
        <span id="ahb-draft" class="pill off">checking…</span>
        <button class="btn sm" id="ahb-publish" onclick="publishSite()">🚀 Publish</button>
      </div>
    </div>
    <div class="card-body">
      <div id="ahb-slugs" style="margin-bottom:10px"></div>
      <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:10px;align-items:flex-end">
        <label style="font-size:11px;color:#555;flex:1;min-width:220px">Page title
          <input id="ahb-meta-title" style="width:100%;background:#111;border:1px solid #1a1a2e;color:#eee;border-radius:6px;padding:7px 9px;font-size:12px"></label>
        <label style="font-size:11px;color:#555;flex:2;min-width:280px">Meta description
          <input id="ahb-meta-desc" style="width:100%;background:#111;border:1px solid #1a1a2e;color:#eee;border-radius:6px;padding:7px 9px;font-size:12px"></label>
        <button class="btn ghost sm" onclick="saveMeta()">💾 Save meta</button>
      </div>
      <iframe id="ahb-frame" style="width:100%;height:68vh;border:1px solid #1a1a2e;border-radius:10px;background:#fff"></iframe>
      <div id="ahb-pubstatus" style="font-size:12px;color:#888;margin-top:8px"></div>
      <div style="margin-top:12px">
        <div style="font-size:12px;font-weight:700;color:#aaa;margin-bottom:6px">Recent site commits (auto-git hourly = your rollback history)</div>
        <div id="ahb-log" style="font-size:12px;color:#666;font-family:monospace">Loading…</div>
      </div>
    </div>
  </div>
```

And the JS (add before the final `loadSiteCard(); loadPageCards();` line, and extend that line):

```js
const AHB_SLUGS = { home:'', services:'services/', portfolio:'portfolio/',
                    about:'about/', contact:'contact/', plan:'plan/' };
let ahbSlug = 'home';

function pickSlug(slug){
  ahbSlug = slug;
  document.querySelectorAll('#ahb-slugs .btn').forEach(b =>
    b.classList.toggle('ghost', b.dataset.slug !== slug));
  document.getElementById('ahb-frame').src = '/web/preview/ahb123/' + AHB_SLUGS[slug];
  loadMeta();
}

function renderSlugs(){
  document.getElementById('ahb-slugs').innerHTML = Object.keys(AHB_SLUGS).map(s =>
    `<button class="btn sm ${s===ahbSlug?'':'ghost'}" data-slug="${s}" onclick="pickSlug('${s}')">${s}</button>`
  ).join(' ');
}

async function loadMeta(){
  try{
    const m = await (await fetch('/api/web/ahb123/meta?slug='+ahbSlug)).json();
    document.getElementById('ahb-meta-title').value = m.title || '';
    document.getElementById('ahb-meta-desc').value = m.description || '';
  }catch(e){}
}

async function saveMeta(){
  const r = await fetch('/api/web/ahb123/meta', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({slug: ahbSlug,
      title: document.getElementById('ahb-meta-title').value,
      description: document.getElementById('ahb-meta-desc').value})});
  if(!r.ok){ alert('Meta save failed ('+r.status+')'); return; }
  loadDraft();
}

async function loadDraft(){
  const pill = document.getElementById('ahb-draft');
  try{
    const d = await (await fetch('/api/web/ahb123/draft')).json();
    if(d.dirty){ pill.className='pill warn'; pill.textContent='● draft — unpublished edits'; }
    else { pill.className='pill ok'; pill.textContent='clean'; }
    pill.title = (d.files||[]).join('\n');
  }catch(e){ pill.className='pill off'; pill.textContent='?'; }
}

async function loadLog(){
  try{
    const j = await (await fetch('/api/web/ahb123/log')).json();
    document.getElementById('ahb-log').innerHTML = (j.commits||[]).map(c =>
      `<div>${escHtml(c.sha)} ${escHtml(c.subject)}</div>`).join('') || 'No site commits yet.';
  }catch(e){ document.getElementById('ahb-log').textContent = 'log unavailable'; }
}

let pubTimer = null;
async function pollPublish(){
  try{
    const s = await (await fetch('/api/web/ahb123/publish/status')).json();
    const box = document.getElementById('ahb-pubstatus');
    const btn = document.getElementById('ahb-publish');
    if(s.state === 'building'){
      box.textContent = '⏳ building + deploying… (started '+(s.started_at||'')+')';
      btn.disabled = true;
      pubTimer = setTimeout(pollPublish, 2000);
    } else {
      btn.disabled = false;
      if(s.state === 'done') box.innerHTML = '✅ published → <a style="color:#00d084" href="'+escHtml(s.url||'')+'" target="_blank">'+escHtml(s.url||'')+'</a> at '+escHtml(s.finished_at||'');
      else if(s.state === 'error') box.innerHTML = '<span style="color:#ff6666">❌ publish failed: '+escHtml(s.error||'')+'</span>';
      loadDraft(); loadLog();
    }
  }catch(e){}
}

async function publishSite(){
  if(!confirm('Publish the current draft to ahb123.com (Cloudflare Pages)?')) return;
  const r = await fetch('/api/web/ahb123/publish', {method:'POST'});
  if(r.status === 409){ alert('A publish is already running.'); return; }
  if(!r.ok){ alert('Publish failed to start ('+r.status+')'); return; }
  pollPublish();
}
```

Change the bottom bootstrap line to:

```js
loadSiteCard(); loadPageCards(); renderSlugs(); pickSlug('home'); loadDraft(); loadLog(); pollPublish();
```

Also update the ahb123 **site card** placeholder line — replace
`<span style="color:#555">Visual source editor lands with phase B-iii.</span>` with
`<span style="color:#555">Edit below ↓ — click-to-edit preview + Publish.</span>`.

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_web_page.py tests/test_web_source_editor.py tests/test_editor_wiring.py -v`
Expected: all pass.

- [ ] **Step 5: Restart + end-to-end smoke**

```bash
sudo systemctl restart baza-dashboard
sleep 2
curl -s localhost:8888/web/preview/ahb123/ | grep -c 'data-edit-id'   # > 0
curl -s localhost:8888/api/web/ahb123/draft
curl -s localhost:8888/api/web/ahb123/meta?slug=home
curl -s -X POST localhost:8888/api/web/ahb123/edit -H 'Content-Type: application/json' \
  -d '{"edit_id":"home:0.0","kind":"text","value":"Philadelphia'"'"'s Trusted Home Builder"}'
git -C /home/switchhacker/baza-empire/agent-framework-v3 diff --stat -- web/ahb123/content
```
Expected: preview stamped; draft/meta JSON valid; the edit round-trips 200 and the diff shows `home.html` touched (the value re-writes the same title text, so `git checkout -- web/ahb123/content/home.html` afterwards if a diff besides entity-encoding appears — note whatever you observe in the report). **Do NOT hit the publish endpoint in smoke tests** — it deploys to production Cloudflare Pages.

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/web.html tests/test_web_page.py
git commit -m "feat(web): ahb123 click-to-edit section — iframe editor, draft pill, publish flow, history"
```
