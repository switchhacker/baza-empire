# AHB123 Quote → Invoice → Payment-Term Milestones → Email — Design Spec

**Date:** 2026-06-16
**Author:** Serge Tkach (via Claude)
**Scope:** `baza-empire/agent-framework-v3/dashboard` — `app.py`, `email_studio.py`, `templates/ahb123.html`, `templates/email.html`

Make the AHB123 quote/estimation → invoicing → email flow seamless: choose a quote → get an editable invoice built from it; define payment terms once per project; generate milestone invoices (deposit → progress → final) on demand, each crediting what's already been paid; show the payment terms and status on the relevant invoice; and share both quotes and invoices by email with the PDF attached.

After any template edit (`ahb123.html`, `email.html`) restart the dashboard — `baza-dashboard.service` runs `debug=False` so Jinja caches templates: `sudo systemctl restart baza-dashboard`. Backend (`app.py`, `email_studio.py`) edits also require a restart for the running process to pick them up. Auto-git (`claw-auto-git.timer`) commits this tree hourly; don't manually commit unless time-sensitive.

---

## Decisions locked in brainstorming

1. **Milestone generation = one at a time, on demand.** Terms live on the project; a "Generate next invoice" action materializes the next milestone invoice when its stage arrives. Extends today's primary + balance-invoice pattern. (No generate-all-up-front; no "payment request" pseudo-documents.)
2. **Each milestone invoice shows the full scope + a "due now" block.** Every milestone invoice lists the full work line items copied from the quote (editable; subtotal = full contract). A Payment Schedule block states the terms, marks which milestone this invoice is, credits prior payments, and shows the Amount Due Now. This keeps the line-item subtotal meaning "contract" and adds a separate "amount due now" number.
3. **Terms = presets + custom % builder.** Dropdown of common presets plus a Custom builder of named milestones with percentages that must sum to 100%. Percentage-based; each milestone amount = its % × current contract subtotal.
4. **Self-healing amount-due (flagged & accepted).** Amount due on a milestone invoice = cumulative-% target through that milestone − total payments received to date. Over/underpayments auto-correct on the next invoice; the final milestone always clears to the true remaining balance.
5. **Quote→invoice prompts on an existing primary (flagged & accepted).** Creating an invoice from a quote when a primary already exists asks "replace its line items or create a new invoice?" rather than silently overwriting (today it silently overwrites via `_apply_quote_to_invoice`).
6. **Email sharing folds in yesterday's unbuilt Piece C (flagged & accepted).** This build adds attachment support + From-account picker + Share buttons for quotes and invoices. (Pieces D / A / B of the 2026-06-15 spec remain separate.)

This spec does **not** re-introduce auto-injected payment-schedule *line items* into a single invoice — milestones are separate invoices, consistent with the standing "no auto deposit lines" rule ([[project_invoice_flow]]).

---

## 1. Data model (additive migrations only)

Follow the existing guarded column-add pattern in `app.py` (PRAGMA `table_info` check, then `ALTER TABLE ... ADD COLUMN`). No destructive changes; no backfill required beyond defaults.

**`ahb_projects`** — add:
- `payment_terms TEXT DEFAULT ''` — JSON; empty = no structured terms (legacy behavior).

**`ahb_invoices`** — add (the free-text `terms` column already exists and stays):
- `milestone_label TEXT DEFAULT ''` — e.g. `Deposit`, `Progress`, `Final`.
- `milestone_index INTEGER DEFAULT -1` — 0-based position in the schedule; `-1` = not term-driven.
- `amount_due REAL` — nullable; the "amount due now" for this milestone (distinct from `total`/contract subtotal). `NULL` ⇒ render as a plain invoice (amount due = total).
- `terms_snapshot TEXT DEFAULT ''` — JSON copy of the project's schedule frozen at generation time, so an invoice's printed schedule never silently changes if project terms are later edited.

**`payment_terms` / `terms_snapshot` JSON shape:**
```json
{
  "preset": "30_30_40",
  "net_days": 0,
  "milestones": [
    {"label": "Deposit",  "pct": 30},
    {"label": "Progress", "pct": 30},
    {"label": "Final",    "pct": 40}
  ]
}
```
- Presets resolve to fixed milestone arrays:
  - `50_50` → Deposit 50 / Completion 50
  - `30_30_40` → Deposit 30 / Progress 30 / Final 40
  - `100_completion` → Completion 100
  - `net_30` → Net 30 (single milestone pct 100, `net_days: 30`)
  - `custom` → user-defined milestones
- Validation: `sum(pct) == 100` within ±0.01; ≥1 milestone; labels non-empty. Rejected payloads return `400` with a clear message.

---

## 2. Quote → first (deposit) invoice

A quote/estimate carries structured items today: `ahb_estimates.line_items` (JSON) and `ahb_quotes.breakdown` (JSON), plus a free-text `description`. The current `_apply_quote_to_invoice()` (`app.py` ~5890) ignores the structured data and *parses the description text* into lines, then silently overwrites the primary invoice.

