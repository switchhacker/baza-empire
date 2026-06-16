# AHB123 — Customizable invoice Terms & Conditions (company default + per-project override)

**Date:** 2026-06-16
**Area:** `dashboard/app.py`, `dashboard/templates/ahb123.html`, AHB settings UI
**Status:** design — pending implementation plan

## Goal

Let the user customize the **Terms & Conditions** text that appears on invoice PDFs. A single
**company-wide default** is editable in settings; each **project** can override it from the
project modal. Invoices render the effective text live.

## Background — current state (verified)

- The invoice PDF "Terms & Conditions" block is **hardcoded** in `app.py:10153–10163`: six
  numbered clauses (deposit before start, total on completion, approx-duration, change-order,
  pay-to ALL HOME BUILDING CO, late-payment interest). Two clauses interpolate live data:
  clause 3 uses the project's `notes` for "approx N days", clause 6 uses
  `interest_rate = inv.get('overdue_interest_per_week') or 50`.
- `ahb_invoices.terms` already exists but is the short **payment** term ("Net 30") — unrelated.
- Settings precedent: `ahb_estimator_settings` is a **single-row (`id=1`)** table with an
  idempotent `_ensure_*` initializer (`app.py:13950`) and a get/put endpoint
  (`api_estimator_settings`, ~14078). No generic key/value settings table exists.
- `ahb_projects` uses `ALTER TABLE … ADD COLUMN` migrations; create/update go through
  `POST /api/ahb/projects` (5611) and `PUT /api/ahb/projects/<pid>` (5695). Free-text fields
  (description/scope/notes) are normalized via `text_utils.normalize_escaped_newlines()`.
- Invoice PDF is **fully live-rendered** on each request (totals, scope, etc. are not stored),
  so a live-render T&C is consistent with existing behaviour.

## Decisions (locked)

- **Company default + per-project override.**
- **Live re-render** — invoices always show the project's current effective T&C; nothing is
  snapshotted onto the invoice.

## Design

### Data

1. **`DEFAULT_INVOICE_TERMS`** — module constant in `app.py` holding the current six clauses as
   plain text (one clause per line, with their literal "1.…6." numbering). This is the seed and
   the final fallback. The two previously-interpolated values become **literal editable text**
   in the seed (see Tradeoff below).
2. **`ahb_invoice_settings`** — new single-row settings table mirroring the estimator pattern:
   ```
   id INTEGER PRIMARY KEY CHECK (id = 1),
   terms_default TEXT,
   updated_at TEXT DEFAULT CURRENT_TIMESTAMP
   ```
   Idempotent `_ensure_invoice_settings()` creates it and seeds row 1 with
   `DEFAULT_INVOICE_TERMS` (wrapped in the same try/except-on-busy pattern).
3. **`ahb_projects.terms_conditions TEXT`** — new column (one `ALTER TABLE`), the per-project
   override. `NULL`/blank means "use the company default".

### Effective-terms resolution (invoice PDF render)

At render time compute, in order:
1. `project.terms_conditions` if non-empty after `strip()`
2. else `ahb_invoice_settings.terms_default` if non-empty
3. else `DEFAULT_INVOICE_TERMS`

Render the resolved text into the existing T&C `<div>` (replacing the hardcoded `<p>` block):
HTML-escape, then convert newlines to line breaks / per-line `<p>` so the layout matches the
current numbered look. **No auto-numbering** — the text carries its own "1.…" so the user has
full control. Keep the surrounding `<h3>Terms & Conditions</h3>` and signature block as-is.

### Backend endpoints

- `GET /api/ahb/invoice-settings` → `{terms_default}`.
- `PUT /api/ahb/invoice-settings` → save `terms_default` (normalized via
  `normalize_escaped_newlines`).
- `POST`/`PUT /api/ahb/projects` accept and persist `terms_conditions` (normalized, same as
  description/scope/notes). `GET /api/ahb/projects/<id>` already returns all columns → the
  field flows to the modal automatically.

### Frontend

1. **Project modal** (`ahb123.html`, near `#project-notes` ~3970): add a **"Terms & Conditions"**
   `<textarea id="project-terms-conditions">` with placeholder *"Leave blank to use the company
   default terms."* plus a small **"Load company default"** button that fetches
   `/api/ahb/invoice-settings` and fills the textarea for editing. Include the field in the
   modal's load (`set(...)`) and save payload.
2. **Settings UI** (alongside the estimator settings editor): a **"Invoice Terms & Conditions"**
   `<textarea>` + Save, backed by the new `/api/ahb/invoice-settings` endpoints.
3. Body-level modal rule / Jinja-cache restart apply (it's `ahb123.html`/settings template).

## Tradeoff to confirm during review

Making the T&C **fully free-text** means the two currently auto-filled values become literal
text:
- clause 3 "approx **{project notes}** days" → static text the user edits per project (the
  per-project override is the natural place for a real duration);
- clause 6 "**$50.00**/week interest" → static text, no longer linked to the invoice's
  `overdue_interest_per_week` field.

This is the simplest model and matches "let me customize the text". If preserving the dynamic
values matters, a **future** enhancement can add a small documented token set
(`{{duration}}`, `{{interest_rate}}`, `{{client_name}}`, `{{company_name}}`) substituted at
render — explicitly **out of scope for v1**.

## Out of scope (v1)

- Token/placeholder substitution (noted above).
- Customizing quote/estimate T&C (the quote PDF has a single "final pricing may adjust" line,
  not a clause block; this spec is invoice-only per the request).
- Rich-text / formatting beyond line breaks.
- Per-invoice T&C snapshotting (explicitly rejected — live render chosen).

## Error handling

- Settings table busy at boot → `_ensure_invoice_settings` defers (same pattern as estimator);
  render falls back to `DEFAULT_INVOICE_TERMS`.
- Missing/blank everywhere → `DEFAULT_INVOICE_TERMS` guarantees the section is never empty.

## Testing

- `_ensure_invoice_settings` seeds row 1 with `DEFAULT_INVOICE_TERMS`; idempotent on re-run.
- Effective-terms resolution: project override wins; blank project → company default; both
  blank → constant. Unit-test the resolver as a pure function fed dicts.
- Invoice PDF: with a project override set, the rendered HTML contains the override text and not
  the default; HTML-escaping of `<`, `&` verified.
- `PUT /api/ahb/invoice-settings` persists and `GET` returns it; project `PUT` persists
  `terms_conditions` and it round-trips through `GET`.
- Follow the repo's existing Flask test-client pattern.

## Build order

1. `app.py`: `DEFAULT_INVOICE_TERMS` constant (extract current text) + `ahb_invoice_settings`
   table/init + `terms_conditions` column migration.
2. `app.py`: effective-terms resolver + swap the hardcoded invoice T&C block to use it → test.
3. `app.py`: `GET`/`PUT /api/ahb/invoice-settings`; extend project create/update to persist
   `terms_conditions`.
4. `ahb123.html`: project-modal T&C textarea + "Load company default" + load/save wiring.
5. Settings UI: company-default T&C editor.
6. `sudo systemctl restart baza-dashboard` → verify: edit company default, override on one
   project, generate that project's invoice PDF and confirm the override text renders.
