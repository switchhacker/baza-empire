# Payment Terms — Dollar/Percent Mode Toggle

**Date:** 2026-06-25
**Area:** AHB123 dashboard — project modal payment terms
**Files:** `dashboard/templates/ahb123.html`, `dashboard/app.py`, tests

## Problem

Today, project payment terms are **percent-only**. Each milestone is `{label, pct}` and milestones must sum to 100%. The custom editor already supports per-milestone labels and a `+ milestone` button.

Serge wants to define milestones by an explicit **dollar amount** instead of a percent — e.g. "Deposit = $5,000" (how much deposit he's actually receiving), "Funds-available draw = $3,000", "Balance upon completion = $4,000" — with a **toggle to switch the whole schedule between percent and dollar**.

## Decisions (locked with Serge)

1. **Whole-schedule mode**, not per-milestone. One toggle at the top of the custom editor: `Percent | Dollar $`. The entire schedule is either all-percent or all-dollar.
2. **Dollar mode = free amounts, no check.** Each milestone bills exactly the dollar figure typed. No sum-to-total validation, no auto-remainder on the final milestone. (Percent mode keeps its existing sum-to-100 rule and self-healing final milestone.)
3. **Σ shown as info only** in dollar mode — a running dollar total under the rows, purely informational, never blocks Save.
4. **Mode switching allowed anytime**, even after invoices exist. Already-issued invoices keep their frozen `terms_snapshot` (unaffected); only future milestone invoices use the new mode.

## Data model

Schedule-level `mode` added to the terms JSON stored in `ahb_projects.payment_terms`:

```jsonc
{
  "preset": "custom",
  "mode": "amount",          // "percent" (default) | "amount"
  "net_days": 0,
  "milestones": [
    {"label": "Deposit",                 "amount": 5000},
    {"label": "Funds available draw",    "amount": 3000},
    {"label": "Balance upon completion", "amount": 4000}
  ]
}
```

- `mode: "percent"` → milestones are `{label, pct}` (today's shape, unchanged).
- `mode: "amount"` → milestones are `{label, amount}` (amount is a number ≥ 0).
- **Backward compatibility:** terms with no `mode` key are treated as `"percent"`. No migration needed; existing projects and frozen snapshots keep working verbatim.

## Behavior

### Dollar mode
- `amount_due` for milestone *k* = the typed `amount` for that milestone. Full stop — no subtract-paid, no contract-relative math, no remainder clearing.
- Deposit milestone (#0) stamps onto the primary invoice as its typed dollar amount ("how much deposit I'm receiving").
- Each subsequent milestone invoice (via the existing Next-Milestone-Invoice flow) bills its typed amount.
- Line items / invoice `total` still come from the project's line items as today; `amount_due` remains a separate column expressing what's due this milestone. (Unchanged plumbing — only the value source for `amount_due` changes.)

### Percent mode
- Unchanged. Milestones sum to 100%; final milestone self-heals to `contract − paid`.

### Mode switching
- Switching mode rewrites `ahb_projects.payment_terms` only. Invoices already issued carry their own frozen `terms_snapshot` and are not touched. Future milestone invoices read the new mode.

## UI — `dashboard/templates/ahb123.html`

Payment-terms box (currently ~lines 4875-4893) + JS (~lines 15942-16005).

- **Mode toggle** at the top of the custom editor: `Percent | Dollar $` (two-button segmented control or a small select).
- **Presets** (`50_50`, `30_30_40`, `100_completion`, `net_30`) only appear in **Percent** mode — they're inherently percent. Selecting **Dollar** drops straight to the custom milestone editor.
- **Milestone rows:**
  - Percent mode: `[ label ] [ % pct ]` (today's row).
  - Dollar mode: `[ label ] [ $ amount ]`.
  - Both: `– remove` per row, `+ milestone` to add.
- **Σ line:**
  - Percent mode: existing "must equal 100%" indicator, gates Save (unchanged).
  - Dollar mode: running dollar total, **info only**, never gates Save.
- `pdCollectMilestones()` returns `{label, pct}` or `{label, amount}` per the active mode; `pdSaveTerms()` includes `mode` in the PUT body.
- `pdLoadTerms()` / `pdRenderCurrentTerms()` read `mode` and render `$X` or `X%` per milestone; current-terms summary line shows e.g. `Deposit $5,000 · Draw $3,000 · Balance $4,000`.

Reminder: `baza-dashboard.service` runs `debug=False`, so after editing `ahb123.html` the service must be restarted for the template change to show.

## Backend — `dashboard/app.py`

- `_resolve_payment_terms(preset, milestones, mode)`:
  - Default `mode="percent"` when absent (backward compat).
  - Percent mode: existing validation (label required, sum to 100).
  - Amount mode: each milestone needs a non-empty `label` and a numeric `amount ≥ 0`; **skip** the sum-to-100 rule. Returns `{"preset", "mode", "net_days", "milestones"}`.
- `_compute_milestone_amount_due(contract, milestones, k, paid, mode)`:
  - Amount mode: return `float(milestones[k]["amount"] or 0)`.
  - Percent mode: existing self-healing logic (final = `contract − paid`).
- `_stamp_primary_as_deposit()`: in amount mode, set `amount_due` = milestone 0's typed amount; freeze the full terms (incl. `mode`) into `terms_snapshot`.
- `_payment_schedule_block(inv)`: read `mode` from the frozen snapshot; render `$amount` per milestone (and the schedule header) in amount mode, `pct% $amount` in percent mode.
- API route `PUT/GET /api/ahb/projects/<pid>/payment-terms`: pass `mode` through to/from the resolver and JSON column.

All reads of frozen snapshots default missing `mode` to `"percent"`, so old invoices render exactly as before.

## Testing

- `tests/test_payment_terms.py`: amount-mode resolve/validate (label required, negative amount rejected, no sum check), `mode` round-trips through PUT/GET, missing-`mode` defaults to percent.
- `tests/test_milestone_invoices.py`: amount-mode `_compute_milestone_amount_due` returns typed amount for each k (incl. final, no remainder); primary stamped with typed deposit amount.
- `tests/test_invoice_schedule_render.py`: dollar-mode schedule renders `$` amounts; percent snapshot still renders `%`; missing-`mode` snapshot renders as percent.

## Out of scope

- Per-milestone mixed units (rejected in favor of whole-schedule mode).
- Sum-to-total enforcement or auto-remainder in dollar mode (explicitly "free amounts, no check").
- Changing how invoice `total`/line items are computed — only `amount_due` sourcing changes.
- Retroactive rewrite of already-issued invoices on mode switch (snapshots protect them).
