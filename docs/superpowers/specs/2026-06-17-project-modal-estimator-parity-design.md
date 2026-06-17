# Project-Modal Estimator Parity — Method 5 + Itemized Materials Picker

**Date:** 2026-06-17
**Author:** Serge + Claude
**Status:** Approved (design)

## Problem

The ahb123 **EstimatOR** super tool (standalone tab) has 5 pricing methods. Method 5
(Unit-Cost DB) and the itemized **material-per-line picker** (Method 4 → Material Cost →
📋 Itemized, backed by `ahb_materials_catalog`, built 2026-06-16/17) exist **only** in the
standalone tool.

When Serge opens a **project** (ahb123 → project → **Quotes & Estimator** panel), the
in-modal estimator exposes only Methods 1–4, and its Method 4 materials is a single **flat
dollar input** — no itemized picker. Serge wants to price a project the same way from inside
the project modal.

## Goal

Bring **full parity** of two features into the project-detail modal's estimator:

1. **Method 5 (Unit-Cost DB)** — qty × cost-book rate, tier + site-condition multiplier.
2. **Itemized material-per-line picker** inside the modal's Method 4 Material Cost section.

Non-goals: re-mirroring M1–M3 (already present), the cost-book editor modal, equipment-catalog
changes, or any backend changes.

## Current State (verified)

- **Standalone EstimatOR UI:** `dashboard/templates/ahb123.html`
  - Method 4 build-up: `~1589–1827`; itemized materials picker block: `~1597–1656`.
  - Method 5 pane: `~1830–1865`; `runMethod5()` `~9718`.
  - Materials picker JS: `~10428–10640` (`m4MatMode`, `onMatVendorChange`, `onMatCategoryChange`,
    `m4MatPickChanged`, `m4FetchMatSuggest`, `m4AddMaterialFromPicker`, `m4SaveMatPickToCatalog`,
    `openMaterialsCatalogModal`).
- **Project-modal estimator:** `ahb123.html` `~4587–4773`.
  - Method picker grid `repeat(4,1fr)` at `~4613`; panes `pd-est-pane-1..4`.
  - Project-modal M4 Material Cost is a single flat input `pd-m4-mat-cost` at `~4670`,
    rolled up by `recalcPdM4()`.
  - Method dispatch: `pdSwitchEstMethod(m)` `~10685`, `pdRunMethod(method)` `~10850+`.
- **Backend (no changes needed):** `dashboard/app.py`
  - `POST /api/ahb/estimator/method5` `~14891` — body `{scope, qty, tier, multiplier}`,
    returns cost-book entry with `low/mid/high` + `selected_total`.
  - `GET/POST /api/ahb/estimator/materials` `~14993`; `DELETE .../materials/<id>` `~15022`;
    `GET /api/ahb/estimator/material-suggest` `~15108`.
- **Table:** `ahb_materials_catalog(id, vendor, name, unit, unit_price, sku, category, notes, active)`.

## Approach

**Approach A — `pd`-prefixed parallel copies (chosen).** The project modal already fully
duplicates Methods 1–4 as `pd`-prefixed UI + JS. We follow that exact convention: add a 5th
method and graft the itemized picker into `pd-est-pane-4`, with `pd`-prefixed JS calling the
**same** existing backend endpoints and the **same** shared catalog-manager modal.

Rejected — **Approach B** (refactor standalone `m4Mat*` JS to be context-parameterized and
shared): less duplication but mutates the working, shipped standalone estimator (regression
risk) and breaks the established pd-copy pattern.

## Design

### 1. Method 5 in the project modal

- **Method picker:** change the grid at `~4613` from `repeat(4,1fr)` to `repeat(5,1fr)`; add a
  5th button `📐 Unit Cost`, `data-m="5"`, `onclick="pdSwitchEstMethod(5)"`.
- **New pane `pd-est-pane-5`** (after `pd-est-pane-4`, ~before `pd-est-results`): three controls
  mirroring standalone `m5-*` — Quantity, Quality tier (`low|mid|high`), Site condition
  multiplier (`0.9|1.0|1.15|1.3`) — plus a `📐 Calculate` button → `pdRunMethod(5)`.
  Scope/description are taken from the existing `pd-quote-scope` / `pd-quote-desc` fields
  (no new scope input).
- **`pdSwitchEstMethod(m)`:** extend the active-button toggle and pane show/hide loop to
  include method `5` (`[1,2,3,4,5]`).
- **`pdRunMethod(5)`:** POST `{scope, qty, tier, multiplier}` to
  `/api/ahb/estimator/method5`; render the returned low/mid/high range + `selected_total`
  into `pd-est-results`; include a **Save as Quote** action that uses the modal's existing
  quote-save path (same flow M1–M4 results use). Empty/zero qty → inline validation message,
  no request.

