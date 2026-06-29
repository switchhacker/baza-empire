# Angi Leads (HomeAdvisor) Integration — Application Checklist

**Goal:** Get AHB123's Angi **leads** delivered to baza as a real-time JSON webhook feed, to replace/augment the email-parse lead source behind Track B's `LeadSource` seam.

**Program:** **Angi Lead Integration API** (a.k.a. the Angi Leads CRM/lead-feed integration). HomeAdvisor Pro is now **Angi Leads** (Angie's List + HomeAdvisor merged → Angi in 2021; Angi spun off from IAC in April 2025). There is **no public developer portal** — setup is handled by Angi's integrations team over email; the field spec is a private attachment. — *Sources: en.wikipedia.org/wiki/Angi; intercom.help/angi (API integration setup).*

**Reality check — read this first:**
- **Leads only. There is NO official Angi reviews API and no profile API.** Angi review intake **stays on email-parse** (Track B) — do not wait on an Angi reviews API; it doesn't exist. Programmatic Angi reviews otherwise means scrapers (ToS risk) or a review vendor (Birdeye/Podium). — *Sources: zembratech, wextractor, apify (all third-party scrapers, not Angi-sanctioned).*
- This is the **simpler** of the two to apply for — essentially **one email** to Angi with a baza endpoint URL. Good first one to do.

---

## What Angi gives you

- [ ] **Leads** — customer lead records pushed to your endpoint as **JSON POST** (webhook). Two lead types on some paths: standard match leads + optional instant bookings. *(intercom.help/angi; help.servicetitan.com Angi Leads)*
- [ ] **Fields are fixed** — *"we are unable to customize the information we send"*; your endpoint must accept Angi's schema as-is. *(intercom.help/angi)*
- [ ] **No** reviews, **no** profile management, **no** two-way messaging via this integration.

## Before you apply — gather these

- [ ] An **active Angi Leads (Angi for Pros) account** for All Home Building.
- [ ] Your **SPID / Service Provider ID** (a.k.a. Company ID) — shown on your Angi agreement/bill and in your Angi-for-Pros URL — and your **Angi Leads account number**. *(intercom.help/angi; docs.usehatchapp.com)*
- [ ] The **business email** to associate with the feed.
- [ ] A **public HTTPS endpoint URL** on baza that accepts the JSON feed — **two of them: a testing endpoint and a production endpoint.** *(intercom.help/angi; corroborated by a contractor who received Angi's "NEW JSON Lead Feed" doc requesting test + prod URLs — community.hubspot.com.)* **(Prerequisite — see below.)**
- [ ] An **`X-API-KEY`** value your endpoint will require, and confirmation your endpoint returns a **JSON response containing `"success"`** to acknowledge each lead (Angi's documented ack contract). *(intercom.help/angi)*

## Apply (pick the direct route — it fits baza)

- [ ] **Direct lead-feed setup (recommended for us):** email **`crmintegrations@angi.com`** with your **endpoint URL(s) + Angi Leads account number / SPID + associated email**, and ask them to enable the **JSON Lead Feed**. Request the **field-definition document** (the JSON schema attachment) so we can build the receiver to match. *(intercom.help/angi)*
- [ ] Ask explicitly: *"Can a single business receive its own lead feed directly to our own HTTPS endpoint (we run our own system), or must this go through a partner CRM?"* — the docs imply CRM-mediated, but the direct email path exists; confirm it's available to you. **(approval likelihood for a lone business is unverified — ask.)**
- [ ] **Fallback if they require a CRM:** the native CRM integrations are **ServiceTitan** (`marketplace.servicetitan.com/partner/angi`), **Jobber**, **Workiz**, and **Hatch** (enter SPID). Only go here if the direct route is refused — it means adopting a CRM we don't otherwise need. *(help.servicetitan.com; help.getjobber.com; workiz.com; docs.usehatchapp.com)*

## Technical integration (post-approval)

*Build only after you have the real field-definition doc from Angi.*

- [ ] **Delivery = JSON POST (webhook) Angi → baza.** Auth via **`X-API-KEY` header**; endpoint must reply with JSON containing `"success"`. Test + production endpoints supported. *(intercom.help/angi; community.hubspot.com)*
- [ ] Baza receiver: `POST /api/ahb/leads/webhook/angi` — verify the `X-API-KEY`, map Angi's JSON → `lead_intake._upsert_lead` (dedup on Angi's lead id), return `{"success": true}`.
- [ ] No OAuth on this path (OAuth only appears on the ServiceTitan "Sign in with Angi" flow). No documented rate limits; the "testing endpoint" is the closest thing to a sandbox. *(intercom.help/angi; help.servicetitan.com)*

## Stand up the endpoint (prerequisite, can start now)

- [ ] Confirm baza is reachable at a **stable public HTTPS URL** and **resolve the dynamic-IP / DDNS drift first** (see `project_nova_caddy_dynamic_ip`). Angi needs both a **test** and a **prod** URL that don't move.
- [ ] Generate a strong `X-API-KEY` secret to give Angi (store 0600, like Track A tokens).

## When approved — hand back to me

- [ ] Give me Angi's **field-definition doc** (the JSON lead schema) + the test/prod arrangement. I'll write a small spec+plan for the `POST /api/ahb/leads/webhook/angi` receiver behind the `LeadSource` seam (returning the `"success"` ack), so the Leads tab is unchanged.

---

### Confidence notes
- **Verified:** rebrand status; leads-only scope; **no official reviews/profile API**; delivery = JSON POST with `X-API-KEY` + `"success"` ack; test+prod endpoint + SPID/account-number/email requirements; `crmintegrations@angi.com` entry point; native CRM list (ServiceTitan/Jobber/Workiz/Hatch); no-field-customization constraint.
- **Unverified (confirm with Angi):** approval likelihood/timeline for a lone small business on the direct path; the full JSON field schema (private attachment); whether a true sandbox exists beyond the "testing endpoint"; contents of the `postman.com/angiteam` workspace.
