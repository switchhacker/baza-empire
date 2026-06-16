# AHB123 Email — Unified Inbox ("All inboxes") design

**Date:** 2026-06-16
**Area:** `dashboard/email_studio.py`, `dashboard/templates/email.html`
**Status:** design — pending implementation plan

## Goal

Let the user see mail from **all already-connected Gmail accounts at once**, merged into
one chronological list, instead of switching the single "active" account one at a time.

## Background — current model

- `email_accounts` (SQLite) holds N connected Gmail accounts; exactly one has `is_active=1`.
- The account pill (`#accPill` → `toggleAccountMenu`) switches which account is active.
- All read/list/action endpoints resolve the target account via `_req_account_id()`:
  `request.args.get("account")` / JSON body `account`, falling back to the active account.
- `api_threads` (`/api/email2/threads`) lists one account's threads for a label
  (default `INBOX`); it already special-cases `label == "ALL"` (omit `labelIds`) but no
  sidebar item ever sends that, and it does **not** stamp the owning account onto each thread.
- `api_search` (`/api/email2/search`) is **local-DB / FTS5 over the `emails` cache table**,
  not a live Gmail call. It currently applies **no account filter at all** — so in
  single-account mode it already leaks other accounts' cached hits, and results carry no
  `account_id`.
- The `emails` table already has an `account_id TEXT` column (populated on sync; legacy rows
  may be `NULL`).

## Core challenge

Once threads come from several accounts, every downstream action — open thread, mark read,
reply, modify (archive/star/label) — must target **that thread's** account, not "the active
one." Gmail thread IDs are unique only within an account. Therefore every thread/search
result object must carry its own `account_id`, and the frontend must thread that value into
open/reply/modify calls. (Passing the per-thread account is also correct, and harmless, in
single-account mode.)

## Decisions (locked)

- **Unified view = combined Inbox only.** In "All inboxes" mode the sidebar mailbox
  selection is ignored; it always merges each account's `INBOX`.
- **Search spans all accounts** in unified mode (and is correctly scoped to one account
  otherwise).
- **Approach A:** extend the existing endpoints + add an "All inboxes" pseudo-account to the
  account pill. No new endpoints, no second "which account am I viewing" control.

## Design

### Backend — `email_studio.py`

1. **Extract a hydration helper.** Pull the per-thread head-hydration block currently inline
   in `api_threads` into:
   ```
   _hydrate_thread(svc, con, t, account_id, account_email) -> dict
   ```
   It returns the existing thread dict **plus** `account_id` and `account_email`. The
   single-account path now also stamps these fields.

2. **`api_threads` — `account=ALL`.** When `_req_account_id()` resolves to the sentinel
   `"ALL"`:
   - Load all rows from `email_accounts`.
   - For each account: `svc = _gmail(account.id)`, list `INBOX` threads up to `limit`,
     hydrate each via `_hydrate_thread(..., account.id, account.email)`.
   - Concatenate, sort by `received_at` descending, slice to `limit`.
   - Per-account fetch errors are caught and skipped (one bad token must not blank the view);
     log and continue.
   - In `ALL` mode the requested `label` is forced to `INBOX` (per decision).
   - Single-account behaviour is otherwise unchanged.

3. **`api_search` — account scoping + `account_id` in results.**
   - `SELECT` adds `e.account_id`, and `LEFT JOIN email_accounts a ON a.id = e.account_id`
     to surface `a.email AS account_email`.
   - When the resolved account is a real id (not `ALL`): add
     `AND (e.account_id = ? OR e.account_id IS NULL)` — scopes to that account while keeping
     legacy `NULL`-account cached rows visible (avoids hiding pre-migration mail). This also
     fixes the existing cross-account leak in single mode.
   - When `ALL`: no account filter.
   - Each result object gains `account_id` and `account_email`.

   `_req_account_id()` needs no change — `"ALL"` flows through it as an ordinary string; the
   branching lives in each endpoint.

