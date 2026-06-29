# Lead-Platform Partner API — Application Checklists

**Date:** 2026-06-29
**Owner:** Serge (the application steps need a logged-in pro account + business identity; I can't submit these for you).
**Context:** Track B (Thumbtack/Angi lead+review intake) ships today as **email-parse** (local LLM over Gmail notifications). This folder is the **Phase 2 / partner-API** path noted in the Track B spec §7 — official two-way access that would replace the email-parse source behind the same `LeadSource` adapter seam if/when granted.

> **You do not need these to use Track B today.** Email-parse already pulls leads + reviews from your inbox. Pursue partner APIs only if you want real-time, structured, two-way access. Email-parse keeps working regardless.

## The one big finding

Neither platform has a self-serve "single contractor gets an API key" tier. Both are built for **software/CRM partners** integrating on behalf of many pros, and both deliver leads by **webhook push** (they POST to *your* endpoint), not by you polling them. Two realistic routes:

| Route | What it means for AHB123 | Effort | Verdict |
|-------|--------------------------|--------|---------|
| **A. Direct partner access** | Apply to each platform; stand up a webhook receiver on baza (it already serves HTTPS). Get structured leads (+ Thumbtack reviews) pushed straight into Baza. | Medium — applications + a small webhook-receiver build | **Recommended to attempt** — baza *is* the endpoint, so the usual "you need a CRM" blocker doesn't apply to us. |
| **B. CRM-passthrough** | Adopt Jobber / Housecall Pro / ServiceTitan / Workiz purely as a lead conduit, then pull from that CRM's API. | High — a whole second SaaS + its own integration | Avoid unless an application is denied. We'd be paying for a CRM we don't otherwise need. |

**Recommendation:** attempt **Route A** for both (low downside — worst case they say no and email-parse continues). Start with **Angi** (simpler: one email to `crmintegrations@angi.com`), then **Thumbtack** (a partner application).

## Honest expectations (don't be surprised)

- **Angi = leads only.** There is **no official Angi/HomeAdvisor reviews or profile API** — Angi review intake stays **email-parse forever** (or a review-management vendor). Don't wait on an Angi reviews API; it doesn't exist.
- **Thumbtack = leads + messages + reviews**, all webhook-pushable — the richer integration, but the harder approval (partner relationship, not a form).
- **Approval likelihood/timeline for a single small business is unpublished** for both. Treat both as "apply and see," not guaranteed.
- **Do not scrape** either platform's logged-in lead/review data — it violates their ToS. The sanctioned channels are the ones in these checklists; the unsanctioned fallback that's always safe is email-parse (Track B).

## How this plugs into the code (the `LeadSource` seam)

Track B's `dashboard/lead_intake.py` `sync()` currently has one implicit source: email-parse (`_gmail_search` → `_parse_email`). The partner-API path is a **second source** behind the same `_upsert_lead`/`_upsert_review` writers, so the Leads tab / Reviews tab don't change. Key design implication from the research: **both platforms PUSH via webhook**, so the API source is **not a poll inside `sync()`** — it's an inbound endpoint:

- A new authenticated receiver, e.g. `POST /api/ahb/leads/webhook/<platform>` (verify a shared secret / `X-API-KEY`), that maps the platform's JSON payload → the same `_upsert_lead` / `_upsert_review` calls (dedup on a platform lead id instead of `gmail_id`).
- baza must be reachable at a stable public HTTPS URL for the webhook (Caddy/Tailscale Serve already front the dashboard — see `nova.ahb123.com` / DDNS notes in the empire map; the dynamic-IP drift issue is the thing to nail down first).

That receiver is a **small, separate spec+plan** to build *after* an application is approved and you have the real payload schema in hand — don't build it speculatively against a guessed schema.

## Files
- [`thumbtack-partner-platform-checklist.md`](thumbtack-partner-platform-checklist.md)
- [`angi-leads-integration-checklist.md`](angi-leads-integration-checklist.md)

*Sources were gathered via web research on 2026-06-29 and cited inline in each checklist. Items I could not verify are marked **(unverified)** — confirm them with the platform during your application; don't assume.*
