# Thumbtack + Angi Lead & Review Intake (Track B) Design

**Date:** 2026-06-28
**Status:** Approved in brainstorming, pending spec review
**Scope:** Email-parse MVP (B1–B4) — pull Thumbtack & Angi/HomeAdvisor **leads** and **reviews** out of Gmail with a local LLM into the AHB123 dashboard. A new **Leads** tab; the existing **Reviews** tab extended to list external-platform reviews. Partner-API two-way sync (B5) is deliberately deferred to its own spec.

This is **Track B** of a 3-track effort (A = LinkedIn publishing, shipped; C = profile-link directory, later). Build order A → B → C.

---

## 1. Why this is NOT the social publisher

Thumbtack and Angi (formerly HomeAdvisor) are lead-generation / reputation marketplaces, not publishing channels. They have no self-serve posting API; their useful signal is **inbound**: customer leads and reviews, both of which they **email** to the business. Baza already has a working Gmail pipeline, so the local-first, no-approval-needed path is to parse those notification emails. This subsystem is CRM/reputation, separate from `social_connect.py`.

## 2. Grounding facts (verified 2026-06-28)

- Lead/review notification email lands in **contactahbco@gmail.com** (active sync) and **sergek729@gmail.com** (configured, `is_active=0`, has a stored token).
- The current email sync (`email-pipeline/fetch_emails.py`) captures **none** of these today (0 Thumbtack/Angi/HomeAdvisor rows in the `emails` table) — it is primary-account/filtered. Therefore intake must **query Gmail directly by sender**, independent of the existing sync, which also yields historical **backfill**.
- DB is `dashboard/baza_projects.db`. Existing relevant tables: `ahb_clients` (has `source`, `status`), `ahb_projects` (has `acquisition_type`, `status` ∈ Planning/In Progress/Completed), `email_accounts` (id, email, label, token_path, is_active), `emails`. No `leads`/`reviews`-for-external tables exist. The existing **Reviews tab** (`tab-reviews`) is AHB123's *first-party* "Leave Us a Review" collection + moderation (`/api/reviews/all`) — a different dataset from external-platform reviews.
- Dashboard tabs include `clients`, `projects`, `social`, `reviews`, `email`. A new `leads` tab will be added.

## 3. Local-first & privacy

