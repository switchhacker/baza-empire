# AHB123 Quote → Invoice → Payment-Term Milestones → Email — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a chosen quote into an editable invoice, let each project define payment terms (presets or custom %), generate milestone invoices (deposit → progress → final) on demand that credit prior payments, show the schedule + status on the invoice, and email quotes/invoices with the PDF attached.

**Architecture:** Pure-function helpers (terms resolution, milestone math, line-item extraction, schedule HTML) are unit-tested in isolation; Flask routes wire them to SQLite (`baza_projects.db`) and the existing invoice/quote machinery; the milestone primary = the deposit invoice (stamped milestone 0 when terms are set); `next-invoice` issues subsequent milestones, with the legacy `balance-invoice` route kept for no-terms projects. Email gains stdlib `multipart/mixed` attachments referencing server-rendered PDFs.

**Tech Stack:** Python 3 / Flask, SQLite (`sqlite3`), weasyprint (existing PDF), stdlib `email.mime`, vanilla JS + Jinja templates, pytest (Flask `test_client` + `monkeypatch`).

**Spec:** `docs/superpowers/specs/2026-06-16-ahb123-quote-to-invoice-payment-terms-email-design.md`

### Conventions for every task
- **TDD:** write the test, watch it fail, implement minimally, watch it pass.
- **Run tests from repo root** `/home/switchhacker/baza-empire/agent-framework-v3` with `venv/bin/python -m pytest …`.
- **No manual git commits.** `claw-auto-git.timer` commits this tree hourly (CLAUDE.md). Each task ends by running tests green and — if `app.py`/`email_studio.py`/templates changed — `sudo systemctl restart baza-dashboard.service` so the running process picks it up.
- **DB env for tests:** set `BAZA_PROJECTS_DB` to a tmp path and import `dashboard.app` fresh (pattern in `tests/test_baza_scaffold_api.py` and `tests/test_estimator_llm_errors.py`).
- **Money/rounding:** all amounts `round(x, 2)`; "within a penny" tolerance for paid-in-full, matching `_ahb_project_payment_summary` (`app.py:6231`).

### Refinement adopted from spec (note)
The **primary invoice is milestone 0 (the deposit)**. It is stamped (milestone_index=0, label, amount_due, terms_snapshot) when payment terms are set (or when created from a quote with terms already set). `next-invoice` then issues milestones 1…N-1. Editing a term invoice's line items does **not** auto-recompute `amount_due` (frozen at generation) — out of scope here.

---

## File Structure

| File | Responsibility | Tasks |
|------|----------------|-------|
| `dashboard/app.py` | migrations; terms/quote/invoice/milestone helpers + routes; PDF schedule block | 1,2,3,5,6,8,9,10,11,13 |
| `dashboard/email_studio.py` | multipart attachments; `/send` attachment refs | 15,16 |
| `dashboard/templates/ahb123.html` | terms control + builder; replace/new prompt; Generate-next button; invoice schedule UI; Share buttons | 4,7,12,14,18 |
| `dashboard/templates/email.html` | From-account picker; `openCompose` attachments | 17,18 |
| `tests/test_payment_terms.py` | terms resolution + routes | 2,3 |
| `tests/test_quote_to_invoice.py` | quote→invoice line items | 5,6 |
| `tests/test_milestone_invoices.py` | total-paid, milestone math, next-invoice, balance compat | 8,9,10,11 |
| `tests/test_invoice_schedule_render.py` | schedule HTML block | 13 |
| `tests/test_email_attachments.py` | mime multipart + send refs | 15,16 |

---

# STEP 1 — Data model + payment terms

## Task 1: Additive migrations (5 new columns)

**Files:**
- Modify: `dashboard/app.py:408-475` (the `alter_stmts` idempotent migration list)
- Test: `tests/test_payment_terms.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_payment_terms.py
import importlib, os, sqlite3, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_PROJECTS_DB", str(tmp_path / "test.db"))
    for m in ("dashboard.app",):
        sys.modules.pop(m, None)
    mod = importlib.import_module("dashboard.app")
    mod.app.config["TESTING"] = True
    return mod


def _cols(app_module, table):
    conn = app_module._ahb_db()
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def test_new_columns_exist(app_module):
    proj = _cols(app_module, "ahb_projects")
    inv = _cols(app_module, "ahb_invoices")
    assert "payment_terms" in proj
    assert {"milestone_label", "milestone_index", "amount_due", "terms_snapshot"} <= inv
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_payment_terms.py::test_new_columns_exist -v`
Expected: FAIL — `payment_terms`/`milestone_*` not in column sets.

- [ ] **Step 3: Add the migration statements**

Insert into the `alter_stmts` list in `app.py` (just before the closing `]` at line 475):

```python
        # Payment terms + milestone invoices (2026-06-16)
        "ALTER TABLE ahb_projects ADD COLUMN payment_terms TEXT DEFAULT ''",
        "ALTER TABLE ahb_invoices ADD COLUMN milestone_label TEXT DEFAULT ''",
        "ALTER TABLE ahb_invoices ADD COLUMN milestone_index INTEGER DEFAULT -1",
        "ALTER TABLE ahb_invoices ADD COLUMN amount_due REAL",
        "ALTER TABLE ahb_invoices ADD COLUMN terms_snapshot TEXT DEFAULT ''",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_payment_terms.py::test_new_columns_exist -v`
Expected: PASS.

- [ ] **Step 5: Restart dashboard** (DB columns are added at boot)

Run: `sudo systemctl restart baza-dashboard.service && sleep 3 && systemctl is-active baza-dashboard.service`
Expected: `active`.

---

## Task 2: `_resolve_payment_terms()` — preset + custom validation

**Files:**
- Modify: `dashboard/app.py` (add helper near the other AHB helpers, e.g. after `_apply_quote_to_invoice` ~`app.py:5906`)
- Test: `tests/test_payment_terms.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_preset_50_50_resolves(app_module):
    t = app_module._resolve_payment_terms("50_50", None)
    assert t["preset"] == "50_50"
    assert [m["label"] for m in t["milestones"]] == ["Deposit", "Completion"]
    assert sum(m["pct"] for m in t["milestones"]) == 100


def test_preset_30_30_40_resolves(app_module):
    t = app_module._resolve_payment_terms("30_30_40", None)
    assert [m["pct"] for m in t["milestones"]] == [30, 30, 40]


def test_net_30_sets_net_days(app_module):
    t = app_module._resolve_payment_terms("net_30", None)
    assert t["net_days"] == 30
    assert t["milestones"][0]["pct"] == 100


def test_custom_must_sum_to_100(app_module):
    with pytest.raises(ValueError):
        app_module._resolve_payment_terms(
            "custom", [{"label": "A", "pct": 40}, {"label": "B", "pct": 40}])


def test_custom_requires_labels(app_module):
    with pytest.raises(ValueError):
        app_module._resolve_payment_terms("custom", [{"label": "", "pct": 100}])


def test_custom_valid(app_module):
    t = app_module._resolve_payment_terms(
        "custom", [{"label": "Deposit", "pct": 30},
                   {"label": "Rough-in", "pct": 30},
                   {"label": "Completion", "pct": 40}])
    assert t["preset"] == "custom"
    assert len(t["milestones"]) == 3
```

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest tests/test_payment_terms.py -k resolve -v` (plus the custom_* tests)
Expected: FAIL — `_resolve_payment_terms` not defined.

- [ ] **Step 3: Implement the helper**

```python
_PAYMENT_TERM_PRESETS = {
    "50_50": [{"label": "Deposit", "pct": 50}, {"label": "Completion", "pct": 50}],
    "30_30_40": [{"label": "Deposit", "pct": 30}, {"label": "Progress", "pct": 30},
                 {"label": "Final", "pct": 40}],
    "100_completion": [{"label": "Completion", "pct": 100}],
    "net_30": [{"label": "Net 30", "pct": 100}],
}


