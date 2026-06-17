# Project-Modal Estimator Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Method 5 (Unit-Cost DB) and the itemized material-per-line picker to the ahb123 project-detail modal estimator, at parity with the standalone EstimatOR tool.

**Architecture:** Frontend-only. Follow the existing `pd`-prefixed parallel-copy convention already used for Methods 1–4 in the project modal (`dashboard/templates/ahb123.html`). Reuse the existing backend endpoints (`/api/ahb/estimator/method5`, `/materials`, `/material-suggest`) and the shared catalog-manager modal unchanged.

**Tech Stack:** Flask + Jinja (single big template `ahb123.html`), vanilla JS, SQLite (`baza_projects.db`). Tests via pytest + Flask test client (mirrors `dashboard/tests/test_mobile_pwa.py`).

**Conventions for this repo:**
- After editing `ahb123.html`, restart the service: `sudo systemctl restart baza-dashboard` (Jinja caches templates under `debug=False`).
- Do **not** `git commit` — `claw-auto-git` hourly-commits `agent-framework-v3`. "Commit" steps below mean `git add` (stage only).
- Run tests with the repo venv: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest dashboard/tests/test_project_modal_estimator.py -v`.

---

## File Structure

- **Modify:** `dashboard/templates/ahb123.html`
  - Project-modal method-picker grid + add 5th button (`~4613`, `~4629`).
  - Add `pd-est-pane-5` (Method 5 UI) after `pd-est-pane-4` (`~4770`).
  - Replace project-modal M4 Material Cost block with Lump/Itemized tabs + picker (`~4664–4671`).
  - `pdSwitchEstMethod()` extend to method 5 (`~10685`).
  - New JS: `pdM5Init`, `pdM5UpdateUnitHint`, materials-picker `pd*` functions (add near existing `pdM4*` block, `~10691–10878`).
  - `recalcPdM4()` materials source (`~10851`).
  - `pdRunMethod()` add a method-5 branch (`~11169`).
- **Create:** `dashboard/tests/test_project_modal_estimator.py`
- **No backend / schema changes.**

---

## Task 1: Method 5 (Unit-Cost DB) in the project modal

**Files:**
- Create: `dashboard/tests/test_project_modal_estimator.py`
- Modify: `dashboard/templates/ahb123.html` (`~4613`, `~4629`, `~4770`, `~10685`, new JS, `~11169`)

- [ ] **Step 1: Write the failing test**

Create `dashboard/tests/test_project_modal_estimator.py`:

```python
"""Tests for bringing Method 5 (unit-cost DB) and the itemized materials
picker into the ahb123 project-detail modal estimator.

Mirrors dashboard/tests/test_mobile_pwa.py: import the real app.py and use its
Flask test client (the shared conftest `app` fixture only wires the email
blueprint, so it can't see these routes).
"""
import os
import sys