**New helper `_invoice_line_items_from_quote(quote)`** (in `app.py`):
- Prefer structured items: parse `breakdown` (quotes) / `line_items` (estimates) JSON into the invoice line-item shape (`description, qty, unit, rate, materials, labor, total, include_in_total`).
- **Fallback** to the existing `_line_items_from_description()` only when no structured data exists.

**Create-from-quote flow:**
- Marking a quote active / "Create invoice from quote":
  - **No primary invoice yet** → create a new primary invoice with the quote's structured line items, editable. If the project has `payment_terms`, stamp it milestone #0 (Deposit) with `amount_due` computed (§4) and `terms_snapshot` frozen.
  - **Primary already exists** → backend honors a body flag `on_existing` ∈ `{replace, new}`. The frontend asks the user (modal/confirm) which they want; default to `replace` only on explicit choice. `replace` rewrites the primary's line items (today's behavior, now from structured data); `new` creates an additional invoice.
- The created invoice is a normal editable invoice; line items can be modified afterward in the existing invoice editor.

`_apply_quote_to_invoice()` is refactored to call `_invoice_line_items_from_quote()` and to require an explicit replace/new decision instead of silent overwrite.

---

## 3. Payment terms on the project

**Backend:**
- `GET /api/ahb/projects/<pid>/payment-terms` → returns the stored JSON (or a default empty structure).
- `PUT /api/ahb/projects/<pid>/payment-terms` → body `{preset, milestones, net_days?}`; validates (sum=100, etc.); stores JSON on `ahb_projects.payment_terms`. Returns the normalized terms.
- A shared `_resolve_payment_terms(preset, milestones)` normalizes a preset name → milestone array and validates custom input.

**Frontend (project detail modal, near the Invoices & Billing panel, `ahb123.html` ~4688):**
- A **Payment Terms** control: dropdown (50/50 · 30/30/40 · 100% on completion · Net 30 · Custom…). Selecting a preset fills the milestone list read-only; **Custom** opens the builder: rows of `[label] [pct]%`, `+ milestone`, live "sum = N% ✓/✗" validator; Save disabled until sum = 100.
- Shows the current terms label inline once set. Persists via the PUT above.

---

## 4. "Generate next invoice" (generalizes Balance Invoice)