### 2. Itemized material-per-line picker in project-modal M4

- Inside `pd-est-pane-4`'s Material Cost block (`~4665–4671`), add **✎ Lump Sum / 📋 Itemized**
  tabs (`pdM4MatMode('lump'|'items')`):
  - **Lump** keeps the current flat `pd-m4-mat-cost` input and behavior.
  - **Itemized** reveals Vendor `<select>` / Category `<select>` / type-ahead product
    `<input list=...>` (catalog ★ / receipts 🧾) / Unit $ / Qty / **+ Add**, a line-items
    table (`pd-m4-mat-tbody`), and **📌 Save to catalog** + **🧱 Manage Catalog** buttons.
- **`pd`-prefixed JS** (own `pdM4MatRows` state, own DOM ids): `pdM4MatMode`,
  `pdOnMatVendorChange`, `pdOnMatCategoryChange`, `pdM4MatPickChanged` (debounced),
  `pdM4FetchMatSuggest`, `pdM4AddMaterialFromPicker`, `pdM4RemoveMaterial`,
  `pdM4SaveMatPickToCatalog`, `pdRenderMatTable`. These call the existing
  `/api/ahb/estimator/material-suggest` and `/api/ahb/estimator/materials`, and reuse the
  already-shared `openMaterialsCatalogModal()` (no second catalog modal).
- **Roll-up:** itemized rows sum into `pd-m4-mat-total` and feed `recalcPdM4()` at the exact
  point the flat input does today. Active mode (lump vs items) determines which value M4 uses,
  so the rest of the build-up (labor / equipment / profit / overhead → Custom Total → Save
  Quote) is unchanged.
- **Vendor/Category population:** on first open of the picker, fetch the catalog
  (`GET /api/ahb/estimator/materials`) once and populate the Vendor/Category selects from
  distinct values (mirrors standalone init).

### 3. Backend

No changes. Reuse `method5` and `materials*` endpoints exactly as the standalone tool does.

## Data Flow

```
Method 5 (modal):
  pd-quote-scope/desc + qty/tier/mult --pdRunMethod(5)--> POST /api/ahb/estimator/method5
    --> {low,mid,high,selected_total} --> render pd-est-results --> [Save as Quote] --> existing quote-save

Itemized materials (modal M4):
  Vendor/Category/search --pdM4MatPickChanged--> GET /material-suggest --> datalist (★ catalog / 🧾 receipts)
  + Add --> pdM4MatRows[] --> pdRenderMatTable --> pd-m4-mat-total --> recalcPdM4() --> Custom Total
  Save to catalog --> POST /api/ahb/estimator/materials
```

## Testing (TDD)

Follow the existing `dashboard/tests/test_mobile_pwa.py` pattern (import the real Flask app,
use `test_client`, assert on served HTML / JSON shapes). New file
`dashboard/tests/test_project_modal_estimator.py`:

1. **Markup-presence (rendered ahb123 page):** asserts the project-modal estimator now contains
   `data-m="5"`, `pd-est-pane-5`, `pdRunMethod(5)`, the Lump/Itemized tabs (`pdM4MatMode`),
   `pd-m4-mat-pick`, and a `pd-m4-mat-tbody` table — and that the method grid is `repeat(5,1fr)`.
2. **Method 5 endpoint regression (modal-context payload):** `POST /api/ahb/estimator/method5`
   with `{scope, qty, tier, multiplier}` returns `low/mid/high` + `selected_total`.
3. **material-suggest regression:** `GET /api/ahb/estimator/material-suggest?vendor=&q=` returns
   the expected suggestion shape (catalog ★ + receipt 🧾 entries).

Write tests first, watch them fail, implement to green. Keep the existing standalone-estimator
and materials-picker tests green (no regressions).

## Risks / Notes

- **Jinja template cache:** `baza-dashboard.service` runs `debug=False`; after editing
  `ahb123.html`, `sudo systemctl restart baza-dashboard` or the change won't show.
- **Don't manually commit:** `claw-auto-git` hourly-commits `agent-framework-v3`; stage only.
- **DOM-id collisions:** all new modal ids must be `pd`-prefixed to avoid clashing with the
  standalone estimator ids on the same page.
- **Single large template:** `ahb123.html` is big; keep the new JS grouped next to the existing
  `pd`-prefixed M4 functions for locality.

## Out of Scope

- Cost-book editor inside the modal (Method 5 reads existing cost book; edits stay in the
  standalone tool's `openCostBookModal()`).
- Equipment-catalog / disposal changes in the modal.
- Any backend or schema change.