import pytest

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT_DIR = os.path.dirname(DASHBOARD_DIR)
for _p in (DASHBOARD_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as appmod


@pytest.fixture
def client():
    with appmod.app.test_client() as c:
        yield c


# ── Method 5 in the project modal ──────────────────────────────────────────
def test_method5_pane_present_in_project_modal(client):
    html = client.get("/ahb123").get_data(as_text=True)
    assert "pd-est-pane-5" in html
    assert 'onclick="pdRunMethod(5)"' in html
    assert 'data-m="5"' in html
    assert "repeat(5,1fr)" in html       # method-picker grid widened to 5
    assert "pd-m5-scope" in html         # modal has its own cost-book scope picker


def test_method5_endpoint_modal_payload(client):
    # Robust against seed contents: discover an existing cost-book scope first.
    book = client.get("/api/ahb/estimator/costbook").get_json()
    assert isinstance(book, list) and book, "cost book should be seeded"
    scope = book[0]["scope"]
    res = client.post(
        "/api/ahb/estimator/method5",
        json={"scope": scope, "qty": 200, "tier": "mid", "multiplier": 1.0},
    )
    assert res.status_code == 200
    d = res.get_json()
    assert d["success"] is True
    assert {"low", "mid", "high"}.issubset(d["totals"].keys())
    assert d["selected_total"] == d["totals"]["mid"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest dashboard/tests/test_project_modal_estimator.py::test_method5_pane_present_in_project_modal -v`
Expected: FAIL — `pd-est-pane-5` / `data-m="5"` / `repeat(5,1fr)` not in HTML. (`test_method5_endpoint_modal_payload` should already PASS — the backend exists.)

- [ ] **Step 3a: Widen the method-picker grid and add the 5th button**

In `dashboard/templates/ahb123.html`, the project-modal method picker (`~4613`):

Change:
```html
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px">
```
to:
```html
          <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:12px">
```

Then immediately after the Method 4 button block (the `</button>` at `~4629`, the one whose label is `Custom Pricing`), add:
```html
            <button class="est-method-btn pd-est-method" data-m="5" onclick="pdSwitchEstMethod(5)" style="padding:10px">
              <div style="font-size:18px">📐</div>
              <div style="font-size:11px;font-weight:800;margin-top:4px">Unit Cost</div>
            </button>
```

- [ ] **Step 3b: Add the Method 5 pane**

In `ahb123.html`, immediately AFTER the closing `</div>` of `pd-est-pane-4` (the line just before `<div id="pd-est-results" ...>` at `~4771`), insert:
```html
          <!-- Method 5: Unit Cost (cost book) -->
          <div class="pd-est-pane" id="pd-est-pane-5" style="display:none">
            <div style="background:#0a1a2a;border:1px solid #1a3a5a;border-radius:6px;padding:9px;margin-bottom:10px;font-size:10px;color:#aaa">📐 Qty × cost-book rate. Pick a scope that has a cost-book entry, enter quantity, quality tier and site condition — instant low/mid/high range, no LLM.</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
              <div>
                <label style="font-size:10px;color:#666">Scope (cost book)</label>
                <select id="pd-m5-scope" onchange="pdM5UpdateUnitHint()" style="width:100%;background:#0a0a16;border:1px solid #2a2a4a;border-radius:5px;padding:7px;color:#e0e0e0;font-size:12px">
                  <option value="kitchen">Kitchen Remodel</option>
                  <option value="bathroom">Bathroom Remodel</option>
                  <option value="addition">Addition</option>
                  <option value="basement">Basement Finish</option>
                  <option value="deck">Deck Build</option>
                  <option value="full-reno">Full Renovation</option>
                  <option value="roofing">Roofing</option>
                  <option value="flooring">Flooring</option>
                  <option value="painting">Painting</option>
                  <option value="other">Other</option>
                </select>
              </div>
              <div>
                <label style="font-size:10px;color:#666">Quantity <span id="pd-m5-unit-hint" style="color:#4da6ff">(sqft)</span></label>
                <input id="pd-m5-qty" type="number" min="0" step="1" placeholder="200" style="width:100%;background:#0a0a16;border:1px solid #2a2a4a;border-radius:5px;padding:7px;color:#e0e0e0;font-size:12px;font-family:monospace">
              </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
              <div>
                <label style="font-size:10px;color:#666">Quality Tier</label>
                <select id="pd-m5-tier" style="width:100%;background:#0a0a16;border:1px solid #2a2a4a;border-radius:5px;padding:7px;color:#e0e0e0;font-size:12px">
                  <option value="low">Economy</option>
                  <option value="mid" selected>Standard</option>
                  <option value="high">Premium</option>
                </select>
              </div>
              <div>
                <label style="font-size:10px;color:#666">Site Condition</label>
                <select id="pd-m5-mult" style="width:100%;background:#0a0a16;border:1px solid #2a2a4a;border-radius:5px;padding:7px;color:#e0e0e0;font-size:12px">
                  <option value="0.9">Easy (×0.9)</option>
                  <option value="1.0" selected>Normal (×1.0)</option>
                  <option value="1.15">Tight (×1.15)</option>
                  <option value="1.3">Difficult (×1.3)</option>
                </select>
              </div>
            </div>
            <button class="btn btn-sm btn-primary" style="width:100%" onclick="pdRunMethod(5)">📐 Calculate Unit-Cost Estimate</button>
          </div>
```

- [ ] **Step 3c: Extend `pdSwitchEstMethod` to method 5**

Replace the function at `~10685`:
```javascript
function pdSwitchEstMethod(m){
  document.querySelectorAll('.pd-est-method').forEach(b=>b.classList.toggle('active', String(b.dataset.m)===String(m)));
  [1,2,3,4].forEach(i=>{const el=document.getElementById('pd-est-pane-'+i); if(el) el.style.display=(i===m?'block':'none');});
  if(m===4){ pdM4Init(); }
}
```
with:
```javascript
function pdSwitchEstMethod(m){
  document.querySelectorAll('.pd-est-method').forEach(b=>b.classList.toggle('active', String(b.dataset.m)===String(m)));
  [1,2,3,4,5].forEach(i=>{const el=document.getElementById('pd-est-pane-'+i); if(el) el.style.display=(i===m?'block':'none');});
  if(m===4){ pdM4Init(); }
  if(m===5){ pdM5Init(); }
}
```

- [ ] **Step 3d: Add `pdM5Init` + `pdM5UpdateUnitHint`**

Insert these new functions immediately after `pdSwitchEstMethod` (after its closing `}` at `~10689`). They reuse the existing global `costBook` and `loadCostBook()`:
```javascript
/* ===== Project-detail Method 5: Unit Cost (cost book) ===== */
let pdM5InitDone=false;
function pdM5Init(){
  // On first open, try to preselect the scope from the project's free-text scope.
  if(!pdM5InitDone){
    const sc=((document.getElementById('pd-quote-scope')||{}).value||'').toLowerCase();
    const sel=document.getElementById('pd-m5-scope');
    if(sel && sc){
      for(const opt of sel.options){ if(opt.value!=='other' && sc.includes(opt.value)){ sel.value=opt.value; break; } }
    }
    pdM5InitDone=true;
  }
  loadCostBook().then(pdM5UpdateUnitHint);
}
function pdM5UpdateUnitHint(){
  const scope=(document.getElementById('pd-m5-scope')||{}).value||'';
  const row=(costBook||[]).find(c=>c.scope===scope);
  const hint=document.getElementById('pd-m5-unit-hint');
  if(hint) hint.textContent='('+(row?(row.unit||'sqft'):'sqft — no cost-book entry yet')+')';
}
```

- [ ] **Step 3e: Add the Method 5 branch to `pdRunMethod`**

In `pdRunMethod(m)`, insert this block immediately after the Method 4 block's `return;` and its closing `}` (`~11169`), BEFORE the line `out.innerHTML='<div style="text-align:center;padding:20px;color:#666">'...`:
```javascript
  // Method 5 (Unit-Cost DB) — cost-book lookup; renders range + save buttons.
  if(m===5){
    const scope=(document.getElementById('pd-m5-scope')||{}).value||'';
    const qty=parseFloat(document.getElementById('pd-m5-qty').value)||0;
    const tier=document.getElementById('pd-m5-tier').value;
    const mult=parseFloat(document.getElementById('pd-m5-mult').value)||1;
    if(!qty){showToast('Enter a quantity','error');return;}
    out.innerHTML='<div style="text-align:center;padding:20px;color:#666">Calculating…</div>';
    try{
      const r=await fetch('/api/ahb/estimator/method5',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope,qty,tier,multiplier:mult})});
      const d=await r.json();
      if(d.success===false){out.innerHTML='<div style="color:#ff6b6b;padding:12px">'+escHtml(d.error||'Failed')+'</div>';return;}
      const t=d.totals;
      const total=d.selected_total;
      const breakdown={low:t.low, mid:t.mid, high:t.high, qty:d.qty, unit_rate:d.rates[tier]*mult, multiplier:mult, total};
      const card=(lbl,val,sel,color)=>`<div style="background:${sel?'#0a1a2a':'#070712'};border:${sel?'2px solid #4da6ff':'1px solid #1a1a2e'};border-radius:8px;padding:10px;text-align:center">
          <div style="font-size:9px;color:${color};font-weight:800;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">${lbl}${sel?' ⭐':''}</div>
          <div style="font-size:${sel?'19':'15'}px;font-weight:800;color:${color}">${fmtCurrency(val)}</div>
        </div>`;
      out.innerHTML=`<div style="background:#0a0a16;border:1px solid #00d084;border-radius:10px;padding:14px">
        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">📐 ${escHtml(d.label||scope)} · ${d.qty} ${d.unit} · ×${mult} condition</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px">
          ${card('Economy',t.low,tier==='low','#00d084')}
          ${card('Standard',t.mid,tier==='mid','#4da6ff')}
          ${card('Premium',t.high,tier==='high','#f5a623')}
        </div>
        <div style="font-size:10px;color:#666;margin-bottom:10px">Cost-book rates: ${fmtCurrency(d.rates.low)} / ${fmtCurrency(d.rates.mid)} / ${fmtCurrency(d.rates.high)} per ${d.unit}${d.notes?' · '+escHtml(d.notes):''}</div>
        <div style="display:flex;gap:6px">
          <button class="btn btn-sm btn-primary" style="flex:1" onclick="pdSaveQuote(${total},'unit_cost',true)">💾 Save as ACTIVE Quote</button>
          <button class="btn btn-sm btn-secondary" onclick="pdSaveQuote(${total},'unit_cost',false)">Save (not active)</button>
        </div>
      </div>`;
      window._pdLastQuote={total,method:'unit_cost',breakdown,scope:qScope,description:qDesc};
    }catch(e){
      out.innerHTML='<div style="color:#ff6b6b;padding:12px">Error: '+escHtml(e.message)+'</div>';
    }
    return;
  }
