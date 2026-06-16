# Materials Catalog Picker — Design Spec

**Date:** 2026-06-16
**Author:** Claude (brainstormed with Serge)
**Status:** Approved design → ready for implementation plan
**Area:** AHB123 dashboard — project detail modal → Custom Pricing (Method 4) → Materials section

## Goal

In the Custom Pricing (Method 4) tab of the project detail modal, let the user **build a materials line-item list from a product picker** — the same way the Equipment section works today — choosing **vendor → product → quantity**, across multiple vendors (Home Depot, Lowe's, Amazon, …). The existing manual materials cost input stays. The picker is backed by a **self-maintained local catalog** (local-first hard rule), enriched with **opt-in suggestions from past receipts**.

## Constraints / Context

- **Local-first is a hard rule.** No live Home Depot/Lowe's/Amazon API. No scraping. The product list is a local DB catalog the user maintains — exactly like the existing `ahb_equipment_catalog`.
- **Mirror the equipment pattern** wherever possible (table, endpoints, picker, manage modal) for consistency and low risk.
- **No change to how a project's materials persist.** Picked materials flow through the existing itemized `m4MatRows` → quote `breakdown` line-items path. No `ahb_quotes` schema change.
- **Dashboard template caching:** `baza-dashboard.service` runs `debug=False`; after editing `dashboard/templates/ahb123.html` you must `sudo systemctl restart baza-dashboard`.
- **Modals must be body-level** (a `.modal-bg` nested inside a `#tab-*` becomes invisible from other tabs). The new Manage modal must sit at body level next to `eqCatalogModal`.

## Reference implementation (what we're cloning)

Equipment section in `dashboard/templates/ahb123.html`:
- Section markup: lines ~1667–1709
- Catalog modal `eqCatalogModal`: lines ~1927–1952
- JS: `loadEquipmentCatalog` (~9868), `m4PopulateEqSelect` (~9877), `m4PrefillRental` (~10000), `m4AddRental` (~10010), `eqcRender`/`eqcSaveRow`/`eqcDeleteRow`/`eqcAddNew`/`openEqCatalogModal` (~10249–10297)

Backend in `dashboard/app.py`:
- Table `ahb_equipment_catalog`: lines ~14052–14061
- Seed: ~14007–14026
- Endpoints `GET/POST /api/ahb/estimator/equipment`, `DELETE .../equipment/<id>`: ~14314–14349

Materials section today (`ahb123.html` ~1599–1631): Lump Sum mode (`m4-mat-cost`, `m4-mat-notes`) + Itemized mode (`m4-mat-tbody`, rows of name/qty/unit$). Itemized rows live in JS `m4MatRows` (~9862) and are turned into line items by `m4BuildLineItemsFromBreakdown` (~10160). The **picker enhances Itemized mode only.**

## Data model — new table `ahb_materials_catalog`

Created idempotently at dashboard startup (same place equipment table is created), in `baza_projects.db`.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | |
| `vendor` | TEXT | "Home Depot" / "Lowe's" / "Amazon" / free-form |
| `name` | TEXT NOT NULL | product name |
| `unit` | TEXT DEFAULT 'each' | each / box / sq ft / lin ft / bag / gal … |
| `unit_price` | REAL DEFAULT 0 | |
| `sku` | TEXT | optional, nullable (model/SKU when known) |
| `category` | TEXT | optional grouping (Lumber, Drywall, Paint, Electrical, Plumbing, Concrete, Fasteners, Insulation, Hardware, Fixtures) |
| `notes` | TEXT | optional |
| `active` | INTEGER DEFAULT 1 | soft-delete |

One product-at-one-vendor per row ("vendor per row" model). Same item at Lowe's is a separate row.

## Seed data — preloaded Home Depot list (required)

On first init (table empty), seed a **decently sized list (~50–70 rows) of the most common Home Depot construction materials** with reference Home Depot 2025–26 prices (Greater Philadelphia), plus a smaller set of common Lowe's and Amazon items. Cover these categories so the picker is immediately useful:

- **Lumber:** 2x4x8 / 2x4x10 / 2x6x8 / 2x10x10 stud & dimensional, 4x8 ½" & ¾" plywood, 4x8 ½" & ⅝" OSB, 1x4 / 1x6 pine, pressure-treated 2x4 / 2x6, furring strip.
- **Drywall:** 4x8 ½" & ⅝" sheet, ⅝" Type X, joint compound (5 gal), drywall tape, drywall screws (1‑lb), corner bead.
- **Concrete/Masonry:** 60‑lb concrete mix, 80‑lb concrete mix, mortar mix, Quikrete fast-set, sand (50‑lb), gravel bag.
- **Fasteners/Hardware:** deck screws (5‑lb), wood screws (1‑lb), framing nails (box), construction adhesive, wall anchors, hinges, cabinet pulls.
- **Paint/Finishing:** interior paint (1 gal), primer (1 gal), caulk (tube), painter's tape, roller kit, sandpaper pack.
- **Electrical:** 14/2 & 12/2 Romex (250 ft), single-gang box, duplex outlet, single-pole switch, wall plate, wire nuts (box), LED recessed light.
- **Plumbing:** ½" & ¾" PEX (100 ft), ½" copper (10 ft), PVC pipe (10 ft), P-trap, supply line, wax ring, ball valve, Teflon tape.
- **Insulation/Weather:** R-13 & R-19 batt roll, foam board (4x8), spray foam can, weatherstrip.
- **Fixtures (common):** standard toilet, vanity 36", kitchen sink, bath faucet, interior door slab, door knob set.

Prices are **reference defaults the user edits** — accuracy is best-effort, not authoritative. (Receipt totals remain the authoritative price source; the catalog is a convenience starting point.) Seed lives alongside the equipment seed in `app.py` (or a small `materials_catalog_seed.json` if cleaner — implementer's call during planning).

## API endpoints (mirror equipment)

In `dashboard/app.py`:

- `GET /api/ahb/estimator/materials` → list active rows, `ORDER BY vendor, name`. Returns JSON array.
- `POST /api/ahb/estimator/materials` → create (no `id`) or update (with `id`). Body: `{id?, vendor, name, unit, unit_price, sku?, category?, notes?}`. `name` required → 400 otherwise. Returns `{success, id}`.
- `DELETE /api/ahb/estimator/materials/<int:id>` → soft delete (`active=0`). Returns `{success}`.
- `GET /api/ahb/estimator/material-suggest?vendor=<v>&q=<text>` → **new.** Queries receipts:
  ```sql
  SELECT COALESCE(NULLIF(store_name,''), vendor) AS vendor,
         json_extract(je.value, '$.name')  AS name,
         json_extract(je.value, '$.price') AS price
  FROM ahb_receipts, json_each(ahb_receipts.items_json) je
  WHERE ahb_receipts.category = 'Materials'
    AND (:vendor = '' OR COALESCE(NULLIF(store_name,''), vendor) = :vendor)
  ```
  Then in Python: filter by `q` (case-insensitive substring on name), drop empty/garbage names, group by (vendor, normalized name) keeping the **most recent price** and a `freq` count, sort by freq desc, **limit ~25**. Returns `[{vendor, name, last_price, freq}]`. Each is tagged source=`receipt` client-side.

  Honest data caveats baked in: ~11% of receipts are itemized; names are OCR-noisy; no clean SKUs. Endpoint must tolerate malformed/empty `items_json` without erroring.

## Picker UX (Itemized materials mode)

Add an "add item" row above the existing materials table (`m4-mat-items-row`), mirroring the equipment add row:

- **Vendor** `<select>` — options = distinct vendors from catalog ∪ canonical materials vendors (from `vendor_kb` seed) ∪ distinct receipt materials vendors; plus an editable/typeable entry for a new vendor. Default could be most-used (Home Depot).
- **Product** autocomplete `<input>` (typeahead) — as the user types, merge:
  - **Catalog matches (★)** from `GET /materials` filtered client-side (or by name), and
  - **Receipt suggestions (🧾)** from `GET /material-suggest?vendor=&q=` (debounced).
  Selecting an option fills the product name + prefills **unit price** (editable).
- **Unit price** number input (prefilled, editable), **Qty** number input, **+ Add** button → push `{name, qty, rate, vendor, unit}` into `m4MatRows`, re-render existing table, recalc total. (Reuses the existing itemized render/save path; `vendor`/`unit` carried for display + line-item description, e.g. "Materials: 2x4x8 Stud (Home Depot)".)
- A **receipt-sourced (🧾)** selection shows a small **"📌 save to catalog"** affordance → one POST to `/materials` promoting it to a permanent catalog row (opt-in). Nothing is auto-saved to the catalog.
- A **"🧱 Manage Materials"** button next to the picker opens the manage modal.

Lump-sum mode is untouched. Switching to Itemized reveals the picker.

## Manage Catalog modal — `materialsCatalogModal`

Body-level modal (next to `eqCatalogModal`), clone of the equipment catalog modal:
- Scrollable table: columns **Vendor, Name, Unit, Unit $, (SKU), Save, Delete** — each row inline-editable, Save → POST, Delete (✕) → DELETE with confirm.
- Add-new row at the bottom: vendor, name, unit, unit price, (sku), **+ Add**.
- JS functions mirror the `eqc*` set: `openMaterialsCatalogModal`, `mcRender`, `mcSaveRow`, `mcDeleteRow`, `mcAddNew`, plus `loadMaterialsCatalog` / `m4PopulateMaterialPicker`.
- **No** bulk "import from receipts" button — receipt promotion is the inline 📌 action in the picker (per design decision: suggest-in-picker, opt-in save).

## Components & boundaries

| Unit | Responsibility | Depends on |
|---|---|---|
| `ahb_materials_catalog` table + init | Persistent catalog storage | sqlite (`baza_projects.db`) |
| Materials catalog endpoints | CRUD over catalog | table |
| `material-suggest` endpoint | Read-only receipt-derived hints | `ahb_receipts.items_json`, `vendor_kb` |
| Picker UI (Itemized mode) | Choose vendor→product→qty, add to `m4MatRows` | catalog + suggest endpoints |
| Manage modal | Maintain catalog | catalog endpoints |
| (unchanged) `m4MatRows` → breakdown line items | Persist project materials | existing save path |

## Error handling

- `material-suggest` must never 500 on bad `items_json` — wrap `json_each` usage defensively (try/except per row or `json_valid` guard); return `[]` on no data.
- POST `/materials` validates `name`; returns 400 with `{success:false,error}` (mirrors equipment).
- Picker degrades gracefully: if catalog empty and no receipt matches, user can still type a free-form product + price + qty and Add (same as a custom row today).
- Vendor free-form: typing a new vendor is allowed and just stored as text.

## Testing (TDD)

New backend tests (follow existing `tests/` patterns):
1. Catalog CRUD: create → list → update → soft-delete (deleted row absent from list).
2. `name` required → 400.
3. `material-suggest`: fixture DB with a couple itemized `Materials` receipts → returns grouped `{vendor,name,last_price,freq}`, respects `vendor` and `q` filters, most-recent price wins, limited count.
4. `material-suggest` resilience: receipt with empty `items_json` / malformed JSON → no error, sensible result.
5. Seed: on empty table, init seeds the expected Home Depot rows (assert count ≥ ~50 and a couple known rows present).

## Out of scope (YAGNI)

- Live vendor APIs / scraping.
- Per-vendor price comparison sub-tables (rejected in favor of vendor-per-row).
- Bulk receipt import UI.
- SKU/UPC reconciliation across receipts.
- Quote/estimate schema changes.
- Historical price trending.

## Open decisions made on user's behalf

- Added `unit` and optional `sku`/`category` columns to the catalog.
- Lump-sum mode untouched; picker enhances Itemized mode only.
- Project materials persist via existing breakdown line-items path (no quote schema change).
- Receipt promotion is inline/opt-in (no bulk importer).
- **Preload a decently sized (~50–70 row) Home Depot catalog** on first init (per explicit user request), with smaller Lowe's/Amazon coverage.
