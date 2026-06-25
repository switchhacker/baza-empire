# Payment Terms — Dollar/Percent Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let AHB123 project payment terms be defined by explicit dollar amounts (whole-schedule toggle), in addition to the existing percent mode, with no sum-to-total validation in dollar mode.

**Architecture:** Add a schedule-level `mode` field (`"percent"` default | `"amount"`) to the terms JSON stored in `ahb_projects.payment_terms`. In amount mode each milestone is `{label, amount}` and `amount_due` is the typed amount verbatim (no self-heal, no remainder). `mode` is threaded explicitly through the four backend helpers that compute/render amounts; everything defaults to `"percent"` so existing data and frozen invoice snapshots render unchanged. The UI gains a Percent/Dollar toggle that swaps the milestone-row input and relaxes the save gate.

**Tech Stack:** Flask (`dashboard/app.py`), SQLite (`ahb_projects.payment_terms` JSON, `ahb_invoices.terms_snapshot` JSON), vanilla JS in `dashboard/templates/ahb123.html`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-25-payment-terms-dollar-mode-design.md`

**Conventions:**
- Run tests with the repo venv: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest <path> -v`
- Per-task local commits are fine here (subagent-driven review needs them). Push is currently blocked by an expired PAT — that's expected; commit locally only.
- After editing `ahb123.html`, the running UI only updates after `sudo systemctl restart baza-dashboard` (Jinja caches templates at `debug=False`).

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `dashboard/app.py` | Modify `_resolve_payment_terms`, `_compute_milestone_amount_due`, `_stamp_primary_as_deposit`, `_invoice_amount_due`, `_payment_schedule_block`, next-invoice route, PUT payment-terms route | Validate + persist `mode`; compute & render dollar amounts |
| `dashboard/templates/ahb123.html` | Modify payment-terms box (~4875-4893) + JS (`pdOnTermsPreset`, `pdAddTermRow`, `pdRecalcTermSum`, `pdCollectMilestones`, `pdSaveTerms`, `pdRenderCurrentTerms`, `pdLoadTerms`) | Percent/Dollar toggle, dollar rows, info-only Σ |
| `tests/test_payment_terms.py` | Add amount-mode resolve/validate + route round-trip tests | Cover Task 1 + route |
| `tests/test_milestone_invoices.py` | Add amount-mode compute + stamp tests | Cover Task 2 + 3 |
| `tests/test_invoice_schedule_render.py` | Add dollar-schedule render test | Cover Task 4 |

---

## Task 1: `_resolve_payment_terms` — accept `mode`, validate amount milestones

**Files:**
- Modify: `dashboard/app.py:6058-6083` (`_resolve_payment_terms`)
- Test: `tests/test_payment_terms.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_payment_terms.py`:

```python
# ---- Dollar mode: _resolve_payment_terms ----

def test_amount_mode_resolves_and_keeps_amounts(app_module):
    t = app_module._resolve_payment_terms(
        "custom",
        [{"label": "Deposit", "amount": 5000},
         {"label": "Draw", "amount": 3000},
         {"label": "Balance upon completion", "amount": 4000}],
        "amount")
    assert t["mode"] == "amount"
    assert t["preset"] == "custom"
    assert [m["amount"] for m in t["milestones"]] == [5000, 3000, 4000]
    assert [m["label"] for m in t["milestones"]] == ["Deposit", "Draw", "Balance upon completion"]


def test_amount_mode_skips_sum_check(app_module):
    # amounts that would never sum to 100 are fine in amount mode
    t = app_module._resolve_payment_terms(
        "custom", [{"label": "A", "amount": 9999}], "amount")
    assert t["milestones"][0]["amount"] == 9999


def test_amount_mode_requires_label(app_module):
    with pytest.raises(ValueError):
        app_module._resolve_payment_terms("custom", [{"label": "", "amount": 100}], "amount")


def test_amount_mode_rejects_negative(app_module):
    with pytest.raises(ValueError):
        app_module._resolve_payment_terms("custom", [{"label": "A", "amount": -5}], "amount")


def test_amount_mode_rejects_non_numeric(app_module):
    with pytest.raises(ValueError):
        app_module._resolve_payment_terms("custom", [{"label": "A", "amount": "x"}], "amount")


def test_percent_mode_default_when_mode_absent(app_module):
    # back-compat: no mode arg => percent, existing behavior preserved
    t = app_module._resolve_payment_terms("50_50", None)
    assert t["mode"] == "percent"
    assert [m["pct"] for m in t["milestones"]] == [50, 50]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_payment_terms.py -v -k "amount_mode or percent_mode_default"`