```

(Note: `qScope`/`qDesc`/`out` are already defined at the top of `pdRunMethod`; `pdSaveQuote` reads `window._pdLastQuote` for scope/description/breakdown.)

- [ ] **Step 4: Restart the dashboard and run the tests**

Run:
```bash
sudo systemctl restart baza-dashboard
cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest dashboard/tests/test_project_modal_estimator.py -k method5 -v
```
Expected: both `test_method5_*` PASS. (The Flask test client imports `app.py` directly, so the restart isn't needed for tests — it's needed for the live browser UI.)

- [ ] **Step 5: Stage (no commit — claw-auto-git owns this tree)**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/templates/ahb123.html dashboard/tests/test_project_modal_estimator.py
```

---

## Task 2: Itemized material-per-line picker in project-modal Method 4

**Files:**
- Modify: `dashboard/templates/ahb123.html` (M4 Material block `~4664–4671`; new `pd*` materials JS; `recalcPdM4` `~10851`)
- Modify: `dashboard/tests/test_project_modal_estimator.py` (add tests)

- [ ] **Step 1: Write the failing test**

Append to `dashboard/tests/test_project_modal_estimator.py`:
```python
# ── Itemized materials picker in the project modal ──────────────────────────
def test_itemized_materials_picker_present_in_project_modal(client):
    html = client.get("/ahb123").get_data(as_text=True)
    assert "pdM4MatMode" in html                       # Lump/Itemized tab handler
    assert "pd-m4-mat-pick" in html                    # type-ahead product input
    assert "pd-m4-mat-tbody" in html                   # line-items table body
    assert "pdM4AddMaterialFromPicker" in html         # + Add handler
    assert 'data-pdmat="items"' in html                # Itemized tab button


def test_material_suggest_endpoint(client):
    res = client.get("/api/ahb/estimator/material-suggest?vendor=&q=")
    assert res.status_code == 200
    assert isinstance(res.get_json(), list)


def test_materials_catalog_list(client):
    res = client.get("/api/ahb/estimator/materials")
    assert res.status_code == 200
    assert isinstance(res.get_json(), list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest dashboard/tests/test_project_modal_estimator.py::test_itemized_materials_picker_present_in_project_modal -v`
