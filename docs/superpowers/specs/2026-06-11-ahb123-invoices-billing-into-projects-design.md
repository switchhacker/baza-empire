# ahb123 — Merge InvoiceIT + Billing into Projects

**Date:** 2026-06-11
**Goal:** Projects becomes the single primary surface for invoicing and billing. Each project's detail modal carries full invoice tooling (create, edit, share, PDF, mark paid, delete) and billing data/status. The standalone InvoiceIT and Billing leaf tabs are retired.

## Why

InvoiceIT and Billing were separate leaves under the Projects / Treasury super-tabs, forcing tab-hopping: the project modal's "Linked Invoice" panel only showed the first invoice and punted editing to InvoiceIT (`closeModal → switchTab → editInvoice`). Billing's "Completed Projects by Year" duplicated the Projects By-Year view. All backend endpoints needed for the merge already exist — this is a frontend-only restructure of `dashboard/templates/ahb123.html`.

## Design

### 1. Project detail modal — "🧾 Invoices & Billing" panel
Replaces the "Linked Invoice" panel.

- **Billing summary strip** (from `p._payment` + invoice statuses): Total Invoiced · Paid · Owed · status chip (`✓ PAID` / `⚠ BALANCE DUE` / `🔥 OVERDUE + $interest` / latest status). Overdue interest pulled from `/api/ahb/billing/summary` overdue_details when applicable.
- **Invoice list** — every row of `p.invoices` (not just `[0]`): number, total, status badge, due date, and actions: ✎ Edit (in-place), 📄 PDF, ⤓ Download, 🔗 Share, ✓ Mark Paid / ↩ Mark Unpaid, 📅 Move to year, ✕ Delete.
- **Header actions**: `+ New Invoice` (opens the body-level invoice modal prefilled with this project + client), `⚙ From Phases` (existing `invoiceFromProjectDetail`), `Sync Line Items` (existing).
- **Return-to-project flow**: global `_invoiceReturnPid`. Opening the invoice editor from the project modal closes the project modal first (the invoice modal sits earlier in the DOM, so stacking would hide it); on invoice save or cancel, `openProjectDetail(_invoiceReturnPid)` reopens the project.
- `editInvoice(id)` gets a cache-miss fallback: if the invoice isn't in `allInvoices`, refetch before bailing.
- Payments panel keeps anchoring to the first (linked) invoice via `pd-invoice-id` — unchanged.

### 2. Projects tab (primary surface)
- **Billing stats row** under the project stats: Total Receivable · Payments Due (Sent) · Overdue (+ accrued interest) · Collected (Paid) — from `/api/ahb/billing/summary`, following the year filter.
- `+ New Invoice` button in the page header.
- **Per-project invoice chip** in the table and By-Year rows: `💰 N inv · $owed` (green when fully paid).
- **Unlinked invoices panel** — appears only when invoices exist with no `project_id`, so nothing becomes unreachable after the InvoiceIT table goes away. Rows open the invoice editor (where a project can be assigned).

### 3. Navigation / removal
- `TAB_GROUPS.projects.children = ['projects','changeorders']`; `treasury` loses `billing`.
- `LEAF_META` drops `invoices` and `billing`; loaders map drops both.
- `switchTab` aliases legacy names: `invoices` and `billing` → `projects` (covers old `?tab=` deep links and stale sessionStorage).
- Delete panes `#tab-invoices`, `#tab-billing` and dead functions: `renderInvoices`, `renderInvoiceStats`, `filterInvoices`, `setInvoiceView`, `renderInvoiceKanban`, `loadBilling`, `renderBillingCompletedByYear`, `_jumpToBillingYear`, `openInvoiceFromProject`.
- `loadInvoices()` becomes data-only (fetch `allInvoices`, refresh project billing surfaces if visible) — Uncle Sam tax rollup and Change Orders keep reading `allInvoices`.
- `markInvoicePaid` / `markInvoiceUnpaid` / `deleteInvoice` refresh via a new `refreshBillingSurfaces()` (reload invoices + projects; re-render the open project detail) instead of `loadBilling()`.
- `m4PushToInvoice` drops its trailing `switchTab('invoices')` (the invoice modal is body-level; no tab switch needed).

### 4. Backend
No changes. Endpoints used: `GET/POST/PUT/DELETE /api/ahb/invoices*`, `POST /api/ahb/invoices/from-project/<pid>`, `GET /api/ahb/invoices/<iid>/pdf`, `GET /api/ahb/billing/summary`, `GET /api/ahb/projects/<pid>/detail` (already returns all invoices + `_payment`).

## Error handling
- Invoice editor opened with empty cache → fallback refetch, toast on true miss.
- Billing summary fetch failure → stat cards show `—`, no crash.
- Projects with zero invoices → chip omitted; modal panel shows "No invoices — create one" affordances.

## Testing
- `node --check` on extracted `<script>` blocks of ahb123.html.
- Restart `baza-dashboard` (Jinja caches templates at debug=False) and curl the page.
- Manual smoke: open a project → see invoices list → edit → save → returns to project; legacy `?tab=invoices` lands on Projects.