Expected: FAIL — `_resolve_payment_terms()` takes 2 positional args / returns no `"mode"` key.

- [ ] **Step 3: Implement the change**

Replace the body of `_resolve_payment_terms` (`dashboard/app.py:6058-6083`) with:

```python
def _resolve_payment_terms(preset, milestones, mode=None):
    """Normalize a preset name or a custom milestone list into a validated
    terms dict: {preset, mode, net_days, milestones}. mode is "percent"
    (default) or "amount". Percent milestones are {label,pct} and must sum to
    100; amount milestones are {label,amount} (numeric >= 0, no sum check).
    Raises ValueError on a missing label, bad number, or percent sum != 100."""
    mode = (mode or "percent").strip().lower()
    if mode not in ("percent", "amount"):
        mode = "percent"
    preset = (preset or "").strip()

    if mode == "amount":
        ms = []
        for m in (milestones or []):
            label = (m.get("label") or "").strip()
            if not label:
                raise ValueError("each milestone needs a label")
            try:
                amount = float(m.get("amount") or 0)
            except (TypeError, ValueError):
                raise ValueError("milestone amount must be a number")
            if amount < 0:
                raise ValueError("milestone amount cannot be negative")
            ms.append({"label": label, "amount": amount})
        if not ms:
            raise ValueError("at least one milestone is required")
        return {"preset": "custom", "mode": "amount", "net_days": 0, "milestones": ms}

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
    return {"preset": preset, "mode": "percent", "net_days": net_days, "milestones": ms}
```

- [ ] **Step 4: Run the tests to verify they pass (incl. existing percent tests)**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_payment_terms.py -v`
Expected: PASS — all new amount-mode tests plus the existing percent/preset tests.

- [ ] **Step 5: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/app.py tests/test_payment_terms.py
git commit -m "feat(payment-terms): add amount mode to _resolve_payment_terms"
```

---

## Task 2: `_compute_milestone_amount_due` — amount-mode branch

**Files:**
- Modify: `dashboard/app.py:6095-6105` (`_compute_milestone_amount_due`)
- Test: `tests/test_milestone_invoices.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_milestone_invoices.py`:

```python
# ---- Dollar mode: _compute_milestone_amount_due ----

def test_amount_mode_returns_typed_amount(app_module):
    ms = [{"label": "Deposit", "amount": 5000},
          {"label": "Draw", "amount": 3000},
          {"label": "Balance", "amount": 4000}]
    f = app_module._compute_milestone_amount_due
    # each milestone bills its typed amount, regardless of paid or position
    assert f(99999, ms, 0, 0, "amount") == 5000
    assert f(99999, ms, 1, 5000, "amount") == 3000
    assert f(99999, ms, 2, 8000, "amount") == 4000   # final is NOT a remainder


def test_amount_mode_clamps_negative_typed_amount(app_module):
    ms = [{"label": "Deposit", "amount": -10}]
    f = app_module._compute_milestone_amount_due
    assert f(99999, ms, 0, 0, "amount") == 0


def test_percent_mode_default_unchanged(app_module):
    # default mode arg keeps existing self-healing percent behavior
    ms = [{"label": "Deposit", "pct": 30}, {"label": "Progress", "pct": 30}, {"label": "Final", "pct": 40}]
    f = app_module._compute_milestone_amount_due
    assert f(20000, ms, 2, 12000) == 8000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_milestone_invoices.py -v -k "amount_mode or percent_mode_default"`
Expected: FAIL — `_compute_milestone_amount_due()` takes 4 args, not 5.

- [ ] **Step 3: Implement the change**

Replace `_compute_milestone_amount_due` (`dashboard/app.py:6095-6105`) with:

```python
def _compute_milestone_amount_due(contract, milestones, k, paid, mode="percent"):
    """Amount due for milestone index k (0-based). In "amount" mode the typed
    dollar amount is billed verbatim (clamped to >= 0). In "percent" mode the
    figure self-heals: cumulative-% target through k minus total paid, with the
    final milestone clearing to the true remaining balance. Negatives clamp to 0."""
    if (mode or "percent") == "amount":
        try:
            return round(max(0.0, float(milestones[k].get("amount") or 0)), 2)
        except (IndexError, TypeError, ValueError):
            return 0.0
    contract = float(contract or 0); paid = float(paid or 0)
    if k >= len(milestones) - 1:                 # final milestone
        due = contract - paid
    else:
        cum_pct = sum(float(m.get("pct") or 0) for m in milestones[:k + 1]) / 100.0
        due = round(contract * cum_pct, 2) - paid
    return round(max(0.0, due), 2)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_milestone_invoices.py -v`