Expected: FAIL — `pdM4MatMode` / `pd-m4-mat-pick` / `pd-m4-mat-tbody` not in HTML. (`test_material_suggest_endpoint` and `test_materials_catalog_list` should already PASS.)

- [ ] **Step 3a: Replace the project-modal M4 Material Cost block**

In `ahb123.html`, find the Method-4 Materials block (`~4664–4671`):
```html
            <!-- 1. Materials -->
            <div style="background:#0a0a16;border:1px solid #1a1a2e;border-radius:6px;padding:10px;margin-bottom:8px">
              <div style="display:flex;align-items:center;gap:8px;font-size:11px;font-weight:700;color:#ccc;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">
                <span style="width:18px;height:18px;border-radius:50%;background:linear-gradient(135deg,#e94560,#7c3aed);color:#fff;display:inline-flex;align-items:center;justify-content:center;font-size:10px">1</span>
                Material Cost <span style="margin-left:auto;color:#00d084;font-family:monospace" id="pd-m4-mat-total">$0.00</span>
              </div>
              <input id="pd-m4-mat-cost" type="number" min="0" step="0.01" value="0" placeholder="Materials $" style="width:100%;background:#0a0a16;border:1px solid #2a2a4a;border-radius:5px;padding:7px;color:#e0e0e0;font-size:12px;font-family:monospace" oninput="recalcPdM4()">
            </div>
```
Replace the WHOLE block above with:
```html
            <!-- 1. Materials -->
            <div style="background:#0a0a16;border:1px solid #1a1a2e;border-radius:6px;padding:10px;margin-bottom:8px">
              <div style="display:flex;align-items:center;gap:8px;font-size:11px;font-weight:700;color:#ccc;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">
                <span style="width:18px;height:18px;border-radius:50%;background:linear-gradient(135deg,#e94560,#7c3aed);color:#fff;display:inline-flex;align-items:center;justify-content:center;font-size:10px">1</span>
                Material Cost <span style="margin-left:auto;color:#00d084;font-family:monospace" id="pd-m4-mat-total">$0.00</span>
              </div>
              <div style="display:flex;gap:6px;margin-bottom:6px">
                <button type="button" class="m4-tab active" data-pdmat="lump" onclick="pdM4MatMode('lump')">✎ Lump Sum</button>
                <button type="button" class="m4-tab" data-pdmat="items" onclick="pdM4MatMode('items')">📋 Itemized</button>
              </div>
              <div id="pd-m4-mat-lump-row">
                <input id="pd-m4-mat-cost" type="number" min="0" step="0.01" value="0" placeholder="Materials $" style="width:100%;background:#0a0a16;border:1px solid #2a2a4a;border-radius:5px;padding:7px;color:#e0e0e0;font-size:12px;font-family:monospace" oninput="recalcPdM4()">
              </div>
              <div id="pd-m4-mat-items-row" style="display:none">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:5px">
                  <select id="pd-m4-mat-vendor" onchange="pdOnMatVendorChange()" style="background:#0a0a16;border:1px solid #2a2a4a;border-radius:5px;padding:6px;color:#e0e0e0;font-size:11px"><option value="">All vendors</option></select>
                  <select id="pd-m4-mat-category" onchange="pdOnMatCategoryChange()" style="background:#0a0a16;border:1px solid #2a2a4a;border-radius:5px;padding:6px;color:#e0e0e0;font-size:11px"><option value="">All categories</option></select>
                </div>
                <div style="display:grid;grid-template-columns:1fr 64px 52px 46px;gap:5px;margin-bottom:5px;align-items:end">
                  <div>
                    <input id="pd-m4-mat-pick" list="pd-m4-mat-datalist" placeholder="Search catalog ★ / receipts 🧾" oninput="pdM4MatPickChanged()" style="width:100%;background:#0a0a16;border:1px solid #2a2a4a;border-radius:5px;padding:6px;color:#e0e0e0;font-size:11px">
                    <datalist id="pd-m4-mat-datalist"></datalist>
                  </div>
                  <input id="pd-m4-mat-pick-price" type="number" min="0" step="0.01" placeholder="Unit $" style="background:#0a0a16;border:1px solid #2a2a4a;border-radius:5px;padding:6px;color:#e0e0e0;font-size:11px;font-family:monospace">
                  <input id="pd-m4-mat-pick-qty" type="number" min="0" step="0.5" value="1" placeholder="Qty" style="background:#0a0a16;border:1px solid #2a2a4a;border-radius:5px;padding:6px;color:#e0e0e0;font-size:11px;font-family:monospace">
                  <button type="button" class="btn btn-sm btn-primary" style="padding:6px" onclick="pdM4AddMaterialFromPicker()">+ Add</button>
                </div>
                <div style="display:flex;justify-content:space-between;gap:6px;margin-bottom:5px">
                  <button type="button" class="btn btn-sm btn-secondary" style="font-size:10px;padding:4px 8px" onclick="pdM4SaveMatPickToCatalog()">📌 Save to catalog</button>
                  <button type="button" class="btn btn-sm btn-secondary" style="font-size:10px;padding:4px 8px" onclick="openMaterialsCatalogModal()">🧱 Manage Catalog</button>
                </div>
                <table id="pd-m4-mat-table" class="line-items-table" style="margin-bottom:0;font-size:11px">
                  <thead><tr><th style="width:48%">Material</th><th style="width:16%">Qty</th><th style="width:20%">Unit $</th><th style="width:13%">Total</th><th style="width:3%"></th></tr></thead>
                  <tbody id="pd-m4-mat-tbody"><tr id="pd-m4-mat-empty"><td colspan="5" style="text-align:center;color:#444;padding:10px;font-size:10px">No materials yet — search above and click + Add.</td></tr></tbody>
                </table>
              </div>
            </div>
```