**Backend — `POST /api/ahb/projects/<pid>/next-invoice`:**
1. Load project + its `payment_terms` (frozen into `terms_snapshot` on the new invoice). If no terms are set → `400` ("set payment terms first"); the no-terms case is served by the legacy `balance-invoice` route instead (see compat note). `next-invoice` is the terms-driven path only.
2. Determine the next milestone: the lowest `milestone_index` in the schedule not yet represented by an existing invoice for this project. If all milestones already issued → `409` ("all milestones invoiced").
3. Find the contract/primary invoice; **copy its full line items** into the new invoice (editable), so subtotal = contract.
4. Compute **self-healing amount due**:
   - `contract = primary.subtotal`
   - `paid = _project_total_paid(pid)` (sum of `ahb_payments.amount` across **all** the project's invoices).
   - For milestone `k` (0-based): `cum_pct = sum(pct[0..k]) / 100`; `cumulative_target = round(contract * cum_pct, 2)`.
   - `amount_due = round(cumulative_target - paid, 2)`.
   - **Final milestone** (`k == len-1`): `amount_due = round(contract - paid, 2)` (clears rounding drift to the true remaining balance).
   - Clamp negatives to 0 (overpaid → nothing due) but still issue the invoice for the record.
5. Create the invoice: `is_primary=0`, `parent_invoice_id = primary.id`, `status='draft'`, `milestone_label`, `milestone_index`, `amount_due`, `terms_snapshot`, dates/contractor/company/client copied from primary. Notes default to e.g. `"<Label> payment due."`.
6. Return `{success, id, invoice_number, milestone_label, milestone_index, contract, paid, amount_due}`.

**`_project_total_paid(pid)`** — new helper summing payments across every invoice of the project (today's `_ahb_project_payment_summary` is per-primary-invoice; reuse its query shape but aggregate project-wide).

**Compat:** keep `POST /api/ahb/projects/<pid>/balance-invoice` working — it becomes the "final milestone / no-terms" path. When terms exist, it delegates to `next-invoice` forcing the final milestone; when no terms exist, it keeps today's behavior (contract − payments, with the negative "less payment received" credit lines). Existing tests/buttons that call balance-invoice keep passing.

**Negative credit lines:** with the Payment Schedule block stating amount due (§5), the old negative "less payment received" *line items* are **off by default** for term-driven invoices (the schedule block shows credits). They remain available for the legacy no-terms balance invoice.

---

## 5. Invoice rendering — PDF + on-screen

Both the PDF (`GET /api/ahb/invoices/<iid>/pdf`, `app.py` ~9908) and the invoice detail UI gain a **Payment Schedule block** rendered from `terms_snapshot` + `amount_due`, placed below the line-item total:

```
PAYMENT SCHEDULE   (30 / 30 / 40)        Status: SENT
  Deposit  (30%) ............ $6,000     ✓ received
  ▶ Progress (30%) .......... $6,000     ← this invoice
  Final    (40%) ............ $8,000
  ---------------------------------------------------
  Contract total ........... $20,000
  Payments received ........ −$6,000
  AMOUNT DUE NOW ........... $6,000
```
- Marks the current milestone (`milestone_index`).
- Shows the **payment terms label** and the **invoice status** (Draft/Sent/Approved/Paid) — both currently absent from the PDF.
- The amount-due figure comes from the stored `amount_due` (frozen at generation), not recomputed at render, so a sent PDF is stable.
- **Non-term invoices** (`amount_due IS NULL` / `milestone_index = -1`) render exactly as today — no schedule block.

UI: the project detail invoice panel (`#pd-invoice-info`) and the invoice editor show the same schedule summary, the terms label, status, and amount due.

---

## 6. Email sharing of quotes + invoices (adopts Piece C)

**`_mime_message()` (`email_studio.py` ~670):** extend to build `multipart/mixed` when attachments are present (stdlib `email.mime`); keep the text/HTML `alternative` part as the body.

**`POST /api/email2/send`:** accept an `attachments` field — a list of **server-side references** (no client uploads):
- `{type: 'invoice_pdf', invoice_id}` → server renders the invoice PDF (existing route) and attaches it.
- `{type: 'quote_pdf', quote_id}` → server renders the quote PDF (`GET /api/ahb/quotes/<qid>/pdf` exists, `app.py` ~6006) and attaches it.
- Total attachment cap 25 MB (Gmail limit); reject oversize with a clear message.
- (Artifact sharing — photos/docs/receipts, the old Piece C2 — is **out of scope** here; quotes + invoices only, per the request. Note this explicitly so it isn't assumed covered.)

**From-account picker:** add a "From" `<select>` to the composer (`email.html`) populated from `GET /api/email2/accounts`; pass the choice to `/send` (the route already accepts an `account`/`from_addr` param). Defaults to the active account.

**Share entry points (`ahb123.html`):**
- On a quote: **"Share via email"** → opens the composer pre-filled — To = client email (from project/`ahb_clients`), Subject = `Quote <id> — <project>`, From defaulted, `{type:'quote_pdf', quote_id}` attached.
- On an invoice: **"Share via email"** → To = client email, Subject = `Invoice <number>`, `{type:'invoice_pdf', invoice_id}` attached. (Replaces today's link-copy `shareInvoice()` at `ahb123.html` ~6757, or augments it with an email option.)
- Reuse the existing `openCompose(pref)` in `email.html`; extend `pref` to carry `account` and `attachments`.

**Privacy guard:** consistent with existing rules, `.private-inbound/` and `.vault_meta/` are never attachable. Only the rendered quote/invoice PDFs are sent here.

---

## 7. Testing

Per layer, mocking the LLM/Gmail where needed (Flask `test_client` + `monkeypatch`, as in `tests/test_social_blueprint.py` / `tests/test_estimator_llm_errors.py`):

- **Terms:** preset → milestone resolution; custom sum-to-100 validation (accept 100, reject 99/101); JSON round-trip on the project.
- **Quote→invoice:** structured `breakdown`/`line_items` copied into invoice line items; description-parse fallback when no structured data; `on_existing=replace` vs `new` behavior; no silent overwrite.
- **Generate-next / self-healing:** 50/50 and 30/30/40 sequences; exact-payment, **under**-payment, and **over**-payment of the deposit all produce the correct next `amount_due`; final milestone clears to the true remaining balance; `409` when all milestones issued.
- **Rendering:** PDF includes schedule block, terms label, status, and amount due for a term invoice; a non-term invoice renders with no schedule block (regression guard).
- **Email:** `_mime_message` produces a `multipart/mixed` with the PDF part; `/send` attaches `invoice_pdf` and `quote_pdf`; oversize (>25 MB) rejected; From-account honored.
- **Compat:** `balance-invoice` still works for a no-terms project.

---

## 8. Build sequence

1. **Data model + payment-terms CRUD** — migrations, `_resolve_payment_terms`, GET/PUT routes, terms UI control + custom builder.
2. **Quote → invoice** — `_invoice_line_items_from_quote`, refactor `_apply_quote_to_invoice`, replace/new prompt.
3. **Generate next invoice** — `next-invoice` route, `_project_total_paid`, self-healing amount-due, balance-invoice delegation.
4. **Rendering** — Payment Schedule block + terms label + status in PDF and invoice UI.
5. **Email sharing** — `_mime_message` attachments + `/send` refs + From-picker + quote/invoice Share buttons.

Each step is independently testable and shippable; restart `baza-dashboard` after backend/template edits.

---

## Cross-cutting notes

- **No new heavy dependencies** — weasyprint (PDF) and stdlib `email.mime` / `sqlite3` already present.
- **Local-first** respected — no new cloud calls; Gmail send is an existing legacy path.
- **Schemas drift** — verify `ahb_invoices` / `ahb_quotes` / `ahb_estimates` columns with `.schema` before assuming, and use guarded `ADD COLUMN` migrations.
- **Auto-git** commits this tree hourly; the standing "no auto deposit *lines*" rule is preserved (milestones are separate invoices).
