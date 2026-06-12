# EstimatOR v2 — Heavy Equipment Estimator Overhaul

**Date:** 2026-06-12 · **Approved by:** Serge (chat) · **Files:** `dashboard/app.py`, `dashboard/templates/ahb123.html`

## Goal
Overhaul the ahb123 Heavy Equipment → EstimatOR tab: persist estimates, add a unit-cost
method + run-all comparison, deepen Method 4, upgrade the LLM methods to the current
local stack, and give every method the full set of output actions.

## 1. Saved estimates + History
- Reuse existing `ahb_estimates` table; add `method TEXT` and `breakdown TEXT` columns (idempotent ALTER).
- Every method result gets a 💾 Save action → `POST /api/ahb/estimates` (now persists method + breakdown JSON).
- New History section at the bottom of the estimator tab: method badge, scope, total, date.
  Actions per row: Reload (re-render result), Compare (2–3 side-by-side), Attach to Project, PDF, Delete.
- New routes: `PUT/DELETE /api/ahb/estimates/<id>`, `POST /api/ahb/estimates/<id>/to-quote`
  (inserts into `ahb_quotes` for a chosen project → shows in project Quotes panel, inherits quote PDF),
  `GET /api/ahb/estimates/<id>/pdf` (WeasyPrint, HTML fallback — mirrors quote PDF styling).

## 2. Method 5 — Unit-Cost (cost book)
- New `ahb_cost_book` table: scope (unique), label, unit (sqft/unit/lnft), low/mid/high rates, notes.
  Seeded with ~15 Philly-area 2025-26 all-in customer rates (kitchen, bath, addition, basement, deck,
  full reno, roofing, flooring, painting, siding, windows/doors, concrete, fence, drywall, demo).
- UI: quantity + quality tier (economy/standard/premium) + condition multiplier → low/mid/high cards.
- `POST /api/ahb/estimator/method5`; cost book CRUD at `/api/ahb/estimator/costbook` + edit modal.

## 3. Run-All comparison
- Header button runs Methods 2, 3, 5 (and 1/4 when inputs are filled) on the shared description;
  renders a grid of totals with min/avg/max band.

## 4. Method 4 deep upgrade
- Materials: Lump Sum | Itemized tab — itemized table (desc, qty, unit $, total).
- New section: Disposal/Dumpster (10/20/30/40-yd Philly-area presets, editable) + Permits $.
- Equipment catalog moves to new `ahb_equipment_catalog` table (seeded from the 33 hardcoded
  items), CRUD modal, `owned` flag (own gear at internal day rate vs rental).
- `/api/ahb/estimator/equipment` CRUD; the select loads from API with the JS const as fallback.

## 5. LLM upgrade
- Methods 2 & 3: `qwen2.5:14b` → `qwen3.6:27b` with `think=False` + `format:json`.
- **Implementation note:** the originally-chosen `gemma4:26b-a4b-it-qat` (and 12b QAT)
  degenerate under Ollama's JSON grammar constraint — runaway whitespace, broken keys,
  bogus negatives — with both `format:"json"` and full JSON-schema format. qwen3.6:27b
  with `think:false` (same 0.30 thinking-regression guard as the vision fixes) produces
  clean, realistic output. `_ollama_text` gained an optional `think` passthrough.

## 6. Uniform output actions
- Shared action bar on every method result: 💾 Save · 📤 Push to Invoice · 🔗 Attach to Project ·
  🖨 PDF · Copy Total · 🏗 Create Project. PDF/Attach auto-save first when the estimate has no id.
- Push-to-invoice generalized: line items built from the method's breakdown (labor / materials /
  permits / equipment / overhead / profit), reusing Method 4's invoice push machinery.

## Non-goals
- No cloud APIs (hard rule). No changes to invoice lifecycle (primary/balance flow untouched).
- Receipt/quote flows untouched except the new to-quote insert.

## Rollout
Schema init is idempotent at import. After template edits: `sudo systemctl restart baza-dashboard`
(debug=False template cache). Verify: settings GET, method1/5 POST, costbook/equipment GET,
save→history→PDF round-trip.