- [ ] **Step 3b: Add the `pd`-prefixed materials-picker JS**

Insert this block immediately BEFORE `function recalcPdM4Labor()` (`~10832`). It reuses the existing globals `MAT_CATALOG`, `DEFAULT_MAT_VENDORS`, `escHtml`, `fmtCurrency`, `showToast`, and the shared `openMaterialsCatalogModal()`:
```javascript
/* ===== Project-detail Method 4: itemized materials picker ===== */
let pdM4MatModeVal='lump';   // 'lump' | 'items'
let pdM4MatRows=[];          // [{name,qty,rate}]
let pdM4MatPickIndex={};     // datalist label -> {name,price,vendor,unit,category,source,id}
let pdM4MatSuggestTimer=null;
let pdMatLoaded=false;

async function pdEnsureMaterials(){
  if(!pdMatLoaded || !MAT_CATALOG.length){
    try{ const rows=await fetch('/api/ahb/estimator/materials').then(r=>r.json()); if(Array.isArray(rows)) MAT_CATALOG=rows; }catch(e){}
    pdMatLoaded=true;
  }
  pdPopulateMatVendors(); pdPopulateMatCategories(); pdBuildMatDatalist([]);
}
function pdPopulateMatVendors(){
  const sel=document.getElementById('pd-m4-mat-vendor'); if(!sel) return;
  const cur=sel.value; const set=new Set(DEFAULT_MAT_VENDORS);
  MAT_CATALOG.forEach(r=>{ if(r.vendor) set.add(r.vendor); });
  sel.innerHTML='<option value="">All vendors</option>'+[...set].sort().map(v=>`<option>${escHtml(v)}</option>`).join('');
  sel.value=cur;
}
function pdPopulateMatCategories(){
  const sel=document.getElementById('pd-m4-mat-category'); if(!sel) return;
  const cur=sel.value; const set=new Set();
  MAT_CATALOG.forEach(r=>{ if(r.category) set.add(r.category); });
  sel.innerHTML='<option value="">All categories</option>'+[...set].sort().map(c=>`<option>${escHtml(c)}</option>`).join('');
  sel.value=cur;
}
function pdBuildMatDatalist(receiptSugs){
  const dl=document.getElementById('pd-m4-mat-datalist'); if(!dl) return;
  const vendor=(document.getElementById('pd-m4-mat-vendor')||{}).value||'';
  const category=(document.getElementById('pd-m4-mat-category')||{}).value||'';
  pdM4MatPickIndex={}; const opts=[];
  MAT_CATALOG.filter(r=>(!vendor||r.vendor===vendor)&&(!category||r.category===category)).forEach(r=>{
    const tag=r.category?` [${r.category}]`:'';
    const label='★ '+r.name+(r.vendor?` — ${r.vendor}`:'')+tag+`  ($${(r.unit_price||0)})`;
    pdM4MatPickIndex[label]={name:r.name, price:r.unit_price||0, vendor:r.vendor||'', unit:r.unit||'each', category:r.category||'', source:'catalog', id:r.id};
    opts.push(label);
  });
  if(!category){
    (receiptSugs||[]).forEach(s=>{
      const label='🧾 '+s.name+(s.vendor?` — ${s.vendor}`:'')+`  ($${(s.last_price||0)})`;
      if(pdM4MatPickIndex[label]) return;
      pdM4MatPickIndex[label]={name:s.name, price:s.last_price||0, vendor:s.vendor||'', unit:'each', category:'', source:'receipt'};
      opts.push(label);
    });
  }
  dl.innerHTML=opts.map(o=>`<option value="${escHtml(o)}"></option>`).join('');
}
function pdM4FetchMatSuggest(){
  clearTimeout(pdM4MatSuggestTimer);
  pdM4MatSuggestTimer=setTimeout(async()=>{
    const vendor=(document.getElementById('pd-m4-mat-vendor')||{}).value||'';
    const q=(document.getElementById('pd-m4-mat-pick')||{}).value||'';
    try{
      const url='/api/ahb/estimator/material-suggest?vendor='+encodeURIComponent(vendor)+'&q='+encodeURIComponent(q.trim());
      const sugs=await fetch(url).then(r=>r.json());
      pdBuildMatDatalist(Array.isArray(sugs)?sugs:[]);
    }catch(e){ pdBuildMatDatalist([]); }
  },220);
}
function pdOnMatVendorChange(){ pdBuildMatDatalist([]); pdM4FetchMatSuggest(); }
function pdOnMatCategoryChange(){ pdBuildMatDatalist([]); pdM4FetchMatSuggest(); }
function pdM4MatPickChanged(){
  const inp=document.getElementById('pd-m4-mat-pick');
  const hit=pdM4MatPickIndex[inp.value];
  if(hit) document.getElementById('pd-m4-mat-pick-price').value=hit.price||0;
  pdM4FetchMatSuggest();
}
function pdM4MatMode(mode){
  pdM4MatModeVal=(mode==='items'?'items':'lump');
  document.querySelectorAll('#pd-est-pane-4 .m4-tab[data-pdmat="lump"],#pd-est-pane-4 .m4-tab[data-pdmat="items"]').forEach(b=>b.classList.toggle('active', b.dataset.pdmat===pdM4MatModeVal));
  document.getElementById('pd-m4-mat-lump-row').style.display=(pdM4MatModeVal==='lump'?'block':'none');
  document.getElementById('pd-m4-mat-items-row').style.display=(pdM4MatModeVal==='items'?'block':'none');
  if(pdM4MatModeVal==='items') pdEnsureMaterials();
  recalcPdM4();
}
function pdM4AddMaterialFromPicker(){
  const inp=document.getElementById('pd-m4-mat-pick');
  const label=(inp.value||'').trim();
  const vendor=(document.getElementById('pd-m4-mat-vendor')||{}).value||'';
  let price=parseFloat(document.getElementById('pd-m4-mat-pick-price').value)||0;
  const qty=parseFloat(document.getElementById('pd-m4-mat-pick-qty').value)||0;
  if(!label){ showToast('Pick or type a material','error'); return; }
  if(!qty){ showToast('Enter a qty','error'); return; }
  const hit=pdM4MatPickIndex[label];
  let name=hit?hit.name:label;
  name=name.replace(/^[★🧾]\s*/,'').replace(/\s+—\s+.*\(\$[0-9.]*\)$/,'');
  const vlabel=vendor||(hit&&hit.vendor)||'';
  if(hit && !price) price=hit.price||0;
  pdM4MatRows.push({name:name+(vlabel?` (${vlabel})`:''), qty, rate:price});
  pdM4MatModeVal='items';
  pdM4RenderMaterials();
  inp.value=''; document.getElementById('pd-m4-mat-pick-price').value=''; document.getElementById('pd-m4-mat-pick-qty').value='1'; inp.focus();
}
async function pdM4SaveMatPickToCatalog(){
  const inp=document.getElementById('pd-m4-mat-pick');
  const label=(inp.value||'').trim();
  if(!label){ showToast('Pick or type a material first','error'); return; }
  const hit=pdM4MatPickIndex[label];
  const vendor=(document.getElementById('pd-m4-mat-vendor')||{}).value || (hit&&hit.vendor) || '';
  let name=hit?hit.name:label;
  name=name.replace(/^[★🧾]\s*/,'').replace(/\s+—\s+.*\(\$[0-9.]*\)$/,'');
  const price=parseFloat(document.getElementById('pd-m4-mat-pick-price').value)||(hit&&hit.price)||0;
  const category=(hit&&hit.category)||(document.getElementById('pd-m4-mat-category')||{}).value||'';
  const d=await fetch('/api/ahb/estimator/materials',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({vendor, name, unit:(hit&&hit.unit)||'each', unit_price:price, category})}).then(r=>r.json());
  if(d.success){ showToast('Saved to catalog: '+name); pdMatLoaded=false; pdEnsureMaterials(); }
  else showToast(d.error||'Save failed','error');
}
function pdM4RenderMaterials(){
  const tb=document.getElementById('pd-m4-mat-tbody');
  if(!pdM4MatRows.length){
    tb.innerHTML='<tr id="pd-m4-mat-empty"><td colspan="5" style="text-align:center;color:#444;padding:10px;font-size:10px">No materials yet — search above and click + Add.</td></tr>';
  } else {
    tb.innerHTML=pdM4MatRows.map((r,i)=>{
      const total=(parseFloat(r.rate)||0)*(parseFloat(r.qty)||0);
      return `<tr>
        <td><input type="text" value="${escHtml(r.name||'')}" oninput="pdM4UpdateMaterial(${i},'name',this.value)" style="width:100%"></td>
        <td><input type="number" min="0" step="0.5" value="${r.qty}" oninput="pdM4UpdateMaterial(${i},'qty',this.value)" style="width:100%"></td>
        <td><input type="number" min="0" step="0.01" value="${r.rate}" oninput="pdM4UpdateMaterial(${i},'rate',this.value)" style="width:100%"></td>
        <td style="text-align:right;color:#00d084;font-weight:600;font-family:monospace">${fmtCurrency(total)}</td>
        <td><button class="btn-icon del" onclick="pdM4RemoveMaterial(${i})">&#10005;</button></td>
      </tr>`;
    }).join('');
  }
  recalcPdM4();
}
function pdM4UpdateMaterial(i,field,val){
  if(!pdM4MatRows[i])return;
  if(field==='rate'||field==='qty') val=parseFloat(val)||0;
  pdM4MatRows[i][field]=val;
  const total=(parseFloat(pdM4MatRows[i].rate)||0)*(parseFloat(pdM4MatRows[i].qty)||0);
  const tr=document.querySelectorAll('#pd-m4-mat-tbody tr')[i];
  if(tr && tr.children[3]) tr.children[3].textContent=fmtCurrency(total);
  recalcPdM4();
}
function pdM4RemoveMaterial(i){
  pdM4MatRows.splice(i,1);
  pdM4RenderMaterials();
}
function pdM4GetMaterialsTotal(){
  if(pdM4MatModeVal==='items') return pdM4MatRows.reduce((a,r)=>a+((parseFloat(r.rate)||0)*(parseFloat(r.qty)||0)),0);
  return parseFloat((document.getElementById('pd-m4-mat-cost')||{}).value)||0;
}
```

