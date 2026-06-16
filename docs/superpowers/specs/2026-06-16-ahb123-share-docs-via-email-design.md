# AHB123 — Share quote / invoice / estimate (and artifacts) via email as real PDF attachments

**Date:** 2026-06-16
**Area:** `dashboard/email_studio.py`, `dashboard/app.py`, `dashboard/templates/ahb123.html`
**Status:** design — pending implementation plan
**Supersedes:** Piece C of `2026-06-15-ahb123-billing-crud-email-share-design.md` (refined here with
concrete decisions and a corrected page-architecture approach).

## Goal

From AHB123 projects, share a **quote, invoice, or estimate** — and project **artifacts**
(photos / documents / receipts) — by email, attaching the **actual PDF/file bytes**, not a
link. Baza is a local server with no public web host, so a "download link" is useless to an
external recipient. The PDF is rendered server-side and attached in-memory to a Gmail message
sent through the existing email service.

## Decisions (locked)

- **Doc types:** Quote **and** Invoice **and** Estimate (all share the WeasyPrint render family).
- **From-account picker:** the share composer includes a "Send from" dropdown of connected
  accounts (defaults to the active account).
- **Artifacts included:** multi-select "Share via email" on the project photos / documents /
  receipts, attaching the real files.
- **No links, no temp hosting:** attachments are bytes inside the MIME message.

## Background — current state (verified)

- **PDF rendering already exists** for all three doc types (WeasyPrint, HTML fallback):
  - Invoice: `GET /api/ahb/invoices/<iid>/pdf` — `app.py` ~9908.
  - Quote:   `GET /api/ahb/quotes/<int:qid>/pdf` — `app.py` ~6006.
  - Estimate:`GET /api/ahb/estimates/<eid>/pdf` — `app.py` ~7366.
- **Send is text-only.** `_mime_message()` (`email_studio.py` ~670) builds `multipart/alternative`
  (text + html); **no attachment support**. `POST /api/email2/send` (~695) has no `attachments`
  field. The route already resolves the sending account via `_req_account_id()` (accepts an
  `account` field).
- **Artifacts** live on disk under `dashboard/artifacts/<project_id>/` (`ARTIFACTS_DIR`,
  `app.py:41`); listed via `artifacts_for_project(pid)`.
- **`/email` and `/ahb123` are separate full-page routes** (`app.py` 3997 / 5396). A button in
  `ahb123.html` therefore **cannot** call `email.html`'s `openCompose()`. The share UI must be
  self-contained in the AHB123 page and talk directly to the JSON send API.
- **Circular import note:** `app.py` imports `email_bp` from `email_studio.py`, so
  `email_studio.py` must **not** import `app.py` at module load. PDF rendering is reached via a
  **deferred (function-local) import**.

## Design

### Backend — `app.py`: shared PDF renderer

Extract the HTML-build + WeasyPrint logic currently inline in the three PDF routes into one
helper:

```
render_ahb_doc_pdf(kind: str, doc_id) -> (filename: str, mimetype: str, data: bytes)
    kind ∈ {"invoice", "quote", "estimate"}
```

- Returns real `application/pdf` bytes when WeasyPrint succeeds; on the existing HTML-fallback
  path it returns `text/html` bytes with an `.html` filename (so a recipient still gets the
  document). Each route is refactored to call this helper and wrap the result in its `Response`
  (behaviour unchanged for the existing GET endpoints).
- Raises a clear exception (e.g. `LookupError`) if the id doesn't exist, so the send path can
  return a 404-ish error.

### Backend — `email_studio.py`: attachments in send

1. **`_mime_message(... attachments: list[dict] | None = None)`** where each attachment is the
   resolved `{filename, mimetype, data: bytes}`:
   - **No attachments** → unchanged `multipart/alternative` (back-compat).
   - **With attachments** → build `multipart/mixed`: first part is the existing
     `multipart/alternative` (text + html), then one `MIMEApplication`/`MIMEBase` part per
     attachment with `Content-Disposition: attachment; filename=...`.

2. **`POST /api/email2/send`** accepts a new optional `attachments` field — a list of
   **server-side references** (never client uploads):
   - `{type:"invoice_pdf",  invoice_id}`  → `render_ahb_doc_pdf("invoice", id)`
   - `{type:"quote_pdf",    quote_id}`    → `render_ahb_doc_pdf("quote", id)`
   - `{type:"estimate_pdf", estimate_id}` → `render_ahb_doc_pdf("estimate", id)`
   - `{type:"artifact", project_id, path}` → read file from `ARTIFACTS_DIR/<project_id>/<path>`.
   - Doc-PDF refs resolved via **deferred import**: `from app import render_ahb_doc_pdf`
     inside the handler.
   - A small resolver builds the `{filename, mimetype, data}` list, then calls `_mime_message`.