Expected: PASS — new amount-mode tests plus existing percent tests.

- [ ] **Step 5: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/app.py tests/test_milestone_invoices.py
git commit -m "feat(payment-terms): amount-mode branch in _compute_milestone_amount_due"
```

---

## Task 3: Thread `mode` through stamp, next-invoice, live-due, and PUT route

**Files:**
- Modify: `dashboard/app.py:6108-6127` (`_stamp_primary_as_deposit`)
- Modify: `dashboard/app.py:6174-6194` (`_invoice_amount_due`)
- Modify: `dashboard/app.py:7011-7034` (next-invoice route — `terms` parse + `_compute_milestone_amount_due` call)
- Modify: `dashboard/app.py:6208-6214` (PUT route — `_resolve_payment_terms` call)
- Test: `tests/test_milestone_invoices.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_milestone_invoices.py`:

```python
# ---- Dollar mode: primary stamped + PUT round-trip + next invoice ----

@pytest.fixture
def amount_client(app_module):
    conn = app_module._ahb_db()
    conn.execute("INSERT INTO ahb_projects (id,title,status) VALUES ('pa','Jones','Planning')")
    conn.execute("INSERT INTO ahb_invoices (id,project_id,invoice_number,subtotal,total,is_primary,line_items) "
                 "VALUES ('ia','pa','AHB-A',20000,20000,1,'[]')")
    conn.commit(); conn.close()
    return app_module.app.test_client()


def _set_amount_terms(client):
    return client.put("/api/ahb/projects/pa/payment-terms", json={
        "preset": "custom", "mode": "amount",
        "milestones": [{"label": "Deposit", "amount": 5000},
                       {"label": "Draw", "amount": 3000},
                       {"label": "Balance upon completion", "amount": 4000}]})


def test_amount_put_round_trip(amount_client):
    r = _set_amount_terms(amount_client)
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["terms"]["mode"] == "amount"
    g = amount_client.get("/api/ahb/projects/pa/payment-terms").get_json()
    assert g["terms"]["mode"] == "amount"
    assert [m["amount"] for m in g["terms"]["milestones"]] == [5000, 3000, 4000]


def test_amount_primary_stamped_with_typed_deposit(amount_client, app_module):
    _set_amount_terms(amount_client)
    conn = app_module._ahb_db()
    inv = dict(conn.execute("SELECT * FROM ahb_invoices WHERE id='ia'").fetchone())
    conn.close()
    assert inv["milestone_index"] == 0
    assert inv["milestone_label"] == "Deposit"
    assert inv["amount_due"] == 5000   # typed amount, not 20000-based


def test_amount_next_invoice_bills_typed_amount(amount_client):
    _set_amount_terms(amount_client)
    r = amount_client.post("/api/ahb/projects/pa/next-invoice")
    assert r.status_code == 200
    body = r.get_json()
    assert body["milestone_index"] == 1
    assert body["milestone_label"] == "Draw"
    assert body["amount_due"] == 3000   # typed, ignores payments
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_milestone_invoices.py -v -k amount`
Expected: FAIL — PUT drops `mode` (resolver not given it) and stamp/next-invoice use percent math, so `amount_due` is wrong (e.g. 0 or 20000-based) and round-trip lacks `mode`.

- [ ] **Step 3a: PUT route — pass `mode` to the resolver**

In `api_ahb_project_payment_terms` (`dashboard/app.py:~6210`), change:

```python
            terms = _resolve_payment_terms(d.get('preset'), d.get('milestones'))
```

to:

```python
            terms = _resolve_payment_terms(d.get('preset'), d.get('milestones'), d.get('mode'))
```

- [ ] **Step 3b: `_stamp_primary_as_deposit` — read mode from terms**

In `_stamp_primary_as_deposit` (`dashboard/app.py:~6122`), change:

```python
    due = _compute_milestone_amount_due(contract, ms, 0, paid)
```

to:

```python
    due = _compute_milestone_amount_due(contract, ms, 0, paid, (terms or {}).get("mode", "percent"))
