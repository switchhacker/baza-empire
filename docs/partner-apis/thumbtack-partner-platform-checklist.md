# Thumbtack Partner Platform — Application Checklist

**Goal:** Get approved for programmatic, real-time access to AHB123's Thumbtack **leads, messages, and reviews** (pushed to baza via webhook), to replace/augment the email-parse source behind Track B's `LeadSource` seam.

**Program:** Thumbtack Partner Platform (formerly "Thumbtack Pro API"). Public docs at <https://developers.thumbtack.com/>; access is **approval-gated** (not self-serve). — *Source: developers.thumbtack.com; press.thumbtack.com (2021-04-21 Pro API launch).*

**Reality check:** The platform is positioned for **software/supply partners** building for many pros; there is **no documented single-business self-serve tier**. We apply anyway because **baza can be the integration endpoint** — we don't need a third-party CRM. Worst case: declined, and email-parse continues. — *Source: developers.thumbtack.com/docs.*

---

## What Thumbtack gives you (so you know what you're applying for)

- [ ] **Leads** — receive/manage incoming homeowner leads in real time. *(developers.thumbtack.com/guides — Leads)*
- [ ] **Messages** — two-way messaging with customers. *(developers.thumbtack.com/docs/messages/implementation)*
- [ ] **Reviews** — review data pushed to your endpoint (businessID, categoryID, rating, reviewID, reviewer nickname, text, verified, photos). *(developers.thumbtack.com/docs — Reviews)*
- [ ] **Businesses/Profiles** — profile + business-phone operations. *(developers.thumbtack.com/docs/businesses)*
- [ ] Delivery is by **webhook** (Thumbtack POSTs to you); webhooks can also be managed in the Thumbtack UI directly. *(developers.thumbtack.com/guides)*

> Unlike Angi, Thumbtack **does** expose reviews — so a successful Thumbtack integration can move Thumbtack review intake off email-parse and onto the live feed.

## Before you apply — gather these

- [ ] An **active Thumbtack Pro account** for All Home Building (login confirmed).
- [ ] **Business identity:** legal name (All Home Building Co LLC), DBA, EIN, business address, business email, phone — all already in `ahb_business_profile`.
- [ ] A one-paragraph **use-case description**. Suggested framing: *"All Home Building Co LLC operates an in-house operations platform (Baza) that manages our leads, quotes, and customer communications. We want to integrate our own Thumbtack leads, messages, and reviews into our internal system in real time to respond faster. We are the pro and the integrator — single-business, first-party use."* Be explicit that you're **not** reselling leads or building a multi-tenant product — set expectations honestly, since the program is partner-oriented.
- [ ] A **public HTTPS endpoint URL** on baza to receive webhooks + the OAuth **redirect URL**. **(Prerequisite — see "Stand up the endpoint" below.)** Thumbtack requires a redirect URL when issuing client credentials. *(developers.thumbtack.com/docs/getting-started/authentication)*
- [ ] Decision on the **two environments** Thumbtack issues: **test** and **production** OAuth clients — you'll request both. *(same source)*

## Apply

- [ ] Go to <https://developers.thumbtack.com/> and click **"Request Access."** *(developers.thumbtack.com)*
- [ ] In parallel / if no response, email **`teampartnerships@thumbtack.com`** (the partnerships team that approves access) with the use-case paragraph + business identity. **(contact unverified beyond research corroboration — confirm it's current when you send.)**
- [ ] Expect a **human approval step** and an assigned **Account Manager** who provisions credentials — this is a relationship, not an instant form. Ask them directly: *"Is there a path for a single first-party business, or only for multi-pro software partners?"* Get that answer early so you don't wait on a dead end.
- [ ] **(unverified)** Ask the Account Manager for: the exact application/form fields, any security/compliance attestations, volume expectations, and **published rate limits** — none of these were findable publicly, so confirm them directly.

## Technical integration (what the build will look like, post-approval)

*Confirm all of this against the docs they unlock — don't build before you have real credentials + the real payload schema.*

- [ ] **Auth: OAuth 2.0.** Two flows: **Authorization Code** (act on the pro's behalf — you authorize once, get redirected back) and **Client Credentials** (app-to-app). *(developers.thumbtack.com/docs/getting-started/authentication)*
  - Auth endpoint `https://auth.thumbtack.com/oauth2/auth`, token `https://auth.thumbtack.com/oauth2/token`.
  - You'll receive a **Client ID + Client Secret**. Store on disk 0600 (mirror Track A's `social-pipeline/` token handling — never in the DB).
  - **Access token TTL = 1 hour**; **refresh token = 180 days, single-use** with a 60-second grace window (each refresh reissues a new refresh token). Build refresh-on-expiry like `lead_intake._gmail_service`'s refresh pattern.
  - `state` parameter required, ≥8 chars (CSRF). Scopes are space-delimited, per-endpoint.
- [ ] **Webhooks:** Thumbtack POSTs new leads/messages/reviews to your endpoint. The Baza receiver: `POST /api/ahb/leads/webhook/thumbtack`, verify the request, map payload → `lead_intake._upsert_lead` / `_upsert_review` (dedup on Thumbtack's lead/review id, not `gmail_id`).
- [ ] **Test first:** build against the **test client/credentials**, then flip to production. *(developers.thumbtack.com/docs/getting-started/authentication)*

## Stand up the endpoint (prerequisite, can start now)

- [ ] Confirm baza is reachable at a **stable public HTTPS URL** (Caddy / Tailscale Serve already front the dashboard). **Resolve the residential dynamic-IP / DDNS drift first** (see `project_nova_caddy_dynamic_ip` in memory) — a webhook needs a URL that doesn't move.
- [ ] That URL is what you give Thumbtack as the redirect + webhook target.

## When approved — hand back to me

- [ ] Give me the **real payload schema** (a sample lead, message, and review JSON) + the granted scopes. I'll write a small spec+plan for the `POST /api/ahb/leads/webhook/thumbtack` receiver + the OAuth client, slotting it behind the existing `LeadSource` seam so the Leads/Reviews tabs don't change.

---

### Confidence notes
- **Verified:** program name + portal, OAuth mechanics (flows, endpoints, token TTLs, redirect/state/scopes), Leads/Messages/Reviews/Businesses capabilities, webhook delivery, test+prod environments, Request-Access entry point, partner-oriented positioning.
- **Unverified (confirm during application):** the `teampartnerships@thumbtack.com` address being current, exact application-form fields, security/compliance attestations, volume requirements, numeric rate limits, approval timeline/likelihood for a single business.
