# AHB123 Invoice Terms & Conditions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the invoice Terms & Conditions text customizable — one editable company default plus a per-project override — rendered live on invoice PDFs.

**Architecture:** A `DEFAULT_INVOICE_TERMS` constant seeds a new single-row `ahb_invoice_settings` table (company default) and is the final fallback. A new `ahb_projects.terms_conditions` column holds the per-project override. The invoice PDF resolves project → company default → constant and renders it in place of the hardcoded clause block. Settings get/put endpoints + a project-modal textarea + a company-default editor in the Estimator Settings modal.

**Tech Stack:** Flask (`app.py`), SQLite (`baza_projects.db`), WeasyPrint, vanilla JS (`ahb123.html`).

**Spec:** `docs/superpowers/specs/2026-06-16-ahb123-invoice-terms-conditions-design.md`

**Repo note:** No manual git commits (auto-git timer owns the repo). Checkpoint = tests green. Restart `baza-dashboard` after `ahb123.html` edits. **v1 is free-text (no `{{token}}` substitution)** per the approved spec.

**Test location:** `dashboard/tests/test_invoice_terms.py` (new), Flask test-client pattern.

---

### Task 1: Constant, settings table, and project column

**Files:**
- Modify: `dashboard/app.py` (migrations block ~410–460 for the new column; add constant + `_ensure_invoice_settings` near `_ensure_estimator_settings` ~13950)
- Test: `dashboard/tests/test_invoice_terms.py`

- [ ] **Step 1: Write the failing test**

```python
import app as appmod, sqlite3, os

def test_invoice_settings_seeded():
    appmod._ensure_invoice_settings()
    con = sqlite3.connect(os.path.join(appmod.DASHBOARD_DIR, "baza_projects.db"))
    row = con.execute("SELECT terms_default FROM ahb_invoice_settings WHERE id=1").fetchone()
    con.close()
    assert row and row[0] and "ALL HOME BUILDING" in row[0].upper()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_invoice_terms.py::test_invoice_settings_seeded -v`
Expected: FAIL (`_ensure_invoice_settings` missing)

- [ ] **Step 3: Implement**

Add the constant (extract the current six clauses verbatim as plain text, literal numbering, the previously-interpolated values made literal — `approx TBD days`, `$50.00`):

```python
DEFAULT_INVOICE_TERMS = """1. A Deposit is due before commencement of the project.
2. Total due upon completion.
3. Project will take approx TBD days to complete.
4. Project description is final unless a change order is requested.
5. Make checks payable to ALL HOME BUILDING CO.
6. Late Payment: Payment is due by the date specified on this invoice. If payment is not received by the due date, this invoice shall be deemed overdue. Interest shall accrue at the rate of fifty dollars ($50.00) per week on the unpaid balance. An overdue interest sheet will be attached reflecting all accrued charges."""
```

Add the migration line to the existing ALTER list (near line 453):

```python
"ALTER TABLE ahb_projects ADD COLUMN terms_conditions TEXT",
```

Add and call the initializer (mirror `_ensure_estimator_settings`):

```python
def _ensure_invoice_settings():
    try:
        conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'), timeout=8.0)
        conn.execute("PRAGMA busy_timeout = 8000")
        conn.execute("""CREATE TABLE IF NOT EXISTS ahb_invoice_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            terms_default TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("INSERT OR IGNORE INTO ahb_invoice_settings (id, terms_default) VALUES (1, ?)",
                     (DEFAULT_INVOICE_TERMS,))
        conn.commit(); conn.close()
    except sqlite3.OperationalError as e:
        print(f"[startup] _ensure_invoice_settings deferred — DB busy: {e}", flush=True)
_ensure_invoice_settings()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_invoice_terms.py::test_invoice_settings_seeded -v`
Expected: PASS

- [ ] **Step 5: Checkpoint** — tests green.

---

### Task 2: Effective-terms resolver + swap the hardcoded block

**Files:**
- Modify: `dashboard/app.py` (add `_resolve_invoice_terms`; invoice PDF T&C block ~10153–10163)
- Test: `dashboard/tests/test_invoice_terms.py`

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_invoice_terms_precedence():
    f = appmod._resolve_invoice_terms
    assert f({"terms_conditions": "PROJECT TERMS"}, "COMPANY") == "PROJECT TERMS"
    assert f({"terms_conditions": "   "}, "COMPANY") == "COMPANY"
    assert f({}, "") == appmod.DEFAULT_INVOICE_TERMS
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_invoice_terms.py::test_resolve_invoice_terms_precedence -v`
Expected: FAIL (`_resolve_invoice_terms` missing)

- [ ] **Step 3: Implement the resolver + renderer + swap**

```python
import html as _html

def _resolve_invoice_terms(project, company_default):
    pj = (project or {}).get("terms_conditions") or ""
    if pj.strip():
        return pj
    if (company_default or "").strip():
        return company_default
    return DEFAULT_INVOICE_TERMS

def _company_terms_default():
    try:
        conn = sqlite3.connect(os.path.join(DASHBOARD_DIR, 'baza_projects.db'), timeout=8.0)
        row = conn.execute("SELECT terms_default FROM ahb_invoice_settings WHERE id=1").fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""

def _render_terms_html(text):
    safe = _html.escape(text or "")
    lines = [ln for ln in safe.splitlines() if ln.strip()]
    return "".join(f'<p style="margin:0 0 8px;">{ln}</p>' for ln in lines)
```

In the invoice PDF route, before building `html`, compute:

```python
terms_text = _resolve_invoice_terms(project, _company_terms_default())
terms_html = _render_terms_html(terms_text)
```

Replace the hardcoded six `<p>` clauses (between `<div style="font-size:13px;color:#444;line-height:1.6;">` and its closing `</div>`, ~10155–10162) with `{terms_html}`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_invoice_terms.py -v`
Expected: PASS

