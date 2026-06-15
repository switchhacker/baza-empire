# AHB123 Billing CRUD + Email Sharing — Design Spec

**Date:** 2026-06-15
**Author:** Serge Tkach (via Claude)
**Scope:** `baza-empire/agent-framework-v3/dashboard` — `app.py`, `email_studio.py`, `templates/ahb123.html`, `templates/email.html`
**Build order:** D → A+B → C

This spec decomposes one request into four related but independently-shippable pieces. Each gets validated, then built in order. After any template edit (`ahb123.html`, `email.html`) the dashboard must be restarted (`sudo systemctl restart baza-dashboard`) because `baza-dashboard.service` runs `debug=False` and Jinja caches templates.

---

## Piece D — Recipient autocomplete in the email composer (the live bug)

**Problem.** When composing mail, typing in the To field does not suggest recipients like Gmail. Investigation shows the building blocks already exist but are not connected:
- `templates/email.html`: the composer has `#cmpTo` / `#cmpCc` / `#cmpBcc` text inputs (lines ~506–514) and an empty `<div class="ac-pop" id="acPop">` popover (line ~539), but **no JS listener** drives it.
- `email_studio.py`: `GET /api/email2/contacts/suggest?q=` (line ~1073) already returns up to 12 name+email suggestions, sourced from email history (`from_addr` in the `emails` table via `parseaddr()`).

So the endpoint and the popover markup exist; nothing wires the input to the endpoint.

**Design.**
- Add a debounced (~150ms) `input` handler on `#cmpTo` (and `#cmpCc`, `#cmpBcc`). On each keystroke past the last comma/semicolon, fetch `/api/email2/contacts/suggest?q=<fragment>` and render results into `#acPop` positioned under the active field.
- Each suggestion shows `Name <email>`; selecting (mouse click, Enter, or arrow-key + Enter) appends `Name <email>, ` to the field and closes the popover. Escape / blur closes it. Multi-recipient aware: only the fragment after the last delimiter is used as the query.
- **Contacts source upgrade (cheap, included):** extend `contacts/suggest` to also query `ahb_clients` (`name`, `email`) and merge with email-history results, de-duplicated by lowercased email. This makes actual AHBCO clients autocomplete even if they've never emailed in.

**Out of scope for D:** People/Contacts API integration; contact groups.

**Test.** Type a partial name/email in To → suggestions appear; selecting fills the field; multi-recipient (after a comma) suggests on the new fragment; a known client name (in `ahb_clients`) surfaces.

---

## Piece A — Payment edit & delete in the project detail modal

**Problem.** The project detail modal's Payments panel (`ahb123.html` ~4703–4736) can **add** payments (`pdAddPayment()` → `POST /api/ahb/payments`, `app.py` ~10397) but cannot edit or delete them. There are no PUT/DELETE routes and no per-row actions.

**Schema (current).** `ahb_payments(id, invoice_id, amount, payment_method, payment_date, notes, created_at)` — no `project_id` (project is reached via the invoice), no soft-delete columns.