- [ ] **Step 3c: Route `recalcPdM4` materials through the new total**

In `recalcPdM4()` (`~10851`), change the first line:
```javascript
  const mat=parseFloat(document.getElementById('pd-m4-mat-cost').value)||0;
```
to:
```javascript
  const mat=pdM4GetMaterialsTotal();
```
(The existing `document.getElementById('pd-m4-mat-total').textContent=fmtCurrency(mat);` later in the function already reflects whichever mode is active.)

- [ ] **Step 4: Restart the dashboard and run the full test file**

Run:
```bash
sudo systemctl restart baza-dashboard
cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest dashboard/tests/test_project_modal_estimator.py -v
```
Expected: all tests PASS (5 total: 2 from Task 1 + 3 from Task 2).

- [ ] **Step 5: Stage (no commit)**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
git add dashboard/templates/ahb123.html dashboard/tests/test_project_modal_estimator.py
```

---

## Task 3: Regression guard + live smoke test

**Files:** none (verification only)

- [ ] **Step 1: Run the estimator-adjacent test suite to confirm no regressions**

Run:
```bash
cd /home/switchhacker/baza-empire/agent-framework-v3 && venv/bin/python -m pytest dashboard/tests/ -v
```
Expected: the new file passes and no previously-green test (`test_invoice_terms.py`, `test_mobile_pwa.py`, `test_share_docs.py`, email tests) regresses.

- [ ] **Step 2: Live smoke the served page**

Run:
```bash
curl -s http://localhost:8888/ahb123 | grep -o -e 'pd-est-pane-5' -e 'data-m="5"' -e 'repeat(5,1fr)' -e 'pd-m4-mat-pick' -e 'pdM4MatMode' -e 'pd-m4-mat-tbody' | sort -u
```
Expected: all six markers printed — confirms the restarted service serves the new markup (Jinja cache cleared).

- [ ] **Step 3: Manual click-path (Serge or implementer in browser)**

1. ahb123 → open a project → **Quotes & Estimator** → **+ Add Quote**.
2. Click **📐 Unit Cost** → pick a scope with a cost-book entry → qty/tier/condition → **Calculate** → range renders → **Save as ACTIVE Quote** writes a quote.
3. Click **🛠 Custom Pricing** → in Material Cost click **📋 Itemized** → Vendor/Category/search → **+ Add** a line → Custom Total reflects the itemized materials → **Save Custom Quote**.

- [ ] **Step 4: Append a session-log entry**

```bash
printf '\n### %s | EstimatOR parity shipped in project modal\n%s\n' "$(date '+%Y-%m-%d %H:%M')" "<one-line summary of what shipped + test count>" >> /home/switchhacker/Desktop/baza-session-log.md
```

---

## Self-Review

**Spec coverage:**
- M5 in project modal → Task 1 (button, pane, `pdSwitchEstMethod`, `pdM5Init`, `pdRunMethod(5)`, Save-as-Quote). ✓
- Itemized materials picker in modal M4 → Task 2 (tabs, picker UI, `pd*` JS, roll-up via `pdM4GetMaterialsTotal` → `recalcPdM4`). ✓
- No backend changes → confirmed; only existing endpoints called. ✓
- Reuse shared catalog modal → `openMaterialsCatalogModal()` reused, no new modal. ✓
- TDD via `test_project_modal_estimator.py` (markup-presence + method5/material-suggest regression). ✓
- Jinja-cache restart + stage-only → Task 1/2 Step 4–5, Task 3 Step 2. ✓

**Placeholder scan:** none — all steps contain concrete code/commands.

**Type/name consistency:** variable `pdM4MatModeVal` vs function `pdM4MatMode` (no collision, mirrors standalone `m4MatModeVal`/`m4MatMode`); `pdM4GetMaterialsTotal` defined in Task 2 Step 3b and consumed in Step 3c; `pd-m4-mat-tbody` used in both HTML (3a) and JS (`pdM4RenderMaterials`); `window._pdLastQuote` shape matches what `pdSaveQuote` reads (`scope`/`description`/`breakdown`).

**Known limitation (matches existing behavior, out of scope):** `pdM4MatRows`/`pdM4RentalRows` are not cleared between opening different projects' modals — same as the pre-existing equipment-rentals behavior. Not introduced by this change; left consistent.
