# Application email draft — Thumbtack Partner Platform

**Primary path:** click **"Request Access"** at <https://developers.thumbtack.com/> and paste the body below into the form / follow-up.
**Email fallback / parallel:** thumbtack's partnerships team — **teampartnerships@thumbtack.com** *(confirm this address is current; it came from research, not Thumbtack's own page).*
**Send from:** your business address (contactahbco@gmail.com or serge@ahb123.com)
**Subject:** Partner Platform access — single-business first-party integration (All Home Building Co LLC)

**Before you send — fill these in:**
- `[your name / title / phone]`.
- `[Thumbtack Pro account email]` — the login for your AHB Thumbtack Pro account.
- `[OAuth redirect URL]` and `[webhook endpoint URL]` — both need baza's stable public HTTPS URL (the DDNS/Caddy fix). If not ready, keep the "we can provide these once approved to proceed" wording.

---

Hello Thumbtack Partnerships team,

I'd like to request access to the **Thumbtack Partner Platform** to integrate **our own** Thumbtack leads, messages, and reviews into the in-house system we use to run our business.

**About us**
- Business: All Home Building Co LLC — a home building & remodeling company (ahb123.com)
- Thumbtack Pro account: [Thumbtack Pro account email]
- Contact: [your name], [title], [phone] · contactahbco@gmail.com

**Use case (important — please read):**
We are **both the pro and the integrator**. We run our own operations platform that manages our leads, quotes, messaging, and customer follow-up, and we want our Thumbtack leads/messages/reviews to flow into it in real time so we can respond faster. This is **single-business, first-party use** — we are not reselling leads and not building a multi-tenant product for other pros.

I understand the Partner Platform is oriented toward software/supply partners. **My main question:** is there a path for a single first-party business like ours to get Partner Platform credentials, or is access limited to multi-pro software partners? If the latter, what would you recommend for a pro who wants programmatic access to their own data?

**On our side we're ready for the technical setup:**
- OAuth 2.0 (Authorization Code + Client Credentials), with an OAuth **redirect URL** we host.
- **Webhook** delivery for leads, messages, and reviews to an HTTPS endpoint we host.
- Separate **test and production** clients.

URLs:
- OAuth redirect: [OAuth redirect URL]
- Webhook endpoint: [webhook endpoint URL]
*(or: "We can provide the redirect and webhook URLs as soon as you confirm we're approved to proceed.")*

If you can share the eligibility path, expected timeline, and any rate limits for our scenario, that would help us plan. Happy to hop on a call.

Thank you,
[your name]
All Home Building Co LLC
ahb123.com · contactahbco@gmail.com · [phone]

---

*Notes for context (do not send): Thumbtack Partner Platform uses OAuth 2.0 (auth.thumbtack.com), webhook delivery for leads/messages/reviews, and issues test+prod clients with a required redirect URL; access is approval-gated and positioned for software partners (no documented single-business self-serve tier — hence the direct question). Thumbtack DOES expose reviews (unlike Angi). Source: developers.thumbtack.com/docs + /getting-started/authentication.*