- **Local-first (hard rule):** Gmail read uses the existing OAuth tokens (already in use, not a new cloud API). All classification, field extraction, and reply drafting run on **local Ollama**. No cloud LLM. No new outbound API.
- Lead emails contain customer PII; storing it in `ahb_leads` is the intended CRM behavior (this is Serge's own business data, not third-party inbound media). The Telegram `.private-inbound` rule does not apply here.

## 4. Architecture

### 4.1 `dashboard/lead_intake.py` (core; network/LLM behind monkeypatchable boundaries)
- **Sender config:**
  - Thumbtack: `thumbtack.com`, `mail.thumbtack.com`.
  - Angi/HomeAdvisor: `angi.com`, `homeadvisor.com`, `leads.angi.com`, `email.angi.com`.
  - Stored as a `PLATFORM_SENDERS = {"thumbtack": [...], "angi": [...]}` dict (env-overridable for tests).
- **`_gmail_service(account_email)`** — build a Gmail API client from that account's stored token (reuse `email-pipeline/gmail_auth.py` helpers; look up `token_path` from `email_accounts`). Raises a clear error if the token is missing/expired (caller logs + skips that account).
- **`_gmail_search(account_email, sender_query, since_epoch)`** boundary — returns a list of message dicts `{gmail_id, from_addr, subject, received_at, body}` for the sender query newer than `since_epoch`. Monkeypatched in tests.
- **`_parse_email(platform, msg)`** boundary — local Ollama call. Returns one of:
  - `{"kind": "lead", "customer_name", "service_type", "location", "zip", "budget", "details", "contact_phone", "contact_email"}`
  - `{"kind": "review", "reviewer_name", "rating", "review_text", "review_date", "source_url"}`
  - `{"kind": "other"}` (ignored)
  Prompt pins JSON output; a parse/JSON failure → caller marks the row `parse_failed` and stores raw for retry (never crashes the run). Monkeypatched in tests.
- **`sync(accounts=None, since=None)`** orchestrator — for each account: build service, search each platform's senders, and for each message not already stored (dedup on `(platform, gmail_id)`): parse → upsert into `ahb_leads` or `ahb_reviews`. Per-account and per-message failures are caught, logged, and skipped. Returns `{"leads_new": n, "reviews_new": m, "errors": [...]}`. Idempotent.
- **Cursor:** persist last-synced epoch per account in a tiny `lead_intake_state` table (or reuse a settings row) so timer runs are incremental; a `full=True` flag ignores the cursor for backfill.

### 4.2 New tables (`_ensure_tables`, created idempotently)
```
ahb_leads(
  id INTEGER PK, platform TEXT, platform_lead_id TEXT, customer_name TEXT,
  service_type TEXT, location TEXT, zip TEXT, budget TEXT, details TEXT,
  contact_phone TEXT, contact_email TEXT,
  status TEXT DEFAULT 'new',            -- new|contacted|quoted|won|lost
  draft_reply TEXT, gmail_id TEXT, account_email TEXT, received_at TEXT,
  converted_client_id INTEGER, converted_project_id INTEGER, notes TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
)
UNIQUE(platform, gmail_id)

ahb_reviews(
  id INTEGER PK, platform TEXT, reviewer_name TEXT, rating REAL, review_text TEXT,
  review_date TEXT, source_url TEXT, responded INTEGER DEFAULT 0,
  flagged_low INTEGER DEFAULT 0, gmail_id TEXT, account_email TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
UNIQUE(platform, gmail_id)

lead_intake_state(account_email TEXT PRIMARY KEY, last_synced_epoch INTEGER)
```
`flagged_low` set when `rating` ≤ 3. External reviews stay separate from the first-party reviews table; the Reviews tab merges both at display time.

### 4.3 Routes — `lead_bp` blueprint (registered in `dashboard/app.py`)
- `POST /api/ahb/leads/sync` — body `{accounts?: [...], full?: bool}`; runs `sync`; returns counts. Outward-facing only in that it reads Gmail; no confirm needed (read-only intake).
- `GET /api/ahb/leads?status=` — list (newest first), optional status filter.
- `GET /api/ahb/leads/<id>` — detail.
- `PATCH /api/ahb/leads/<id>` — update `status` / `notes` (whitelisted fields).
- `POST /api/ahb/leads/<id>/draft` — generate a local-LLM reply in AHB123 voice (brand/context from `ahb_business_profile`); store in `draft_reply`, return it. **Drafts only — never auto-sends.**
- `POST /api/ahb/leads/<id>/convert` — body `{create_project?: bool}`; insert `ahb_clients` (`source=<platform>`, `status='active'`) from the lead's contact fields, optionally an `ahb_projects` row (`status='Planning'`, `acquisition_type='lead'`, `client_id` linked); set the lead's `converted_client_id`/`converted_project_id` and `status='won'`. Idempotent (re-convert returns the existing links).
- `GET /api/ahb/reviews/external?platform=` — list synced external reviews for the Reviews tab merge.

### 4.4 Automation
- `scripts/lead_intake_run.py` → calls `lead_intake.sync()`; wired to a `baza-lead-intake.timer` (~every 30 min) + `.service`. (User installs the unit; spec provides the unit text.)
- Manual **Sync now** button in the Leads tab calls `POST /leads/sync`.

### 4.5 Frontend (`dashboard/templates/ahb123.html`)
- **New `leads` tab:** sub-tab nav entry + `#tab-leads` pane. Status-filter chips (New/Contacted/Quoted/Won/Lost/All), a lead list, and a body-level detail modal (per the dashboard modal rule) showing parsed fields, a **Draft reply** button (calls `/draft`, shows text + Copy, and Email-send **only** when `contact_email` exists), status dropdown (PATCH), and **Convert to client/project**. A **Sync now** button. New JS module `AhbLeads` mirroring existing tab modules.
- **Reviews tab extension:** add external reviews to `reviews-list` via `/api/ahb/reviews/external`, each with a platform badge (Thumbtack/Angi) and rating; add a source filter (All / First-party / Thumbtack / Angi). Low-rating external reviews visually flagged.
- Template edits require `sudo systemctl restart baza-dashboard` (Jinja cache).

## 5. Error handling

- Missing/expired token for an account → that account skipped, recorded in `errors`, others proceed. (sergek729@ likely needs a re-auth; surfaced clearly, with a pointer to `scripts/gmail_auth.py`.)
- Local Ollama down or bad JSON → message marked `parse_failed`, raw retained, retried next run; sync still returns success for the rest.
- Convert when contact fields are sparse → create the client with whatever exists; never fail the conversion on missing optional fields.

## 6. Testing — `tests/test_lead_intake.py`

Monkeypatch `_gmail_search` (canned Thumbtack + Angi message fixtures, incl. one lead + one review + one `other`) and `_parse_email` (canned structured returns; one that raises to exercise `parse_failed`). Cases:
- lead message → `ahb_leads` row with parsed fields; review message → `ahb_reviews` row; `other` ignored.
- dedup: running `sync` twice does not double-insert (same `(platform, gmail_id)`).
- `rating <= 3` sets `flagged_low`.
- `GET /api/ahb/leads` + status filter; `PATCH` status; `GET /api/ahb/leads/<id>`.
- `POST /leads/<id>/draft` (monkeypatch the LLM boundary) stores + returns draft.
- `POST /leads/<id>/convert` creates an `ahb_clients` row (and `ahb_projects` when `create_project`), links them, sets lead `status='won'`; re-convert is idempotent.
- `GET /api/ahb/reviews/external` lists synced reviews; parse-failure path leaves a retriable record and doesn't crash the run.
No network, no LLM, isolated tmp DB (mirror the `env` fixture style from `tests/test_social_connect.py`).

## 7. Forward-looking — partner API (B5, separate spec)

Define a `LeadSource` protocol (`fetch_leads(since)`, `fetch_reviews(since)`) so the email-parse source and a future Thumbtack Pro API / Angi partner-API source are interchangeable behind `sync`. Implementation deferred until access is granted; I will prepare the Thumbtack/Angi partner-application checklist as a separate deliverable. Email-parse keeps working regardless.

## 8. Decisions (override on spec review)

- v1 **drafts** replies (local LLM) for Serge to send; **never auto-sends**. Confirmed.
- **New tables** `ahb_leads` / `ahb_reviews` (not reusing `ahb_clients`) so lead-pipeline and external-review fields stay clean; conversion bridges into the existing CRM. Confirmed.
- External reviews **extend the existing Reviews tab**; leads get a **new dedicated tab**. Confirmed.
- Intake queries Gmail directly across **contactahbco@** + **sergek729@** by sender domain. Confirmed.

## 9. Constraints honored

- Local-first: local Ollama for all parsing/drafting; Gmail read via existing OAuth; no new cloud API.
- Dashboard modal rule: lead detail is a body-level modal.
- Template cache: restart `baza-dashboard` after editing `ahb123.html`.
- Auto-git: this spec is committed by the hourly `claw-auto-git` timer, not manually.