def _resolve_payment_terms(preset, milestones):
    """Normalize a preset name or a custom milestone list into a validated
    terms dict: {preset, net_days, milestones:[{label,pct}]}. Raises
    ValueError if a custom list lacks labels or its pct does not sum to 100."""
    preset = (preset or "").strip()
    net_days = 30 if preset == "net_30" else 0
    if preset in _PAYMENT_TERM_PRESETS:
        ms = [dict(m) for m in _PAYMENT_TERM_PRESETS[preset]]
    else:
        preset = "custom"
        ms = []
        for m in (milestones or []):
            label = (m.get("label") or "").strip()
            if not label:
                raise ValueError("each milestone needs a label")
            try:
                pct = float(m.get("pct") or 0)
            except (TypeError, ValueError):
                raise ValueError("milestone pct must be a number")
            ms.append({"label": label, "pct": pct})
    if not ms:
        raise ValueError("at least one milestone is required")
    total = round(sum(float(m["pct"]) for m in ms), 2)
    if abs(total - 100.0) > 0.01:
        raise ValueError(f"milestone percentages must sum to 100 (got {total})")
    return {"preset": preset, "net_days": net_days, "milestones": ms}
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_payment_terms.py -v`
Expected: PASS (all resolution tests).

---

## Task 3: GET/PUT `/api/ahb/projects/<pid>/payment-terms`

**Files:**
- Modify: `dashboard/app.py` (add routes near the quotes routes ~`app.py:5909`)
- Test: `tests/test_payment_terms.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.fixture
def client(app_module):
    # seed one project
    conn = app_module._ahb_db()
    conn.execute("INSERT INTO ahb_projects (id, title, status) VALUES ('p1','Smith kitchen','Planning')")
    conn.commit(); conn.close()
    return app_module.app.test_client()