- [ ] **Step 5: Manual verify** — `sudo systemctl restart baza-dashboard`; generate an invoice PDF → Terms & Conditions section shows the six default clauses (unchanged look).

- [ ] **Step 6: Checkpoint** — tests green.

---

### Task 3: Settings endpoints + project create/update persistence

**Files:**
- Modify: `dashboard/app.py` (add `GET`/`PUT /api/ahb/invoice-settings`; project create ~5611 and update ~5695 to persist `terms_conditions`)
- Test: `dashboard/tests/test_invoice_terms.py`

- [ ] **Step 1: Write the failing test**

```python
def test_invoice_settings_get_put(client):
    client.put("/api/ahb/invoice-settings", json={"terms_default": "NEW CO TERMS"})
    got = client.get("/api/ahb/invoice-settings").get_json()
    assert got["terms_default"] == "NEW CO TERMS"

def test_project_update_persists_terms(client, a_project_id):
    client.put(f"/api/ahb/projects/{a_project_id}", json={"terms_conditions": "PROJ TERMS"})
    proj = client.get(f"/api/ahb/projects").get_json()
    row = [p for p in (proj.get("projects") or proj) if p["id"] == a_project_id][0]
    assert row["terms_conditions"] == "PROJ TERMS"
```

(Provide `a_project_id` fixture creating a project via `POST /api/ahb/projects`.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_invoice_terms.py -k "settings_get_put or persists_terms" -v`
Expected: FAIL (endpoint missing; column not in update list)

- [ ] **Step 3: Implement**

Add endpoints:

```python
@app.route('/api/ahb/invoice-settings', methods=['GET'])
def api_ahb_invoice_settings_get():
    _ensure_invoice_settings()
    return jsonify({"terms_default": _company_terms_default() or DEFAULT_INVOICE_TERMS})

@app.route('/api/ahb/invoice-settings', methods=['PUT'])
def api_ahb_invoice_settings_put():
    data = request.json or {}
    val = normalize_escaped_newlines(data.get('terms_default') or '')
    conn = _ahb_db()
    conn.execute("INSERT INTO ahb_invoice_settings (id, terms_default, updated_at) VALUES (1, ?, datetime('now')) "
                 "ON CONFLICT(id) DO UPDATE SET terms_default=excluded.terms_default, updated_at=excluded.updated_at",
                 (val,))
    conn.commit(); conn.close()
    return jsonify({"success": True})
```

In `api_ahb_projects_update`, add `'terms_conditions'` to the tuple of updatable keys and to the `normalize_escaped_newlines` set (the `if k in ('description','scope','notes')` → add `'terms_conditions'`).

In `api_ahb_projects_create`, add `terms_conditions` to the INSERT column list + values as `normalize_escaped_newlines(data.get('terms_conditions'))`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_invoice_terms.py -v`
Expected: PASS

- [ ] **Step 5: Checkpoint** — tests green.

---

### Task 4: Project-modal Terms & Conditions textarea

**Files:**
- Modify: `dashboard/templates/ahb123.html` (project modal near `#project-notes` ~3970; load/save wiring ~5462/5529)

- [ ] **Step 1:** Add the field after the Notes textarea:

```html
<div style="margin-top:10px">
  <label style="display:flex;justify-content:space-between;align-items:center">
    <span>Terms &amp; Conditions</span>
    <button type="button" class="btn-secondary btn-sm" onclick="loadDefaultTerms()">Load company default</button>
  </label>
  <textarea id="project-terms-conditions" rows="6"
    placeholder="Leave blank to use the company default terms."></textarea>
</div>
```

- [ ] **Step 2:** Add `loadDefaultTerms`:

```javascript
async function loadDefaultTerms(){
  const d = await fetch('/api/ahb/invoice-settings').then(r=>r.json());
  document.getElementById('project-terms-conditions').value = d.terms_default || '';
}
```

- [ ] **Step 3:** Include the field in the project-modal load (where `set('project-...')` calls populate fields, ~5529): `set('project-terms-conditions', d.terms_conditions || '');` and in the save payload object (~5462): `terms_conditions: document.getElementById('project-terms-conditions').value,`.

- [ ] **Step 4: Manual verify** — `sudo systemctl restart baza-dashboard`; open a project → set custom terms → save → reopen shows them → generate that project's invoice PDF → custom terms render. Clear them → invoice shows company default.

- [ ] **Step 5: Checkpoint.**

---

### Task 5: Company-default editor in Estimator Settings modal

**Files:**
- Modify: `dashboard/templates/ahb123.html` (Estimator Settings modal ~1856; `loadEstimatorSettings`/save ~9260–9300)

- [ ] **Step 1:** Add an "Invoice Terms & Conditions" `<textarea id="set-invoice-terms" rows="8">` to the settings modal.

- [ ] **Step 2:** On settings load, populate it: `document.getElementById('set-invoice-terms').value = await fetch('/api/ahb/invoice-settings').then(r=>r.json()).then(d=>d.terms_default||'');`

- [ ] **Step 3:** On settings save, also `await fetch('/api/ahb/invoice-settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({terms_default: document.getElementById('set-invoice-terms').value})});`

- [ ] **Step 4: Manual verify** — `sudo systemctl restart baza-dashboard`; edit the company default in settings → save → a project with blank terms now renders the new company default on its invoice PDF.

- [ ] **Step 5: Final checkpoint** — `cd dashboard && python -m pytest tests/test_invoice_terms.py -v` (all green) + manual flows pass.