```

- [ ] **Step 3c: next-invoice route — read mode from terms**

In `api_ahb_project_next_invoice` (`dashboard/app.py:~7011-7034`), just after `terms = json.loads(raw)` add a `mode` read, then pass it into the compute call. Change:

```python
        terms = json.loads(raw)
        milestones = terms.get('milestones') or []
```

to:

```python
        terms = json.loads(raw)
        mode = terms.get('mode', 'percent')
        milestones = terms.get('milestones') or []
```

and change:

```python
        due = _compute_milestone_amount_due(contract, milestones, next_k, paid)
```

to:

```python
        due = _compute_milestone_amount_due(contract, milestones, next_k, paid, mode)
```

- [ ] **Step 3d: `_invoice_amount_due` — read mode from frozen snapshot**

In `_invoice_amount_due` (`dashboard/app.py:6174-6194`), change:

```python
    try:
        ms = json.loads(raw).get('milestones') or []
    except Exception:
        return float(inv.get('amount_due') or 0)
    if not ms:
        return float(inv.get('amount_due') or 0)
    contract = float(inv.get('total') or inv.get('subtotal') or 0)
    return _compute_milestone_amount_due(contract, ms, idx, paid)
```

to:

```python
    try:
        snap = json.loads(raw)
        ms = snap.get('milestones') or []
    except Exception:
        return float(inv.get('amount_due') or 0)
    if not ms:
        return float(inv.get('amount_due') or 0)
    contract = float(inv.get('total') or inv.get('subtotal') or 0)
    return _compute_milestone_amount_due(contract, ms, idx, paid, snap.get('mode', 'percent'))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_milestone_invoices.py tests/test_payment_terms.py -v`
Expected: PASS — all amount tests plus every existing percent test (stamp, next-invoice, round-trip).

- [ ] **Step 5: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/app.py tests/test_milestone_invoices.py
git commit -m "feat(payment-terms): thread mode through stamp, next-invoice, live-due, PUT"
```

---

## Task 4: `_payment_schedule_block` — render dollar schedule

**Files:**
- Modify: `dashboard/app.py:6130-6171` (`_payment_schedule_block`)
- Test: `tests/test_invoice_schedule_render.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_invoice_schedule_render.py`:

```python
def test_amount_mode_renders_dollar_schedule(app_module):
    terms = {"preset": "custom", "mode": "amount",
             "milestones": [{"label": "Deposit", "amount": 5000},
                            {"label": "Draw", "amount": 3000},
                            {"label": "Balance upon completion", "amount": 4000}]}
    inv = {"milestone_index": 1, "amount_due": 3000, "total": 20000, "status": "draft",
           "terms_snapshot": json.dumps(terms)}
    html = app_module._payment_schedule_block(inv)
    assert "PAYMENT SCHEDULE" in html
    assert "Deposit" in html and "Draw" in html and "Balance upon completion" in html
    assert "5,000" in html and "3,000" in html and "4,000" in html
    assert "%" not in html            # no percent markers in dollar mode
    assert "AMOUNT DUE NOW" in html


def test_percent_snapshot_still_renders_percent(app_module):
    # back-compat: snapshot with no mode key renders as percent (existing behavior)
    terms = {"preset": "50_50", "milestones": [{"label": "Deposit", "pct": 50},
                                               {"label": "Completion", "pct": 50}]}
    inv = {"milestone_index": 1, "amount_due": 10000, "total": 20000, "status": "draft",
           "terms_snapshot": json.dumps(terms)}
    html = app_module._payment_schedule_block(inv)
    assert "(50%)" in html or "50%" in html
    assert "10,000" in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_invoice_schedule_render.py -v -k "amount_mode or percent_snapshot"`
Expected: FAIL — dollar test fails because the current renderer always emits `(pct%)` and computes `contract*pct/100`, so it shows `0.00` and a `%` sign.

- [ ] **Step 3: Implement the change**

Replace `_payment_schedule_block` (`dashboard/app.py:6130-6171`) with:

```python
def _payment_schedule_block(inv):
    """HTML block for a term-driven invoice's payment schedule, or '' for a
    plain invoice. Self-contained (own money formatter) so it is unit-testable
    and reusable in both the PDF and any HTML view. Renders dollar rows when the
    frozen snapshot's mode is "amount", percent rows otherwise (back-compat)."""
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
    mode = terms.get("mode", "percent")
    contract = float(inv.get("total") or 0)
    due = float(inv.get("amount_due") or 0)

    def m(x):
        return ("-$" if x < 0 else "$") + f"{abs(x):,.2f}"

    rows = ""
    for k, mil in enumerate(ms):
        marker = " &larr; this invoice" if k == idx else ""
        weight = "700" if k == idx else "400"
        if mode == "amount":
            amt = float(mil.get("amount") or 0)
            desc = f'{mil.get("label","")}{marker}'
        else:
            amt = round(contract * float(mil.get("pct") or 0) / 100.0, 2)
            desc = f'{mil.get("label","")} ({mil.get("pct",0)}%){marker}'
        rows += (f'<div style="display:flex;justify-content:space-between;font-weight:{weight};">'
                 f'<span>{desc}</span>'
                 f'<span>{m(amt)}</span></div>')
    if mode == "amount":
        header = "$"
    else:
        header = (terms.get("preset") or "").replace("_", " / ")
    status = (inv.get("status") or "draft").upper()
    return (
        '<div style="margin-top:18px;border-top:1px dashed #94a3b8;padding-top:10px;width:320px;font-size:12px;">'
        f'<div style="display:flex;justify-content:space-between;font-weight:700;color:#334155;">'
        f'<span>PAYMENT SCHEDULE ({header})</span><span>Status: {status}</span></div>'
        f'{rows}'
        '<div style="display:flex;justify-content:space-between;border-top:1px solid #333;'
        f'margin-top:6px;padding-top:6px;font-weight:700;color:#2563eb;">'
        f'<span>AMOUNT DUE NOW</span><span>{m(due)}</span></div></div>')
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_invoice_schedule_render.py -v`
Expected: PASS — dollar render + percent back-compat + the original two tests.

- [ ] **Step 5: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/app.py tests/test_invoice_schedule_render.py
git commit -m "feat(payment-terms): render dollar-mode payment schedule"
```

---

## Task 5: Frontend — Percent/Dollar toggle in the project modal

**Files:**
- Modify: `dashboard/templates/ahb123.html:4875-4893` (payment-terms box markup)
- Modify: `dashboard/templates/ahb123.html:15942-16006` (the `pd*` term functions)

This task has no Python unit test (the codebase has no JS test runner); it ends with a manual UI verification step.

- [ ] **Step 1: Add the mode toggle to the payment-terms box markup**

In `dashboard/templates/ahb123.html`, replace the block at lines 4875-4893 with:

```html
        <div id="pd-terms-box" style="margin:4px 0 10px;font-size:12px;">
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
          <span id="pd-terms-mode-wrap" style="display:none;margin-left:8px;">
            <label style="color:#94a3b8;">as
              <select id="pd-terms-mode" onchange="pdOnTermsMode()" style="margin-left:4px;">
                <option value="percent">Percent %</option>
                <option value="amount">Dollar $</option>
              </select>
            </label>
          </span>
          <div id="pd-terms-custom" style="display:none;margin-top:6px;">
            <div id="pd-terms-rows"></div>
            <button type="button" class="btn btn-sm btn-secondary" onclick="pdAddTermRow()">+ milestone</button>
            <span id="pd-terms-sum" style="margin-left:8px;"></span>
          </div>
          <button type="button" id="pd-terms-save" class="btn btn-sm btn-primary" style="margin-top:6px;" onclick="pdSaveTerms()">Save terms</button>
          <span id="pd-terms-current" style="margin-left:8px;color:#22c55e;"></span>
        </div>