**Design.**
- **Backend — two new routes in `app.py`** beside the existing payment routes:
  - `PUT /api/ahb/payments/<payment_id>` — updates `amount`, `payment_method`, `payment_date`, `notes`. Validates `amount > 0`.
  - `DELETE /api/ahb/payments/<payment_id>` — hard `DELETE FROM ahb_payments WHERE id=?`.
  - Both resolve `invoice_id → project_id`, then run shared status-recompute logic (see B) that can move project status **forward or backward** from the recomputed paid total. Both return `{success, project_id, project_status}`, matching the POST response shape.
  - Both write a billing-log row (see B): action `delete` for DELETE; edits are logged as an `edit` action (B's log records add/edit/delete uniformly).
- **Frontend — `ahb123.html` Payments panel:**
  - `pdLoadPayments()` row render gains ✏️ (edit) and 🗑️ (delete) buttons, following the existing list-row + action-button pattern used elsewhere in the file.
  - **Edit:** ✏️ loads that payment's values into the existing add-payment form and flips it to "update" mode via a hidden `#pd-pay-edit-id` (button label → "Update payment"). `pdAddPayment()` branches: edit-id set → `PUT`, else → `POST` (mirrors the existing `editProject`/`saveProject` pattern).
  - **Delete:** 🗑️ → `confirm()` → `DELETE` → refresh panel + toast.
  - After edit/delete: re-run `pdLoadPayments(...)`, apply returned `project_status` to the UI, `showToast`.

**Test.** Add a payment; edit its amount down → invoice balance and project status update (status can regress, e.g. Completed → In Progress); delete it → status recomputes again; books reflect the change with no orphan.

---

## Piece B — Clean erase + hidden billing log (payments, invoices, receipts)

Two guarantees, applied consistently to **payments, invoices, and receipts**:

### B1 — Clean books ("as if it never existed")
Erasing anything must leave no downstream reference:
- **Invoice delete cascades to its payments.** Today `DELETE /api/ahb/invoices/<iid>` (`app.py` ~6696) is a bare hard delete that **orphans** rows in `ahb_payments` pointing at the gone invoice. Fix: in the same transaction, `DELETE FROM ahb_payments WHERE invoice_id=?` before/with the invoice delete.
- **Payment / receipt delete recomputes** affected invoice balance, project status, and any rollup totals/reports so the erased amount no longer appears anywhere.
- Receipt delete already removes the row + image files (`app.py` ~6924); confirm it also drops out of any project receipt totals after deletion.
- Net rule: after an erase, every financial view (balances, paid totals, project status, reports) reads as though the item never existed. No "voided" markers in those views.

### B2 — Hidden billing log
A new table records add/edit/delete events for the safety trail, **never surfaced in any financial view**:
```sql
CREATE TABLE IF NOT EXISTS ahb_billing_log (
    id TEXT PRIMARY KEY,
    entity_type TEXT,   -- 'payment' | 'invoice' | 'receipt'
    entity_id TEXT,
    action TEXT,        -- 'add' | 'edit' | 'delete'
    amount REAL,        -- amount at time of action (nullable)
    actor TEXT,         -- X-Agent-Id header or 'serge' default
    at TEXT DEFAULT (datetime('now')),
    snapshot TEXT       -- small JSON of the row's key fields at action time
);
```
- A single helper `_billing_log(entity_type, entity_id, action, amount, snapshot, actor)` is called from every add/edit/delete handler for payments, invoices, and receipts.
- This table is **excluded** from all totals, balances, reports, and the `/cloud` and AHB123 financial UIs. It exists only as a private record (queryable later by hand if needed). No UI is built for it in this spec.
- Distinct from the existing `ahb_receipt_corrections` table (which logs receipt *field edits*); `ahb_billing_log` adds the missing add/delete coverage and extends it to payments + invoices. We do not remove `ahb_receipt_corrections`.

**Status-recompute helper.** Extract the project-status logic currently inline in the payment POST (`app.py` ~10431–10438) into a reusable function `_recompute_project_status(project_id)` that derives status from the current paid-total vs invoice-total and writes it, moving forward or back. A, B, and the existing add path all call it.

**Test.** Delete an invoice that has payments → its payments vanish too, project paid-total drops to zero for that invoice, status recomputes, and a `delete` row appears in `ahb_billing_log` (but nowhere in the books). Add a payment → an `add` row is logged; the books show it normally.

---

## Piece C — Share AHB123 documents & artifacts via connected mail accounts (largest)

**Goal.** From AHB123, share **any** artifact — invoices, project photos, documents, receipts, estimates/quotes — via the connected Gmail accounts, with the composer pre-filled.

**What exists.**
- Invoice PDF: `GET /api/ahb/invoices/<iid>/pdf` (weasyprint → PDF, HTML fallback) — `app.py` ~9902.
- Project artifacts list: `GET /api/artifacts?project_id=<pid>`; download `GET /api/artifacts/download/<pid>/<path>` — on disk under `dashboard/artifacts/<project_id>/`.
- Connected accounts: `email-pipeline/accounts/<email>/token.json`, enumerated via `email_studio.py` helpers (`_pick_account`, `_gmail`); `GET /api/email2/accounts`.
- Send: `POST /api/email2/send` (`email_studio.py` ~695) — **text-only**; `_mime_message()` (~670) builds text parts with **no attachment support**. Active account is **global** (sidebar), not selectable per-compose.

**Gaps to close.**
1. **Attachments in send.** Extend `_mime_message()` to build a `multipart/mixed` message when attachments are present, and extend `POST /api/email2/send` to accept an `attachments` field — a list of **server-side references**, not uploads:
   - `{type: 'invoice_pdf', invoice_id}` → server renders the invoice PDF and attaches it.
   - `{type: 'artifact', project_id, path}` → server reads the file from the artifacts dir (path-validated to stay within `artifacts/<project_id>/`) and attaches it.
   - Enforce a total attachment size cap (e.g. 25 MB, Gmail's limit) and reject path traversal.
2. **From-account picker in compose.** Add a "From" `<select>` to the composer populated from `/api/email2/accounts`; pass the chosen account to `/send` (the send route already accepts an `account` param). Defaults to the active account.
3. **Share entry points in AHB123.**
   - On an invoice: a "Share via email" button that opens the composer pre-filled — To = client email (from invoice/`ahb_clients`), Subject = `Invoice <number>`, account defaulted, with `{type:'invoice_pdf', invoice_id}` attached.
   - On the project's photos/documents/receipts tabs: select one or more artifacts → "Share via email" → composer opens with those `{type:'artifact', ...}` references attached and To pre-filled with the client email.
   - Reuse the existing `openCompose(pref)` (`email.html` ~939); extend its `pref` object to carry `account` and `attachments`.

**Privacy guard.** The `.private-inbound/` and `.vault_meta/` artifact areas are excluded from sharing (consistent with the existing rule that they stay hidden from `/cloud`). Only project-visible artifacts are attachable.

**Optional split.** If C is too large for one pass: **C1** = invoice share (just `invoice_pdf` attachments + from-picker + invoice button); **C2** = general artifact share (photos/docs/receipts). Build C1 first, C2 after.

**Test.** From an invoice, "Share via email" opens the composer with the client's address, the PDF attached, and the right From account; sending delivers an email with the PDF. From the photos tab, select 2 photos → share → both arrive as attachments. Oversized selection is rejected with a clear message; a path outside the project's artifacts dir is rejected.

---

## Cross-cutting notes
- **No new heavy dependencies.** weasyprint already present for invoice PDFs; everything else is stdlib (`email.mime`, `sqlite3`) + existing Gmail client.
- **Restart required** after editing `ahb123.html` / `email.html` (Jinja cache): `sudo systemctl restart baza-dashboard`.
- **Auto-git** commits `agent-framework-v3` hourly; do not manually commit there unless time-sensitive.
- **Local-first** rule respected: no new cloud calls; Gmail send is an existing legacy path, contacts/autocomplete and logging are local.

## Build sequence
1. **D** — wire autocomplete + `ahb_clients` source. Smallest, fixes a live annoyance.
2. **A + B** — payment PUT/DELETE + shared `_recompute_project_status` + invoice-delete cascade + `ahb_billing_log` + log calls across payments/invoices/receipts. Same code paths, done together.
3. **C** — attachments in send + from-picker + share entry points (optionally C1 then C2).