### Frontend — `templates/email.html`

1. **Account menu** (`renderAccountMenu`): add a "📬 All inboxes" row at the top. Selecting it
   sets `state.activeAccount = {id:'ALL', email:'All inboxes', all:true}` and does **not**
   call the `/activate` endpoint (it's a view mode, not a server-side active switch).
   Re-render the pill to show "All inboxes".

2. **`loadThreads`:** when `state.activeAccount?.all`, request
   `/api/email2/threads?account=ALL&limit=40` (label omitted/forced INBOX). Otherwise pass the
   real `&account=<id>` explicitly so every list is account-stamped.

3. **Per-thread account on every object.** Thread/search result objects now carry `account_id`
   (and `account_email`). Store them as-is in `state.threads`.

4. **Account badge.** In ALL mode, `renderThreads` shows a small colored badge per row with the
   source inbox (e.g. the local part of `account_email`, color hashed from the address). Hidden
   in single-account mode.

5. **Thread actions use the thread's account.** `openThread(tid)` looks up the thread in
   `state.threads`, reads its `account_id`, and:
   - reads the thread via `/api/email2/thread/<tid>?account=<account_id>`,
   - sends the mark-read `modify` with `account` in the body,
   - reply/send include `account` = the thread's `account_id`.
   In single mode `account_id` is just the active account, so the same code path works.

6. **Sidebar in ALL mode:** show only the system **Mailboxes** group; the click handler is a
   no-op for unified mode (Inbox-only), or simply non-highlighting. User Labels are
   account-specific and are hidden while in ALL mode. (Counts are not summed in v1.)

7. **Search:** `searchThreads` passes `&account=ALL` when in unified mode, real `&account=<id>`
   otherwise; renders the same account badge.

## Out of scope (v1)

- Cross-account pagination / "load more" in unified mode (fetch ~`limit` per account, merge,
  slice — enough for an inbox view).
- Summed per-label unread counts in the sidebar while in ALL mode.
- Unified compose "From" picker. Reply already uses the thread's own account. For a
  brand-new compose, the frontend keeps `state.lastRealAccount` (the last non-ALL account the
  user had selected) and sends from that; if none exists yet, fall back to the server's active
  account. A real From-account dropdown is deferred.
- Live (Gmail-API) unified search — search stays FTS-over-cache; only the cache that's been
  synced is searchable, same as today.

## Error handling

- A failing account during `account=ALL` thread fetch is logged and skipped; the merged list
  returns whatever succeeded. If **all** accounts fail, return the existing error shape.
- No accounts connected → unified mode shows the existing "No threads" empty state.

## Testing

- `_hydrate_thread` unit test: stamps `account_id`/`account_email`; single-account output
  shape unchanged vs. pre-refactor (golden dict compare).
- `api_threads?account=ALL`: with `_gmail` and the threads list mocked for ≥2 fake accounts,
  assert merged + sorted-desc + sliced to `limit`, and that one account raising is skipped not
  fatal.
- `api_search`: (a) `account=<id>` returns only that account's + NULL-account rows and includes
  `account_id`; (b) `account=ALL` returns across accounts. Seed the `emails`/`emails_fts`
  tables with rows from two account ids + one NULL.
- Follow the repo's existing Flask test-client pattern used by the social blueprint tests.

## Build order

1. Backend `_hydrate_thread` refactor (no behaviour change) + test → green.
2. Backend `api_threads` `account=ALL` merge + test.
3. Backend `api_search` scoping + `account_id` + test.
4. Frontend: account-menu "All inboxes" entry + `state.activeAccount.all` plumbing in
   `loadThreads`/`searchThreads`.
5. Frontend: per-thread `account_id` through `openThread`/modify/reply + account badge.
6. Restart `baza-dashboard.service` (Jinja template cache) and manual verify with ≥2 accounts.