```

- [ ] **Step 2: Replace the `pd*` term JS functions**

In `dashboard/templates/ahb123.html`, replace the functions at lines 15942-16006 (`pdOnTermsPreset` through `pdLoadTerms`, inclusive) with:

```javascript
function pdTermsMode(){
  const el=document.getElementById('pd-terms-mode');
  return el ? el.value : 'percent';
}
function pdOnTermsPreset(){
  const v=document.getElementById('pd-terms-preset').value;
  const isCustom=(v==='custom');
  document.getElementById('pd-terms-custom').style.display=isCustom?'block':'none';
  // The %/$ toggle only makes sense for custom schedules (presets are percent).
  document.getElementById('pd-terms-mode-wrap').style.display=isCustom?'inline':'none';
  if(!isCustom){ document.getElementById('pd-terms-mode').value='percent'; }
  if(isCustom && !document.querySelectorAll('#pd-terms-rows .pd-term-row').length){
    if(pdTermsMode()==='amount'){ pdAddTermRow('Deposit',0); pdAddTermRow('Balance upon completion',0); }
    else { pdAddTermRow('Deposit',50); pdAddTermRow('Completion',50); }
  }
  pdRecalcTermSum();
}
function pdOnTermsMode(){
  // Switching unit re-labels existing rows' input; values reset to 0 to avoid
  // a 50 "%" silently becoming $50.
  const rows=[...document.querySelectorAll('#pd-terms-rows .pd-term-row')]
    .map(r=>r.querySelector('.pd-term-label').value);
  document.getElementById('pd-terms-rows').innerHTML='';
  if(!rows.length){
    if(pdTermsMode()==='amount'){ pdAddTermRow('Deposit',0); pdAddTermRow('Balance upon completion',0); }
    else { pdAddTermRow('Deposit',50); pdAddTermRow('Completion',50); }
  } else {
    rows.forEach(lbl=>pdAddTermRow(lbl,0));
  }
  pdRecalcTermSum();
}
function pdAddTermRow(label,val){
  const amount=(pdTermsMode()==='amount');
  const row=document.createElement('div');
  row.className='pd-term-row'; row.style.margin='3px 0';
  const field = amount
    ? '$ <input class="pd-term-val pd-term-amount" type="number" min="0" step="0.01" value="'+(val||0)+'" style="width:90px;" oninput="pdRecalcTermSum()">'
    : '<input class="pd-term-val pd-term-pct" type="number" min="0" max="100" value="'+(val||0)+'" style="width:60px;" oninput="pdRecalcTermSum()">%';
  row.innerHTML='<input class="pd-term-label" placeholder="Stage" value="'+(label||'')+'" style="width:160px;"> '
    +field
    +' <button type="button" class="btn btn-sm btn-secondary" onclick="this.parentNode.remove();pdRecalcTermSum()">✕</button>';
  document.getElementById('pd-terms-rows').appendChild(row);
  pdRecalcTermSum();
}
function pdRecalcTermSum(){
  const el=document.getElementById('pd-terms-sum');
  const isCustom=document.getElementById('pd-terms-preset').value==='custom';
  let s=0;
  document.querySelectorAll('#pd-terms-rows .pd-term-val').forEach(i=>s+=parseFloat(i.value||0));
  if(pdTermsMode()==='amount'){
    // Σ is informational only in dollar mode — never blocks Save.
    el.textContent='Σ = $'+s.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
    el.style.color='#94a3b8';
    document.getElementById('pd-terms-save').disabled=false;
  } else {
    const ok=Math.abs(s-100)<0.01;
    el.textContent='sum = '+s+'%'+(ok?' ✓':' ✗'); el.style.color=ok?'#22c55e':'#ef4444';
    document.getElementById('pd-terms-save').disabled=isCustom && !ok;
  }
}
function pdCollectMilestones(){
  const amount=(pdTermsMode()==='amount');
  return [...document.querySelectorAll('#pd-terms-rows .pd-term-row')].map(r=>{
    const o={label:r.querySelector('.pd-term-label').value.trim()};
    if(amount) o.amount=parseFloat(r.querySelector('.pd-term-amount').value||0);
    else o.pct=parseFloat(r.querySelector('.pd-term-pct').value||0);
    return o;
  });
}
async function pdSaveTerms(){
  const pid=document.getElementById('pd-id').value;
  const preset=document.getElementById('pd-terms-preset').value;
  const payload={preset};
  if(preset==='custom'){ payload.mode=pdTermsMode(); payload.milestones=pdCollectMilestones(); }
  const r=await fetch('/api/ahb/projects/'+pid+'/payment-terms',
    {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const j=await r.json();
  if(!j.success){showToast(j.error||'Failed to save terms','error');return;}
  pdRenderCurrentTerms(j.terms);
  showToast('Payment terms saved');
  try{ await loadInvoices(); }catch(e){}
}
function pdRenderCurrentTerms(terms){
  const el=document.getElementById('pd-terms-current');
  if(!el)return;
  if(!terms||!terms.milestones||!terms.milestones.length){el.textContent='';return;}
  if(terms.mode==='amount'){
    el.textContent=terms.milestones.map(m=>'$'+(m.amount||0).toLocaleString()+' '+m.label).join(' · ');
  } else {
    el.textContent=terms.milestones.map(m=>m.pct+'% '+m.label).join(' · ');
  }
}
async function pdLoadTerms(pid){
  try{
    const j=await fetch('/api/ahb/projects/'+pid+'/payment-terms').then(r=>r.json());
    if(j.success && j.terms){
      const sel=document.getElementById('pd-terms-preset');
      const modeSel=document.getElementById('pd-terms-mode');
      if(modeSel) modeSel.value=j.terms.mode||'percent';
      if(sel){ sel.value=j.terms.preset||''; pdOnTermsPreset(); }
      if(j.terms.preset==='custom'){
        if(modeSel) modeSel.value=j.terms.mode||'percent';
        document.getElementById('pd-terms-mode-wrap').style.display='inline';
        document.getElementById('pd-terms-rows').innerHTML='';
        (j.terms.milestones||[]).forEach(m=>pdAddTermRow(m.label,
          (j.terms.mode==='amount')?(m.amount||0):(m.pct||0)));
        pdRecalcTermSum();
      }
      pdRenderCurrentTerms(j.terms);
    }
  }catch(e){}
}
```

- [ ] **Step 3: Restart the dashboard so the template reloads**

Run: `sudo systemctl restart baza-dashboard`
Expected: command returns cleanly; `systemctl is-active baza-dashboard` prints `active`.

- [ ] **Step 4: Manual UI verification**

Open the dashboard (`:8888` AHB123 tab), open any project's detail modal, and confirm:
1. Payment terms → select **Custom…**: an "as Percent % / Dollar $" toggle appears next to the preset dropdown.
2. With **Percent %**: rows show a `%` field, Σ shows `sum = …% ✓/✗`, Save disabled until 100%. (Unchanged behavior.)
3. Switch to **Dollar $**: rows show a `$` field, Σ shows `Σ = $…` in grey, Save always enabled.
4. Enter Deposit=$5000, Draw=$3000, Balance upon completion=$4000 → Save terms → toast "Payment terms saved"; the green current-terms line reads `$5,000 Deposit · $3,000 Draw · $4,000 Balance upon completion`.
5. Close & reopen the modal → terms reload in Dollar mode with the same rows.
6. Generate the next milestone invoice → the schedule block shows dollar rows and "AMOUNT DUE NOW $3,000".

- [ ] **Step 5: Commit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/templates/ahb123.html
git commit -m "feat(payment-terms): percent/dollar toggle in project modal UI"
```

---

## Task 6: Full regression + session log

**Files:**
- Append: `~/Desktop/baza-session-log.md`

- [ ] **Step 1: Run the full payment-terms test surface**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest tests/test_payment_terms.py tests/test_milestone_invoices.py tests/test_invoice_schedule_render.py -v`
Expected: PASS — every test green.

- [ ] **Step 2: Run the broader suite to check for collateral breakage**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest -q 2>&1 | tail -20`
Expected: No NEW failures. (`test_split_detection` is a known pre-existing unrelated failure — see session log 2026-06-25 17:50.) If any other test newly fails, stop and investigate before claiming done.

- [ ] **Step 3: Append a session-log entry**

Get the timestamp with `date '+%Y-%m-%d %H:%M'` and append a `### <ts> | Payment terms dollar mode shipped` entry summarizing: files touched, mode default = percent (zero behavior change to existing projects), test counts, and that the dashboard was restarted.

---

## Self-Review Notes

- **Spec coverage:** data model `mode` (T1), free-amounts-no-check (T1 skips sum; T2 returns typed amount), Σ info-only (T5 step 2 `pdRecalcTermSum`), mode-switchable with snapshot protection (T3 freezes `mode` into `terms_snapshot`; T4 reads it back; old snapshots default to percent), UI toggle + presets-only-in-percent (T5). All covered.
- **Type/signature consistency:** `_resolve_payment_terms(preset, milestones, mode=None)`, `_compute_milestone_amount_due(contract, milestones, k, paid, mode="percent")` — used identically in every call site (T2 def, T3 call sites). JS: `.pd-term-val` class is on both pct and amount inputs so `pdRecalcTermSum` reads either; `.pd-term-amount`/`.pd-term-pct` distinguish for collection.
- **Back-compat:** every snapshot/terms read defaults missing `mode` to `"percent"`; `_resolve_payment_terms` called with 2 args still works (mode defaults). Existing percent tests are re-run in Tasks 1-4 step 4.