3. **Guards:**
   - **Total size cap** 25 MB (Gmail's limit); over → `400` with a clear message naming the cap.
   - **Path-traversal guard** for artifacts: `os.path.realpath` of the resolved file must start
     with `os.path.realpath(ARTIFACTS_DIR/<project_id>) + os.sep`; otherwise reject.
   - **Privacy exclusion:** reject artifact paths under `.private-inbound/` or `.vault_meta/`
     (consistent with "inbound media is private by default").
   - Unknown `type`, missing id/path, or render failure → `400`/`404` with the reason; the
     email is **not** sent if any attachment fails to resolve (all-or-nothing).

### Frontend — `ahb123.html`: self-contained Share modal

1. **One reusable share modal** (body-level, per the dashboard modal rule) with:
   - **To** (prefilled with the project/client email — `#project-client-email`, falls back to
     the client's `ahb_clients` email).
   - **Send from** `<select>` populated from `GET /api/email2/accounts` (default = active).
   - **Subject** + **body** prefilled per context (e.g. `Quote #<n> — <project title>`).
   - An **attachment chip list** showing what will be sent (the doc PDF and/or selected files).
   - **Send** → `POST /api/email2/send` with `{account, to, subject, body, attachments:[...refs]}`.
   - Success/error toast; close on success.

2. **Entry points (buttons), placed next to the existing PDF buttons:**
   - **Invoice** (near `viewInvoicePDF`, ~6748): "✉️ Share" → opens modal with
     `[{type:"invoice_pdf", invoice_id}]`.
   - **Quote** (near `pdViewQuotePDF`, ~10561): "✉️ Share" → `[{type:"quote_pdf", quote_id}]`.
   - **Estimate** (near the estimate PDF button, ~9770): "✉️ Share" →
     `[{type:"estimate_pdf", estimate_id}]`.
   - **Artifacts tabs (photos / documents / receipts):** a multi-select mode → "✉️ Share
     selected" → modal with one `{type:"artifact", project_id, path}` ref per selected file.

3. A single `openShareEmail({to, subject, body, attachments})` JS function drives all entry
   points (mirrors the old spec's `pref` idea, but local to AHB123).

## Out of scope (v1)

- Editing/branding the email body template beyond a sensible prefill.
- Inline image previews of attachments in the modal (chips with filename + size only).
- Sharing from the global Data Hub / `/cloud` views (this is AHB123-project-scoped).
- Scheduled/queued sending — send is synchronous.

## Error handling

- Any attachment ref failing to resolve (missing doc, bad path, oversize) aborts the send with
  a specific message; nothing is sent half-formed.
- WeasyPrint absent → the doc attaches as `.html` (existing fallback), surfaced in the success
  toast wording is not required, but the filename extension reflects it.
- No connected accounts → the From `<select>` is empty and Send is disabled with a hint to
  connect an account in the Email tab.

## Testing

- `render_ahb_doc_pdf`: for each kind, returns bytes + correct filename; unknown id raises;
  the three GET routes still return the same content-type as before (golden check).
- `_mime_message` with attachments: produces `multipart/mixed`, attachment part has
  `Content-Disposition: attachment` and decodes back to the original bytes; no-attachment call
  is byte-identical to the pre-change output.
- `POST /api/email2/send` (Gmail `send` mocked): an `invoice_pdf` ref triggers
  `render_ahb_doc_pdf` and an attachment part is present; oversize (mock >25 MB) → 400;
  artifact path `../../etc/passwd` → rejected; `.private-inbound/...` → rejected.
- Follow the repo's existing Flask test-client pattern (as used by the social blueprint tests).

## Build order

1. `app.py`: extract `render_ahb_doc_pdf` + re-point the 3 routes (no behaviour change) → test.
2. `email_studio.py`: `_mime_message` attachments + `/send` `attachments` resolver + guards
   (deferred import) → tests.
3. `ahb123.html`: share modal + From picker + invoice/quote/estimate "Share" buttons.
4. `ahb123.html`: artifact multi-select share on photos/documents/receipts.
5. `sudo systemctl restart baza-dashboard` (Jinja cache) → manual verify: share each doc type +
   2 photos to a real address; confirm the PDF/files arrive as attachments.
```