def test_put_and_get_terms(client):
    r = client.put("/api/ahb/projects/p1/payment-terms", json={"preset": "30_30_40"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert [m["pct"] for m in body["terms"]["milestones"]] == [30, 30, 40]

    g = client.get("/api/ahb/projects/p1/payment-terms")
    assert g.get_json()["terms"]["preset"] == "30_30_40"


def test_put_custom_bad_sum_rejected(client):
    r = client.put("/api/ahb/projects/p1/payment-terms",
                   json={"preset": "custom",
                         "milestones": [{"label": "A", "pct": 60},
                                        {"label": "B", "pct": 50}]})
    assert r.status_code == 400
    assert "100" in r.get_json()["error"]
```

- [ ] **Step 2: Run to verify fail**

Run: `venv/bin/python -m pytest tests/test_payment_terms.py -k terms -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Implement the routes**

```python
@app.route('/api/ahb/projects/<pid>/payment-terms', methods=['GET', 'PUT'])
def api_ahb_project_payment_terms(pid):
    conn = _ahb_db()
    try:
        proj = conn.execute("SELECT id, payment_terms FROM ahb_projects WHERE id=?", (pid,)).fetchone()
        if not proj:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        if request.method == 'GET':
            raw = (dict(proj).get('payment_terms') or '').strip()
            terms = json.loads(raw) if raw else {'preset': '', 'net_days': 0, 'milestones': []}
            return jsonify({'success': True, 'terms': terms})
        d = request.get_json() or {}
        try:
            terms = _resolve_payment_terms(d.get('preset'), d.get('milestones'))
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
        conn.execute("UPDATE ahb_projects SET payment_terms=?, updated_at=? WHERE id=?",
                     (json.dumps(terms), datetime.datetime.now().isoformat(), pid))
        # Stamp the primary invoice as milestone 0 (the deposit) so its
        # schedule/amount-due reflect the chosen terms immediately.
        _stamp_primary_as_deposit(conn, pid, terms)
        conn.commit()
        return jsonify({'success': True, 'terms': terms})
    finally:
        conn.close()
```

`_stamp_primary_as_deposit` is implemented in Task 9 (it needs the milestone-math helper). For now add a temporary no-op shim **above** this route so Task 3 passes in isolation, then Task 9 replaces it:

```python
def _stamp_primary_as_deposit(conn, pid, terms):
    """Stamp the project's primary invoice as milestone 0. Filled in Task 9."""
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_payment_terms.py -v`
Expected: PASS.

- [ ] **Step 5: Restart dashboard**

Run: `sudo systemctl restart baza-dashboard.service && sleep 3 && systemctl is-active baza-dashboard.service`

---

## Task 4: Payment-terms UI control + custom builder (frontend)

**Files:**
- Modify: `dashboard/templates/ahb123.html` — Invoices & Billing panel (~`4688-4701`); add JS near the project-detail invoice functions.

- [ ] **Step 1: Add the markup** inside the Invoices & Billing panel, above the invoice list:

```html
<div id="pd-terms-box" style="margin:8px 0;font-size:12px;">
  <label style="color:#94a3b8;">Payment terms:
    <select id="pd-terms-preset" onchange="pdOnTermsPreset()" style="margin-left:6px;">
      <option value="">— none —</option>
      <option value="50_50">50% deposit / 50% completion</option>
      <option value="30_30_40">30 / 30 / 40 (deposit / progress / final)</option>
      <option value="100_completion">100% on completion</option>
      <option value="net_30">Net 30</option>
      <option value="custom">Custom…</option>
    </select>
  </label>
  <div id="pd-terms-custom" style="display:none;margin-top:6px;">
    <div id="pd-terms-rows"></div>
    <button type="button" onclick="pdAddTermRow()">+ milestone</button>
    <span id="pd-terms-sum" style="margin-left:8px;"></span>
  </div>
  <button type="button" id="pd-terms-save" onclick="pdSaveTerms()" style="margin-top:6px;">Save terms</button>
  <span id="pd-terms-current" style="margin-left:8px;color:#22c55e;"></span>
</div>
```

- [ ] **Step 2: Add the JS** (new functions; `PD_PID` is the current project id already used by the panel — match the existing variable name in the file):

```javascript
function pdOnTermsPreset() {
  const v = document.getElementById('pd-terms-preset').value;
  document.getElementById('pd-terms-custom').style.display = (v === 'custom') ? 'block' : 'none';
  if (v === 'custom' && !document.querySelectorAll('#pd-terms-rows .pd-term-row').length) {
    pdAddTermRow('Deposit', 50); pdAddTermRow('Completion', 50);
  }
  pdRecalcTermSum();
}
function pdAddTermRow(label, pct) {
  const row = document.createElement('div');
  row.className = 'pd-term-row'; row.style.margin = '3px 0';
  row.innerHTML = `<input class="pd-term-label" placeholder="Stage" value="${label||''}" style="width:120px;">
    <input class="pd-term-pct" type="number" min="0" max="100" value="${pct||0}" style="width:60px;" oninput="pdRecalcTermSum()">%
    <button type="button" onclick="this.parentNode.remove();pdRecalcTermSum()">✕</button>`;
  document.getElementById('pd-terms-rows').appendChild(row);
  pdRecalcTermSum();
}
function pdRecalcTermSum() {
  let s = 0;
  document.querySelectorAll('#pd-terms-rows .pd-term-pct').forEach(i => s += parseFloat(i.value || 0));
  const el = document.getElementById('pd-terms-sum');
  el.textContent = `sum = ${s}%` + (Math.abs(s - 100) < 0.01 ? ' ✓' : ' ✗');
  el.style.color = (Math.abs(s - 100) < 0.01) ? '#22c55e' : '#ef4444';
  document.getElementById('pd-terms-save').disabled =
    (document.getElementById('pd-terms-preset').value === 'custom') && Math.abs(s - 100) >= 0.01;
}
function pdCollectMilestones() {
  return [...document.querySelectorAll('#pd-terms-rows .pd-term-row')].map(r => ({
    label: r.querySelector('.pd-term-label').value.trim(),
    pct: parseFloat(r.querySelector('.pd-term-pct').value || 0),
  }));
}
async function pdSaveTerms() {
  const preset = document.getElementById('pd-terms-preset').value;
  const payload = { preset };
  if (preset === 'custom') payload.milestones = pdCollectMilestones();
  const r = await fetch(`/api/ahb/projects/${PD_PID}/payment-terms`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const j = await r.json();
  if (!j.success) { showToast(j.error || 'Failed to save terms'); return; }
  pdRenderCurrentTerms(j.terms);
  showToast('Payment terms saved');
  pdLoadInvoice && pdLoadInvoice(PD_PID);   // refresh invoice panel (amount due may have changed)
}
function pdRenderCurrentTerms(terms) {
  const el = document.getElementById('pd-terms-current');
  if (!terms || !terms.milestones || !terms.milestones.length) { el.textContent = ''; return; }
  el.textContent = terms.milestones.map(m => `${m.pct}% ${m.label}`).join(' · ');
}
async function pdLoadTerms() {
  const r = await fetch(`/api/ahb/projects/${PD_PID}/payment-terms`);
  const j = await r.json();
  if (j.success && j.terms) {
    document.getElementById('pd-terms-preset').value = j.terms.preset || '';
    pdOnTermsPreset();
    if (j.terms.preset === 'custom') {
      document.getElementById('pd-terms-rows').innerHTML = '';
      (j.terms.milestones || []).forEach(m => pdAddTermRow(m.label, m.pct));
    }
    pdRenderCurrentTerms(j.terms);
  }
}
```

- [ ] **Step 3:** Call `pdLoadTerms()` where the project detail modal loads its billing panel (alongside the existing `pdLoadInvoice`/`pdLoadPayments` calls — find them in the modal-open handler and add the call).

- [ ] **Step 4: Restart + manual verify**

Run: `sudo systemctl restart baza-dashboard.service && sleep 3`
Open a project → Invoices & Billing → pick 30/30/40, Save → "30% Deposit · 30% Progress · 40% Final" shows; reopen → persists; Custom builder enforces sum=100 before Save.

---

# STEP 2 — Quote → invoice

## Task 5: `_invoice_line_items_from_quote()` — structured-first, description fallback

**Files:**
- Modify: `dashboard/app.py` (add near `_line_items_from_description` ~`app.py:5848`)
- Test: `tests/test_quote_to_invoice.py`

- [ ] **Step 1: Write the failing tests** (reuse the `app_module` fixture pattern from Task 1)

```python
# tests/test_quote_to_invoice.py
import importlib, os, sys
import pytest
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_PROJECTS_DB", str(tmp_path / "test.db"))
    sys.modules.pop("dashboard.app", None)
    mod = importlib.import_module("dashboard.app")
    mod.app.config["TESTING"] = True
    return mod


def test_structured_breakdown_line_items(app_module):
    quote = {"total": 20000, "description": "ignore me",
             "breakdown": {"line_items": [
                 {"description": "Framing", "total": 8000},
                 {"description": "Cabinets", "total": 12000}]}}
    items = app_module._invoice_line_items_from_quote(quote)
    assert [i["description"] for i in items] == ["Framing", "Cabinets"]
    assert items[0]["total"] == 8000 and items[0]["include_in_total"] is True


def test_falls_back_to_description(app_module):
    quote = {"total": 5000, "description": "Demo\nHaul away", "breakdown": {}}
    items = app_module._invoice_line_items_from_quote(quote)
    assert [i["description"] for i in items] == ["Demo", "Haul away"]
```

- [ ] **Step 2: Run to verify fail**

Run: `venv/bin/python -m pytest tests/test_quote_to_invoice.py -v`
Expected: FAIL — `_invoice_line_items_from_quote` not defined.

- [ ] **Step 3: Implement**

```python
def _invoice_line_items_from_quote(quote):
    """Build invoice line_items from a quote/estimate, preferring structured
    data (breakdown.line_items / line_items JSON) and falling back to the
    description-text parse when no structured items exist."""
    breakdown = quote.get("breakdown")
    if isinstance(breakdown, str):
        try:
            breakdown = json.loads(breakdown) if breakdown else {}
        except Exception:
            breakdown = {}
    structured = None
    if isinstance(breakdown, dict) and isinstance(breakdown.get("line_items"), list):
        structured = breakdown["line_items"]
    elif isinstance(quote.get("line_items"), (list, str)):
        structured = _parse_line_items(quote.get("line_items"))
    if structured:
        items = []
        for li in structured:
            if not isinstance(li, dict):
                continue
            total = float(li.get("total") or li.get("amount") or 0)
            items.append({
                "description": (li.get("description") or li.get("label") or "").strip() or "Item",
                "qty": li.get("qty") or li.get("quantity") or 1,
                "quantity": li.get("qty") or li.get("quantity") or 1,
                "unit": li.get("unit") or "qty",
                "rate": float(li.get("rate") or li.get("unit_price") or total or 0),
                "unit_price": float(li.get("rate") or li.get("unit_price") or total or 0),
                "materials": float(li.get("materials") or 0),
                "labor": float(li.get("labor") or 0),
                "total": total,
                "include_in_total": li.get("include_in_total", True) is not False,
            })
        if items:
            return items
    return _line_items_from_description(quote.get("description") or "", quote.get("total") or 0)
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_quote_to_invoice.py -v`
Expected: PASS.

---

## Task 6: Quote→invoice create-or-replace (no silent overwrite)

**Files:**
- Modify: `dashboard/app.py` — `_apply_quote_to_invoice` (~`5890`) and the quotes POST handler (~`5929-5933`)
- Test: `tests/test_quote_to_invoice.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.fixture
def client(app_module):
    conn = app_module._ahb_db()
    conn.execute("INSERT INTO ahb_projects (id,title,status) VALUES ('p1','Smith','Planning')")
    conn.commit(); conn.close()
    return app_module.app.test_client()


def _make_quote(client, **extra):
    body = {"total": 20000, "description": "Framing\nCabinets",
            "breakdown": {"line_items": [{"description": "Framing", "total": 8000},
                                          {"description": "Cabinets", "total": 12000}]},
            "make_active": True}
    body.update(extra)
    return client.post("/api/ahb/projects/p1/quotes", json=body)


def test_active_quote_creates_primary_when_none(client, app_module):
    r = _make_quote(client)
    assert r.status_code == 200
    conn = app_module._ahb_db()
    invs = [dict(x) for x in conn.execute("SELECT * FROM ahb_invoices WHERE project_id='p1'").fetchall()]
    conn.close()
    assert len(invs) == 1
    items = app_module._parse_line_items(invs[0]["line_items"])
    assert [i["description"] for i in items] == ["Framing", "Cabinets"]
    assert invs[0]["is_primary"] == 1
    assert float(invs[0]["subtotal"]) == 20000


def test_existing_primary_requires_decision(client):
    _make_quote(client)               # primary now exists
    r = _make_quote(client, on_existing=None)   # second active quote, no decision
    assert r.status_code == 409
    assert "on_existing" in r.get_json()["error"]


def test_existing_primary_new_creates_second(client, app_module):
    _make_quote(client)
    r = _make_quote(client, on_existing="new")
    assert r.status_code == 200
    conn = app_module._ahb_db()
    n = conn.execute("SELECT count(*) c FROM ahb_invoices WHERE project_id='p1'").fetchone()["c"]
    conn.close()
    assert n == 2
```

- [ ] **Step 2: Run to verify fail**

Run: `venv/bin/python -m pytest tests/test_quote_to_invoice.py -k "primary or decision" -v`
Expected: FAIL (no primary created / no 409 logic).

- [ ] **Step 3: Replace `_apply_quote_to_invoice` and wire the POST handler**

Replace `_apply_quote_to_invoice` (`app.py:5890-5906`) with:

```python
def _create_primary_invoice_from_quote(c, project, quote):
    """Create a new primary invoice carrying the quote's line items."""
    pid = project["id"]
    items = _invoice_line_items_from_quote(quote)
    subtotal = float(quote.get("total") or 0)
    iid = uuid.uuid4().hex[:24]
    inv_num = f"AHB-{datetime.datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:3].upper()}"
    c.execute(
        """INSERT INTO ahb_invoices (id, client_id, project_id, invoice_number, line_items,
           subtotal, tax, total, status, notes, client_name, project_name,
           date, is_primary, project_address)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (iid, project.get("client_id") or "", pid, inv_num, json.dumps(items),
         subtotal, 0, subtotal, "draft", "",
         project.get("client_name") or "", project.get("title") or "",
         datetime.datetime.now().date().isoformat(), 1,
         project.get("address") or project.get("location") or ""))
    return iid


def _apply_quote_to_invoice(c, project_id, quote, on_existing):
    """Apply an active quote to the project's invoice. Returns (invoice_id, error_or_None).
    - No primary yet: create one. - Primary exists: require on_existing in
    {'replace','new'} (no silent overwrite)."""
    project = c.execute("SELECT * FROM ahb_projects WHERE id=?", (project_id,)).fetchone()
    project = dict(project) if project else {"id": project_id}
    has_primary = _project_has_primary_invoice(c, project_id)
    if not has_primary:
        return _create_primary_invoice_from_quote(c, project, quote), None
    if on_existing == "new":
        return _create_primary_invoice_from_quote(c, project, quote), None  # is_primary swap is manual
    if on_existing == "replace":
        inv = c.execute(
            "SELECT id FROM ahb_invoices WHERE project_id=? ORDER BY is_primary DESC, created_at ASC LIMIT 1",
            (project_id,)).fetchone()
        items = _invoice_line_items_from_quote(quote)
        total = float(quote.get("total") or 0)
        c.execute("UPDATE ahb_invoices SET line_items=?, subtotal=?, total=?, tax=0, updated_at=? WHERE id=?",
                  (json.dumps(items), total, total, datetime.datetime.now().isoformat(), inv["id"]))
        return inv["id"], None
    return None, "a primary invoice already exists — pass on_existing='replace' or 'new'"
```

In the quotes POST handler (`app.py:5929-5933`), replace the `_apply_quote_to_invoice(c, pid, total, d.get('description',''))` call with:

```python
        if d.get('make_active'):
            c.execute("UPDATE ahb_quotes SET is_active=0 WHERE project_id=? AND id<>?", (pid, qid))
            c.execute("UPDATE ahb_projects SET value=?, budget_high=?, updated_at=? WHERE id=?",
                      (total, total, datetime.datetime.now().isoformat(), pid))
            quote_row = dict(c.execute("SELECT * FROM ahb_quotes WHERE id=?", (qid,)).fetchone())
            inv_id, err = _apply_quote_to_invoice(c, pid, quote_row, d.get('on_existing'))
            if err:
                conn.rollback(); conn.close()
                return jsonify({'success': False, 'error': err, 'needs_decision': True}), 409
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_quote_to_invoice.py -v`
Expected: PASS.

- [ ] **Step 5: Restart dashboard.**

---

## Task 7: Replace/new prompt (frontend)

**Files:**
- Modify: `dashboard/templates/ahb123.html` — the function that POSTs a quote with `make_active` (the "Make Active" / save-quote handler in the Quotes panel).

- [ ] **Step 1:** In the quote-activate handler, on a `409` with `needs_decision`, prompt and retry:

```javascript
async function activateQuote(pid, quoteBody) {
  let r = await fetch(`/api/ahb/projects/${pid}/quotes`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...quoteBody, make_active: true }) });
  if (r.status === 409 && (await r.clone().json()).needs_decision) {
    const choice = confirm("This project already has a primary invoice.\n\nOK = REPLACE its line items with this quote.\nCancel = create a NEW invoice from this quote.");
    r = await fetch(`/api/ahb/projects/${pid}/quotes`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...quoteBody, make_active: true, on_existing: choice ? 'replace' : 'new' }) });
  }
  const j = await r.json();
  if (!j.success) { showToast(j.error || 'Failed'); return; }
  showToast('Quote applied'); pdLoadInvoice && pdLoadInvoice(pid);
}
```

(Wire the existing "Make Active" button to call `activateQuote(PD_PID, {...})` with the quote fields it already collects. Match the existing collection code in the panel.)

- [ ] **Step 2: Restart + manual verify** — first active quote creates the primary; a second active quote prompts replace-vs-new and acts accordingly.

---

# STEP 3 — Generate next invoice (milestones)

## Task 8: `_project_total_paid()` — payments across all project invoices

**Files:**
- Modify: `dashboard/app.py` (add near `_ahb_project_payment_summary` ~`6240`)
- Test: `tests/test_milestone_invoices.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_milestone_invoices.py
import importlib, os, sys
import pytest
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_PROJECTS_DB", str(tmp_path / "test.db"))
    sys.modules.pop("dashboard.app", None)
    mod = importlib.import_module("dashboard.app")
    mod.app.config["TESTING"] = True
    return mod


def test_total_paid_sums_across_invoices(app_module):
    conn = app_module._ahb_db()
    conn.execute("INSERT INTO ahb_projects (id,title) VALUES ('p1','x')")
    conn.execute("INSERT INTO ahb_invoices (id,project_id,total,is_primary) VALUES ('i1','p1',20000,1)")
    conn.execute("INSERT INTO ahb_invoices (id,project_id,total) VALUES ('i2','p1',0)")
    conn.execute("INSERT INTO ahb_payments (id,invoice_id,amount) VALUES ('y1','i1',6000)")
    conn.execute("INSERT INTO ahb_payments (id,invoice_id,amount) VALUES ('y2','i2',6000)")
    conn.commit()
    assert app_module._project_total_paid(conn, 'p1') == 12000.0
    conn.close()
```

- [ ] **Step 2: Run to verify fail.** `venv/bin/python -m pytest tests/test_milestone_invoices.py::test_total_paid_sums_across_invoices -v` → FAIL (not defined).

- [ ] **Step 3: Implement**

```python
def _project_total_paid(conn, pid):
    """Sum of all payments across every invoice belonging to the project."""
    row = conn.execute(
        "SELECT COALESCE(SUM(p.amount),0) AS paid FROM ahb_payments p "
        "JOIN ahb_invoices i ON p.invoice_id = i.id WHERE i.project_id = ?",
        (pid,)).fetchone()
    return float(row["paid"] or 0) if row else 0.0
```

- [ ] **Step 4: Run to verify pass.** Expected: PASS.

---

## Task 9: Milestone math + stamp-primary (self-healing amount due)

**Files:**
- Modify: `dashboard/app.py` — add `_compute_milestone_amount_due`; replace the Task 3 `_stamp_primary_as_deposit` shim.
- Test: `tests/test_milestone_invoices.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_milestone_due_exact_payments(app_module):
    ms = [{"label": "Deposit", "pct": 30}, {"label": "Progress", "pct": 30}, {"label": "Final", "pct": 40}]
    f = app_module._compute_milestone_amount_due
    assert f(20000, ms, 0, 0) == 6000        # deposit, nothing paid
    assert f(20000, ms, 1, 6000) == 6000     # progress, deposit paid
    assert f(20000, ms, 2, 12000) == 8000    # final clears remainder


def test_milestone_due_underpaid_deposit_self_heals(app_module):
    ms = [{"label": "Deposit", "pct": 50}, {"label": "Final", "pct": 50}]
    f = app_module._compute_milestone_amount_due
    # client paid only 9000 of a 10000 deposit; final must collect 11000
    assert f(20000, ms, 1, 9000) == 11000


def test_milestone_due_overpaid_clamps_zero(app_module):
    ms = [{"label": "Deposit", "pct": 50}, {"label": "Progress", "pct": 25}, {"label": "Final", "pct": 25}]
    f = app_module._compute_milestone_amount_due
    # paid 15000 already, cumulative target through progress = 15000 -> 0 due
    assert f(20000, ms, 1, 15000) == 0
```

- [ ] **Step 2: Run to verify fail.** FAIL — not defined.

- [ ] **Step 3: Implement the math + real stamp helper**

```python
def _compute_milestone_amount_due(contract, milestones, k, paid):
    """Self-healing amount due for milestone index k (0-based):
    cumulative-% target through k minus total paid; the final milestone
    clears to the true remaining balance. Negatives clamp to 0."""
    contract = float(contract or 0); paid = float(paid or 0)
    if k >= len(milestones) - 1:                 # final milestone
        due = contract - paid
    else:
        cum_pct = sum(float(m.get("pct") or 0) for m in milestones[:k + 1]) / 100.0
        due = round(contract * cum_pct, 2) - paid
    return round(max(0.0, due), 2)


def _stamp_primary_as_deposit(conn, pid, terms):
    """Stamp the project's primary invoice as milestone 0 (the deposit):
    label, index 0, frozen terms snapshot, and self-healing amount_due."""
    ms = (terms or {}).get("milestones") or []
    if not ms:
        return None
    primary = conn.execute(
        "SELECT id, subtotal, total FROM ahb_invoices WHERE project_id=? "
        "ORDER BY is_primary DESC, created_at ASC LIMIT 1", (pid,)).fetchone()
    if not primary:
        return None
    primary = dict(primary)
    contract = float(primary.get("subtotal") or primary.get("total") or 0)
    paid = _project_total_paid(conn, pid)
    due = _compute_milestone_amount_due(contract, ms, 0, paid)
    conn.execute(
        "UPDATE ahb_invoices SET milestone_label=?, milestone_index=0, amount_due=?, "
        "terms_snapshot=? WHERE id=?",
        (ms[0]["label"], due, json.dumps(terms), primary["id"]))
    return primary["id"]
```

- [ ] **Step 4: Run to verify pass.** `venv/bin/python -m pytest tests/test_milestone_invoices.py -k "milestone_due" -v` → PASS.

- [ ] **Step 5:** Re-run Task 3 terms tests to confirm the real stamp helper didn't break them: `venv/bin/python -m pytest tests/test_payment_terms.py -v` → PASS.

---

## Task 10: `POST /api/ahb/projects/<pid>/next-invoice`

**Files:**
- Modify: `dashboard/app.py` (add route near `balance-invoice` ~`6614`)
- Test: `tests/test_milestone_invoices.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.fixture
def client(app_module):
    conn = app_module._ahb_db()
    conn.execute("INSERT INTO ahb_projects (id,title,status) VALUES ('p1','Smith','Planning')")
    conn.execute("INSERT INTO ahb_invoices (id,project_id,invoice_number,subtotal,total,is_primary) "
                 "VALUES ('i1','p1','AHB-1',20000,20000,1)")
    conn.commit(); conn.close()
    return app_module.app.test_client()


def _set_terms(client, preset):
    return client.put("/api/ahb/projects/p1/payment-terms", json={"preset": preset})


def test_next_invoice_requires_terms(client):
    r = client.post("/api/ahb/projects/p1/next-invoice")
    assert r.status_code == 400


def test_next_invoice_issues_second_milestone(client, app_module):
    _set_terms(client, "50_50")                      # primary stamped milestone 0 (deposit)
    # record the 10000 deposit against the primary
    client.post("/api/ahb/payments", json={"invoice_id": "i1", "amount": 10000})
    r = client.post("/api/ahb/projects/p1/next-invoice")
    assert r.status_code == 200
    body = r.get_json()
    assert body["milestone_index"] == 1
    assert body["milestone_label"] == "Completion"
    assert body["amount_due"] == 10000     # 20000 contract - 10000 paid
    conn = app_module._ahb_db()
    inv = dict(conn.execute("SELECT * FROM ahb_invoices WHERE id=?", (body["id"],)).fetchone())
    conn.close()
    assert inv["parent_invoice_id"] == "i1"
    # full scope copied: subtotal equals contract
    assert float(inv["subtotal"]) == 20000
    assert inv["is_primary"] == 0


def test_next_invoice_409_when_all_issued(client):
    _set_terms(client, "50_50")
    client.post("/api/ahb/projects/p1/next-invoice")   # issues milestone 1 (final)
    r = client.post("/api/ahb/projects/p1/next-invoice")  # nothing left
    assert r.status_code == 409
```

- [ ] **Step 2: Run to verify fail.** FAIL — route missing (404).

- [ ] **Step 3: Implement the route**

```python
@app.route('/api/ahb/projects/<pid>/next-invoice', methods=['POST'])
def api_ahb_project_next_invoice(pid):
    """Generate the next milestone invoice from the project's payment terms.
    The primary invoice is milestone 0 (deposit); this issues 1..N-1, each
    carrying the full contract line items and a self-healing amount_due."""
    try:
        conn = _ahb_db()
        project = conn.execute("SELECT * FROM ahb_projects WHERE id=?", (pid,)).fetchone()
        if not project:
            conn.close(); return jsonify({'success': False, 'error': 'Project not found'}), 404
        project = dict(project)
        raw = (project.get('payment_terms') or '').strip()
        if not raw:
            conn.close()
            return jsonify({'success': False, 'error': 'set payment terms first'}), 400
        terms = json.loads(raw)
        milestones = terms.get('milestones') or []
        primary = conn.execute(
            "SELECT * FROM ahb_invoices WHERE project_id=? ORDER BY is_primary DESC, created_at ASC LIMIT 1",
            (pid,)).fetchone()
        if not primary:
            conn.close()
            return jsonify({'success': False, 'error': 'create the primary invoice first'}), 400
        primary = dict(primary)
        # Ensure the primary is stamped as milestone 0.
        if int(primary.get('milestone_index') if primary.get('milestone_index') is not None else -1) != 0:
            _stamp_primary_as_deposit(conn, pid, terms); conn.commit()
        issued = {int(r['milestone_index']) for r in conn.execute(
            "SELECT milestone_index FROM ahb_invoices WHERE project_id=? AND milestone_index>=0", (pid,)
        ).fetchall()}
        next_k = next((k for k in range(len(milestones)) if k not in issued), None)
        if next_k is None:
            conn.close()
            return jsonify({'success': False, 'error': 'all milestones invoiced'}), 409

        contract = float(primary.get('subtotal') or primary.get('total') or 0)
        paid = _project_total_paid(conn, pid)
        due = _compute_milestone_amount_due(contract, milestones, next_k, paid)
        line_items = _parse_line_items(primary.get('line_items')) or []

        iid = uuid.uuid4().hex[:24]
        inv_num = f"AHB-{datetime.datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:3].upper()}"
        label = milestones[next_k]['label']
        conn.execute(
            """INSERT INTO ahb_invoices (id, client_id, project_id, invoice_number, line_items,
               subtotal, tax, total, status, notes, client_name, project_name, terms,
               date, parent_invoice_id, is_primary, company_name, contractor_name,
               client_address, client_email, client_phone, project_address,
               milestone_label, milestone_index, amount_due, terms_snapshot)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (iid, primary.get('client_id') or project.get('client_id') or '',
             pid, inv_num, json.dumps(line_items), contract, 0, contract, 'draft',
             f"{label} payment due.",
             primary.get('client_name') or project.get('client_name') or '',
             primary.get('project_name') or project.get('title') or '',
             primary.get('terms') or '',
             datetime.datetime.now().date().isoformat(),
             primary['id'], 0,
             primary.get('company_name') or 'All Home Building Co',
             primary.get('contractor_name') or 'Sergey Tkach',
             primary.get('client_address') or '',
             primary.get('client_email') or project.get('client_email') or '',
             primary.get('client_phone') or '',
             primary.get('project_address') or project.get('address') or '',
             label, next_k, due, json.dumps(terms)))
        conn.commit(); conn.close()
        return jsonify({'success': True, 'id': iid, 'invoice_number': inv_num,
                        'milestone_label': label, 'milestone_index': next_k,
                        'contract': contract, 'paid': paid, 'amount_due': due})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
```

- [ ] **Step 4: Run to verify pass.** `venv/bin/python -m pytest tests/test_milestone_invoices.py -v` → PASS.

- [ ] **Step 5: Restart dashboard.**

---

## Task 11: Keep `balance-invoice` working for no-terms projects

**Files:**
- Test only: `tests/test_milestone_invoices.py` (regression guard — `balance-invoice` is unchanged by this plan)

- [ ] **Step 1: Write the guard test**

```python
def test_balance_invoice_still_works_without_terms(client, app_module):
    client.post("/api/ahb/payments", json={"invoice_id": "i1", "amount": 5000})
    r = client.post("/api/ahb/projects/p1/balance-invoice")
    assert r.status_code == 200
    assert r.get_json()["balance"] == 15000   # 20000 - 5000
```

- [ ] **Step 2: Run.** Expected: PASS immediately (route pre-exists, untouched) — confirms no regression. If it fails, the quote→invoice refactor (Task 6) broke a shared helper; fix there.

---

## Task 12: "Generate next invoice" button (frontend)

**Files:**
- Modify: `dashboard/templates/ahb123.html` — Invoices & Billing panel action row (the existing "⚖ Balance Invoice" button area ~`4688-4701`).

- [ ] **Step 1:** Add a button + handler; show it when terms are set, fall back to balance-invoice when not:

```html
<button type="button" id="pd-next-invoice-btn" onclick="pdGenerateNextInvoice()">＋ Generate next invoice</button>
```

```javascript
async function pdGenerateNextInvoice() {
  const r = await fetch(`/api/ahb/projects/${PD_PID}/next-invoice`, { method: 'POST' });
  const j = await r.json();
  if (!j.success) {
    if (r.status === 400) { showToast('Set payment terms first'); return; }
    if (r.status === 409) { showToast('All milestones already invoiced'); return; }
    showToast(j.error || 'Failed'); return;
  }
  showToast(`Created ${j.milestone_label} invoice — $${j.amount_due.toLocaleString()} due`);
  pdLoadInvoice && pdLoadInvoice(PD_PID);
}
```

- [ ] **Step 2: Restart + manual verify** — with 50/50 terms and a recorded deposit, the button creates the Completion invoice showing the balance due; a second click reports "all milestones already invoiced."

---

# STEP 4 — Rendering (PDF + UI)

## Task 13: `_payment_schedule_block()` + insert into PDF

**Files:**
- Modify: `dashboard/app.py` — add helper; insert call into the invoice PDF f-string after the totals div (`app.py:10114`, right after the closing `</div>` of the totals block).
- Test: `tests/test_invoice_schedule_render.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_invoice_schedule_render.py
import importlib, json, os, sys
import pytest
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_PROJECTS_DB", str(tmp_path / "test.db"))
    sys.modules.pop("dashboard.app", None)
    mod = importlib.import_module("dashboard.app")
    return mod


def test_non_term_invoice_no_block(app_module):
    assert app_module._payment_schedule_block({"milestone_index": -1, "amount_due": None}) == ""


def test_term_invoice_renders_schedule(app_module):
    terms = {"preset": "50_50", "milestones": [{"label": "Deposit", "pct": 50},
                                               {"label": "Completion", "pct": 50}]}
    inv = {"milestone_index": 1, "amount_due": 10000, "total": 20000, "status": "draft",
           "terms_snapshot": json.dumps(terms)}
    html = app_module._payment_schedule_block(inv)
    assert "PAYMENT SCHEDULE" in html
    assert "Deposit" in html and "Completion" in html
    assert "AMOUNT DUE NOW" in html
    assert "10,000" in html        # due-now amount formatted
```

- [ ] **Step 2: Run to verify fail.** FAIL — not defined.

- [ ] **Step 3: Implement the helper**

```python
def _payment_schedule_block(inv):
    """HTML block for a term-driven invoice's payment schedule, or '' for a
    plain invoice. Self-contained (own money formatter) so it is unit-testable
    and reusable in both the PDF and any HTML view."""
    try:
        idx = int(inv.get("milestone_index") if inv.get("milestone_index") is not None else -1)
    except (TypeError, ValueError):
        idx = -1
    raw = (inv.get("terms_snapshot") or "").strip()
    if idx < 0 or not raw:
        return ""
    try:
        terms = json.loads(raw)
    except Exception:
        return ""
    ms = terms.get("milestones") or []
    if not ms:
        return ""
    contract = float(inv.get("total") or 0)
    due = float(inv.get("amount_due") or 0)

    def m(x):
        return ("-$" if x < 0 else "$") + f"{abs(x):,.2f}"

    label = (terms.get("preset") or "").replace("_", " / ")
    rows = ""
    for k, mil in enumerate(ms):
        amt = round(contract * float(mil.get("pct") or 0) / 100.0, 2)
        marker = " ← this invoice" if k == idx else ""
        weight = "700" if k == idx else "400"
        rows += (f'<div style="display:flex;justify-content:space-between;font-weight:{weight};">'
                 f'<span>{mil.get("label","")} ({mil.get("pct",0)}%){marker}</span>'
                 f'<span>{m(amt)}</span></div>')
    status = (inv.get("status") or "draft").upper()
    return (
        '<div style="margin-top:18px;border-top:1px dashed #94a3b8;padding-top:10px;width:320px;font-size:12px;">'
        f'<div style="display:flex;justify-content:space-between;font-weight:700;color:#334155;">'
        f'<span>PAYMENT SCHEDULE ({label})</span><span>Status: {status}</span></div>'
        f'{rows}'
        '<div style="display:flex;justify-content:space-between;border-top:1px solid #333;'
        f'margin-top:6px;padding-top:6px;font-weight:700;color:#2563eb;">'
        f'<span>AMOUNT DUE NOW</span><span>{m(due)}</span></div></div>')
```

- [ ] **Step 4: Run to verify pass.** PASS.

- [ ] **Step 5: Insert into the PDF.** In `api_ahb_invoice_pdf` (`app.py`), after the totals `</div>` at line 10114, add to the HTML f-string:

```html
{_payment_schedule_block(invoice)}
```

(Use whatever the invoice dict variable is named in that function — confirm by reading ~`9912-9960`; it's the row fetched for the PDF. The variable holds `milestone_index`, `amount_due`, `terms_snapshot`, `total`, `status` since the SELECT is `*`.)

- [ ] **Step 6: Restart + verify** — open a milestone invoice PDF (`/api/ahb/invoices/<iid>/pdf`); the schedule block, terms label, status, and amount-due appear; a plain invoice PDF is unchanged.

---

## Task 14: Invoice detail UI shows schedule/terms/status/amount-due

**Files:**
- Modify: `dashboard/templates/ahb123.html` — the invoice detail render (`#pd-invoice-info`).

- [ ] **Step 1:** The invoice GET already returns the new fields (SELECT *). In the JS that renders `#pd-invoice-info`, append a schedule summary when `inv.milestone_index >= 0 && inv.terms_snapshot`:

```javascript
function pdInvoiceScheduleHtml(inv) {
  if (!inv || inv.milestone_index === undefined || inv.milestone_index < 0 || !inv.terms_snapshot) return '';
  let terms; try { terms = JSON.parse(inv.terms_snapshot); } catch (e) { return ''; }
  const ms = (terms.milestones || []);
  const contract = parseFloat(inv.total || 0);
  const rows = ms.map((m, k) => {
    const amt = Math.round(contract * (m.pct || 0)) / 100;
    const cur = (k === inv.milestone_index);
    return `<div style="display:flex;justify-content:space-between;${cur ? 'font-weight:700;' : ''}">
      <span>${m.label} (${m.pct}%)${cur ? ' ←' : ''}</span><span>$${amt.toLocaleString()}</span></div>`;
  }).join('');
  return `<div style="margin-top:8px;border-top:1px dashed #475569;padding-top:6px;font-size:12px;">
    <div style="display:flex;justify-content:space-between;font-weight:700;">
      <span>Payment schedule (${(terms.preset||'').replace(/_/g,' / ')})</span>
      <span>${(inv.status||'draft').toUpperCase()}</span></div>
    ${rows}
    <div style="display:flex;justify-content:space-between;color:#60a5fa;font-weight:700;margin-top:4px;">
      <span>Amount due now</span><span>$${parseFloat(inv.amount_due||0).toLocaleString()}</span></div></div>`;
}
```

Call it where `#pd-invoice-info` is built: append `pdInvoiceScheduleHtml(inv)` to that panel's HTML.

- [ ] **Step 2: Restart + verify** — the project's milestone invoice shows the schedule, terms label, status, and amount due in the panel; non-term invoices show nothing extra.

---

# STEP 5 — Email sharing (quotes + invoices)

## Task 15: `_mime_message` multipart/mixed with attachments

**Files:**
- Modify: `dashboard/email_studio.py` — `_mime_message` (`720-742`)
- Test: `tests/test_email_attachments.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_email_attachments.py
import base64, importlib, os, sys
import pytest
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


@pytest.fixture
def es():
    sys.modules.pop("email_studio", None)
    return importlib.import_module("email_studio")


def test_mime_message_attaches_pdf(es):
    raw = es._mime_message("a@b.com", "Subj", "hello",
                           attachments=[{"filename": "inv.pdf",
                                         "data": b"%PDF-1.4 fake",
                                         "mimetype": "application/pdf"}])
    decoded = base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
    assert "multipart/mixed" in decoded
    assert "inv.pdf" in decoded
    assert "hello" in decoded


def test_mime_message_no_attachments_unchanged(es):
    raw = es._mime_message("a@b.com", "Subj", "hello")
    decoded = base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
    assert "hello" in decoded
```

- [ ] **Step 2: Run to verify fail.** FAIL — `_mime_message` has no `attachments` param (TypeError).

- [ ] **Step 3: Implement** — add the import and extend the function. At the top of `email_studio.py` near the other mime imports (line ~22), ensure:

```python
from email.mime.base import MIMEBase
from email import encoders
```

Replace `_mime_message` with:

```python
def _mime_message(to: str, subject: str, body: str,
                  cc: str = "", bcc: str = "",
                  in_reply_to: str = "", references: str = "",
                  from_addr: str = "", attachments=None) -> str:
    inner = MIMEMultipart("alternative")
    inner.attach(MIMEText(body, "plain", _charset="utf-8"))
    html_body = "<pre style='font-family:inherit;white-space:pre-wrap'>" + \
                body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + \
                "</pre>"
    inner.attach(MIMEText(html_body, "html", _charset="utf-8"))

    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(inner)
        for att in attachments:
            mimetype = att.get("mimetype") or "application/octet-stream"
            maintype, _, subtype = mimetype.partition("/")
            part = MIMEBase(maintype or "application", subtype or "octet-stream")
            part.set_payload(att["data"])
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment",
                            filename=att.get("filename") or "attachment")
            msg.attach(part)
    else:
        msg = inner

    msg["To"] = to
    if cc: msg["Cc"] = cc
    if bcc: msg["Bcc"] = bcc
    if from_addr: msg["From"] = from_addr
    msg["Subject"] = subject
    if in_reply_to: msg["In-Reply-To"] = in_reply_to
    if references: msg["References"] = references
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
```

- [ ] **Step 4: Run to verify pass.** PASS.

---

## Task 16: `/send` resolves attachment refs (invoice_pdf, quote_pdf) + size cap

**Files:**
- Modify: `dashboard/email_studio.py` — `api_send` (`746-…`)
- Test: `tests/test_email_attachments.py`

- [ ] **Step 1: Write the failing test** (mock Gmail + the PDF renderer so no network/DB)

```python
def test_send_attaches_invoice_pdf(es, monkeypatch):
    captured = {}
    # Fake the gmail client + account resolution
    class FakeSend:
        def send(self, userId, body): captured["raw"] = body["raw"]; return {"id": "x"}
        def messages(self): return self
        def users(self): return self
        def execute(self): return {"id": "x"}
    monkeypatch.setattr(es, "_req_account_id", lambda: "acct")
    monkeypatch.setattr(es, "_gmail", lambda a: FakeSend())
    # Fake the invoice-PDF fetch helper the route will call
    monkeypatch.setattr(es, "_render_attachment_ref",
                        lambda ref: {"filename": "inv.pdf", "data": b"%PDF fake",
                                     "mimetype": "application/pdf"})
    es.app.config["TESTING"] = True if hasattr(es, "app") else None
    client = es.email_bp  # blueprint; use a test app
    # Easiest: call the function logic via a tiny Flask test app
    from flask import Flask
    app = Flask(__name__); app.register_blueprint(es.email_bp); app.config["TESTING"] = True
    c = app.test_client()
    r = c.post("/api/email2/send", json={"to": "a@b.com", "subject": "S", "body": "hi",
                                         "attachments": [{"type": "invoice_pdf", "invoice_id": "i1"}]})
    assert r.status_code == 200
    raw = base64.urlsafe_b64decode(captured["raw"]).decode("utf-8", "replace")
    assert "inv.pdf" in raw
```

- [ ] **Step 2: Run to verify fail.** FAIL — `_render_attachment_ref` missing / `attachments` ignored by route.

- [ ] **Step 3: Implement** — add a resolver and wire it into `api_send`.

Add near the top of `email_studio.py` (after imports), a resolver that calls back into the dashboard app for PDFs. Since `email_studio.py` is a blueprint on the same Flask app, import the render functions lazily to avoid a circular import:

```python
_ATTACH_CAP_BYTES = 25 * 1024 * 1024  # Gmail 25 MB

def _render_attachment_ref(ref):
    """Resolve a server-side attachment reference to {filename,data,mimetype}.
    Supported: {type:'invoice_pdf', invoice_id}, {type:'quote_pdf', quote_id}."""
    t = ref.get("type")
    if t == "invoice_pdf":
        from app import _render_invoice_pdf_bytes
        data, fname = _render_invoice_pdf_bytes(ref["invoice_id"])
        return {"filename": fname, "data": data, "mimetype": "application/pdf"}
    if t == "quote_pdf":
        from app import _render_quote_pdf_bytes
        data, fname = _render_quote_pdf_bytes(ref["quote_id"])
        return {"filename": fname, "data": data, "mimetype": "application/pdf"}
    raise ValueError(f"unsupported attachment type: {t}")
```

In `api_send`, after reading `body`, before building the message:

```python
    atts = []
    total_bytes = 0
    for ref in (data.get("attachments") or []):
        try:
            built = _render_attachment_ref(ref)
        except Exception as e:
            return jsonify({"ok": False, "error": f"attachment failed: {e}"}), 400
        total_bytes += len(built["data"])
        if total_bytes > _ATTACH_CAP_BYTES:
            return jsonify({"ok": False, "error": "attachments exceed 25 MB"}), 400
        atts.append(built)
```

And pass them through (both `_mime_message` call sites in `api_send` — the compose path at ~`785`):

```python
        raw = _mime_message(to, subject, body, cc=cc, bcc=bcc,
                            in_reply_to=in_reply_to, references=references,
                            attachments=atts)
```

- [ ] **Step 4: Implement the PDF-bytes helpers in `app.py`** so the resolver has something to import. These factor the existing PDF rendering into a bytes-returning function. Near `api_ahb_invoice_pdf` (`app.py:9908`), add:

```python
def _render_invoice_pdf_bytes(iid):
    """Return (pdf_bytes, filename) for an invoice — reuses the PDF route's
    renderer. Implemented by calling the existing view and reading its data."""
    with app.test_request_context():
        resp = api_ahb_invoice_pdf(iid)
    data = resp[0] if isinstance(resp, tuple) else resp
    pdf = data.get_data() if hasattr(data, "get_data") else bytes(data)
    return pdf, f"Invoice-{iid}.pdf"


def _render_quote_pdf_bytes(qid):
    with app.test_request_context():
        resp = api_ahb_quote_pdf(int(qid))
    data = resp[0] if isinstance(resp, tuple) else resp
    pdf = data.get_data() if hasattr(data, "get_data") else bytes(data)
    return pdf, f"Quote-{qid}.pdf"
```

(Confirm the PDF routes return a Flask `Response` with PDF bytes; if they `send_file`, `get_data()` still yields the bytes. Adjust the filename to the invoice number if readily available.)

- [ ] **Step 5: Run to verify pass.** `venv/bin/python -m pytest tests/test_email_attachments.py -v` → PASS.

- [ ] **Step 6: Restart dashboard.**

---

## Task 17: From-account picker (frontend)

**Files:**
- Modify: `dashboard/templates/email.html` — composer (~`506-514`) + `openCompose` (~`939-962`).

- [ ] **Step 1:** Add a From `<select>` to the composer markup:

```html
<label style="font-size:12px;color:#94a3b8;">From
  <select id="cmpFrom" style="margin-left:6px;"></select>
</label>
```

- [ ] **Step 2:** Populate it from `/api/email2/accounts` when the composer opens, and include it + any `pref.attachments` in the send payload:

```javascript
async function cmpLoadAccounts(selected) {
  const r = await fetch('/api/email2/accounts'); const j = await r.json();
  const sel = document.getElementById('cmpFrom'); sel.innerHTML = '';
  (j.accounts || j || []).forEach(a => {
    const email = a.email || a; const o = document.createElement('option');
    o.value = email; o.textContent = email; if (email === selected) o.selected = true;
    sel.appendChild(o);
  });
}
```

In the existing send handler, add `account: document.getElementById('cmpFrom').value` and `attachments: window.__cmpAttachments || []` to the POST body. In `openCompose(pref)`, call `cmpLoadAccounts(pref.account)` and set `window.__cmpAttachments = pref.attachments || []`.

- [ ] **Step 3:** Confirm the send route honors the account — `api_send` uses `_req_account_id()`; ensure it reads the `account` field from the JSON body (add `account` handling to `_req_account_id` or pass it explicitly). If `_req_account_id()` only checks a header, extend `api_send` to prefer `data.get("account")`.

- [ ] **Step 4: Restart + verify** — composer shows the account dropdown defaulting to the active account; sending uses the chosen account.

---

## Task 18: Share-via-email buttons on quote + invoice (frontend)

**Files:**
- Modify: `dashboard/templates/ahb123.html` — quote row actions (Quotes panel) and invoice actions; reuse `openCompose`.

- [ ] **Step 1:** Add buttons + handlers. They pre-fill `openCompose` with the client email, subject, and a server-side attachment ref:

```javascript
function shareQuoteEmail(quoteId, projectName, clientEmail) {
  openCompose({
    to: clientEmail || '',
    subject: `Quote — ${projectName || ''}`.trim(),
    body: 'Please find your quote attached.\n\n— All Home Building Co',
    attachments: [{ type: 'quote_pdf', quote_id: quoteId }],
  });
}
function shareInvoiceEmail(invoiceId, invoiceNumber, clientEmail) {
  openCompose({
    to: clientEmail || '',
    subject: `Invoice ${invoiceNumber || ''}`.trim(),
    body: 'Please find your invoice attached.\n\n— All Home Building Co',
    attachments: [{ type: 'invoice_pdf', invoice_id: invoiceId }],
  });
}
```

Markup (in the quote row and invoice action area respectively):

```html
<button type="button" onclick="shareQuoteEmail('{{q.id}}', PD_PROJECT_NAME, PD_CLIENT_EMAIL)">✉ Share via email</button>
<button type="button" onclick="shareInvoiceEmail(inv.id, inv.invoice_number, inv.client_email)">✉ Share via email</button>
```

(Use the client email already available in the panel's data — `inv.client_email` for invoices; for quotes, the project's `client_email`. If `openCompose` lives in `email.html` and the AHB tab is a different view, navigate to the email tab first or ensure `openCompose` is globally available; match how other cross-tab actions in this file work.)

- [ ] **Step 2: Restart + verify** — clicking "Share via email" on a quote opens the composer with the client address, the From picker, and the quote PDF attached; sending delivers the PDF. Same for an invoice. Oversized/missing refs surface a clear error.

---

## Final verification

- [ ] **Run the full new-test set:**

```bash
venv/bin/python -m pytest tests/test_payment_terms.py tests/test_quote_to_invoice.py \
  tests/test_milestone_invoices.py tests/test_invoice_schedule_render.py \
  tests/test_email_attachments.py -v
```
Expected: all PASS.

- [ ] **Regression sweep** of the related existing suites:

```bash
venv/bin/python -m pytest tests/test_baza_projects.py tests/test_ahb_api_skill.py \
  tests/test_text_utils.py tests/test_estimator_llm_errors.py -q
```
Expected: all PASS.

- [ ] **Restart dashboard** and walk the end-to-end flow in the UI: quote → create primary (deposit) → set 30/30/40 terms → deposit invoice shows schedule + amount due → record deposit → Generate next invoice (Progress) → record → Generate next (Final clears balance) → Share each via email with PDF attached.

- [ ] **Append a session-log entry** (`~/Desktop/baza-session-log.md`) summarizing what shipped, per CLAUDE.md.

---

## Self-review notes (author)

- **Spec coverage:** §1 data model → Task 1; §2 quote→invoice → Tasks 5–7; §3 terms → Tasks 2–4; §4 next-invoice + self-healing → Tasks 8–11; §5 rendering → Tasks 13–14; §6 email → Tasks 15–18. All covered.
- **Refinement:** primary = milestone 0, stamped on terms-set (Task 9) — consistent with spec §2/§4; `next-invoice` issues 1…N-1.
- **Type consistency:** `_resolve_payment_terms`→`{preset,net_days,milestones[{label,pct}]}` used identically in Tasks 3/9/13/14; `_compute_milestone_amount_due(contract,milestones,k,paid)` signature stable across Tasks 9/10; attachment ref shape `{type, invoice_id|quote_id}` identical in Tasks 16/18.
- **Known limitation (documented):** editing a term invoice's line items does not recompute frozen `amount_due`; a manual "recompute" is out of scope.
- **Open verification for implementer:** confirm the PDF route variable name for the invoice dict (Task 13) and that the PDF routes return readable bytes for `_render_*_pdf_bytes` (Task 16) — both flagged inline.
